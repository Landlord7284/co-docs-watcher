"""Fetching the FCA package, and the cache that keeps the network out of most runs.

The registry changes when a company files a new registration form — a few times a day across
the whole market — so re-downloading it on every run buys nothing. The cache is a directory of
yearly packages under ``data_root``; a package younger than the configured age is used as it
is, and the network is never touched.

Two years are always read: the previous one as the base, the current one on top. The yearly
package only holds companies that filed *that* year, so in February the current year alone
would be a registry of a few dozen companies — enough to make a company that exists look like
one that does not.

Failure is asymmetric on purpose. A download that fails or arrives corrupt leaves the previous
snapshot exactly where it was and the run continues on it, loudly; only the total absence of
any usable snapshot raises. What that error blocks is registration, never monitoring.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

import httpx

from co_docs_watcher.cvm.registry import Registry, merge_registries, parse_package
from co_docs_watcher.errors import RegistryError, RegistryNotPublishedError

__all__ = [
    "CACHE_DIRECTORY",
    "DEFAULT_REGISTRY_MAX_AGE_DAYS",
    "FCA_PACKAGE_URL",
    "MAX_PACKAGE_BYTES",
    "RegistryCache",
]

logger = logging.getLogger(__name__)

#: The CVM's open data portal. Public and unauthenticated: there are no credentials anywhere
#: in this system, and there is nothing here to keep secret.
FCA_PACKAGE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{year}.zip"

#: The 2026 package was 359 387 bytes on 2026-08-24. The cap is two orders of magnitude above
#: it: a response that large is a mirror gone wrong, not a registry.
MAX_PACKAGE_BYTES = 32 * 1024 * 1024

#: Where the cached packages live, under ``data_root``.
CACHE_DIRECTORY = "cvm-cache"

#: Days a cached package is considered current.
DEFAULT_REGISTRY_MAX_AGE_DAYS = 7

_TIMEOUT = httpx.Timeout(30.0)


class RegistryCache:
    """The cached FCA packages, and the policy for when to go get them again."""

    __slots__ = ("_client", "_max_age_days", "_max_bytes", "_root", "_url_template")

    def __init__(
        self,
        root: Path,
        *,
        max_age_days: int = DEFAULT_REGISTRY_MAX_AGE_DAYS,
        url_template: str = FCA_PACKAGE_URL,
        client: httpx.Client | None = None,
        max_bytes: int = MAX_PACKAGE_BYTES,
    ) -> None:
        self._root = root
        self._max_age_days = max_age_days
        self._url_template = url_template
        self._client = client
        self._max_bytes = max_bytes

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, year: int) -> Path:
        return self._root / f"fca_cia_aberta_{year}.zip"

    def is_fresh(self, year: int, *, now: datetime) -> bool:
        """Whether the cached package for ``year`` is young enough to be used untouched."""
        path = self.path_for(year)
        try:
            modified = path.stat().st_mtime
        except FileNotFoundError:
            return False
        except OSError as exc:
            # Not the same thing as an absent cache, and worth saying so: the answer is the
            # same "refresh it", but the reason is a cache directory that cannot be read.
            logger.warning("registry: the cached package at %s could not be stat'd (%s)", path, exc)
            return False
        age = now.timestamp() - modified
        return age <= self._max_age_days * 86_400

    def load(self, *, now: datetime, refresh: bool = True) -> Registry:
        """Return the registry, refreshing whatever is stale.

        ``refresh=False`` is the offline path: whatever is cached is used, and only the
        absence of any cached package raises.
        """
        years = (now.year - 1, now.year)
        registries = [
            registry
            for registry in (
                self._registry(year, now=now, refresh=refresh) for year in years
            )
            if registry is not None
        ]
        if not any(len(registry) for registry in registries):
            raise RegistryError(
                f"no usable FCA package for {years[0]} or {years[1]} in {self._root}; "
                "registering new companies needs one, monitoring does not"
            )
        return merge_registries(*registries)

    def _registry(self, year: int, *, now: datetime, refresh: bool) -> Registry | None:
        """One year's registry: cached when current, downloaded when not, ``None`` when
        neither is possible.

        A year is parsed exactly once, here, and what travels no further than this method is
        the payload — which is what lets a package that fails to parse be dropped on its own
        rather than taking the other year down with it.
        """
        path = self.path_for(year)
        if not refresh or self.is_fresh(year, now=now):
            return self._cached_registry(path, year)
        try:
            payload = self._download(year)
            registry = parse_package(payload)
        except RegistryNotPublishedError:
            logger.warning(
                "registry: the %s package is not published yet; continuing without it", year
            )
            return self._cached_registry(path, year)
        except RegistryError as exc:
            logger.warning(
                "registry: could not refresh the %s package (%s); falling back to whatever is "
                "cached in %s",
                year,
                exc,
                self._root,
            )
            return self._cached_registry(path, year)
        try:
            self._store(path, payload)
        except RegistryError as exc:
            logger.warning(
                "registry: the %s package was downloaded but could not be cached (%s); this "
                "run uses it, and the next one downloads it again",
                year,
                exc,
            )
        return registry

    def _cached_registry(self, path: Path, year: int) -> Registry | None:
        """The cached year, parsed, or ``None`` with a reason on the log.

        A cached package that no longer parses is treated exactly like one that was never
        there: it is one year of two, and the other one may well be intact. What must not
        happen is the pair failing together over a single corrupt file — that reads as "the
        registry is gone" when half of it is fine.
        """
        payload = self._cached(path)
        if payload is None:
            return None
        try:
            return parse_package(payload)
        except RegistryError as exc:
            logger.warning(
                "registry: the cached %s package at %s is unusable (%s); continuing without it",
                year,
                path,
                exc,
            )
            return None

    def _cached(self, path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("registry: the cached package at %s could not be read (%s)", path, exc)
            return None

    def _download(self, year: int) -> bytes:
        url = self._url_template.format(year=year)
        try:
            with self._open_client() as client, client.stream("GET", url) as response:
                if response.status_code == httpx.codes.NOT_FOUND:
                    raise RegistryNotPublishedError(f"{url} does not exist")
                if response.status_code >= httpx.codes.BAD_REQUEST:
                    raise RegistryError(f"{url} answered HTTP {response.status_code}")
                return self._read_capped(response, url)
        except httpx.HTTPError as exc:
            raise RegistryError(f"{url} could not be fetched: {exc}") from exc

    def _read_capped(self, response: httpx.Response, url: str) -> bytes:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self._max_bytes:
            raise RegistryError(f"{url} declares {declared} bytes, over the cap")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise RegistryError(f"{url} exceeded the {self._max_bytes} byte cap mid-download")
            chunks.append(chunk)
        return b"".join(chunks)

    def _store(self, path: Path, payload: bytes) -> None:
        """Place an already-parsed package in the cache.

        Validation happens in the caller, before anything is written: a truncated or corrupt
        download that overwrote the cache would leave the watcher with no registry at all
        until the next successful refresh, and the failure would surface as "company not
        found" rather than as a download problem.

        A cache that cannot be written is a ``RegistryError`` and not a bare ``OSError``. The
        distinction is the whole error contract of this package: ``RegistryError`` is the one
        the caller knows how to continue past, while an ``OSError`` escaping here would leave
        the CLI with an exception it does not map to any documented exit code — over a
        directory being read-only, which is a plausible way to mount ``data_root``.
        """
        staging = path.with_name(path.name + ".part")
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            staging.write_bytes(payload)
            os.replace(staging, path)
        except OSError as exc:
            with suppress(OSError):
                staging.unlink(missing_ok=True)
            raise RegistryError(
                f"the registry cache in {self._root} is not writable: {exc}"
            ) from exc

    @contextmanager
    def _open_client(self) -> Iterator[httpx.Client]:
        if self._client is not None:
            yield self._client
            return
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            yield client
