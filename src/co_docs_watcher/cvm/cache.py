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
from contextlib import contextmanager
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
        except OSError:
            return False
        age = now.timestamp() - modified
        return age <= self._max_age_days * 86_400

    def load(self, *, now: datetime, refresh: bool = True) -> Registry:
        """Return the registry, refreshing whatever is stale.

        ``refresh=False`` is the offline path: whatever is cached is used, and only the
        absence of any cached package raises.
        """
        years = (now.year - 1, now.year)
        registries = []
        for year in years:
            payload = self._package(year, now=now, refresh=refresh)
            if payload is not None:
                registries.append(parse_package(payload))
        if not registries or not any(len(registry) for registry in registries):
            raise RegistryError(
                f"no usable FCA package for {years[0]} or {years[1]} in {self._root}; "
                "registering new companies needs one, monitoring does not"
            )
        return merge_registries(*registries)

    def _package(self, year: int, *, now: datetime, refresh: bool) -> bytes | None:
        """The bytes for one year: cached when current, downloaded when not, ``None`` when
        neither is possible."""
        path = self.path_for(year)
        if not refresh or self.is_fresh(year, now=now):
            return self._cached(path)
        try:
            payload = self._download(year)
            self._store(path, payload)
        except RegistryNotPublishedError:
            logger.warning(
                "registry: the %s package is not published yet; continuing without it", year
            )
            return self._cached(path)
        except RegistryError as exc:
            logger.warning(
                "registry: could not refresh the %s package (%s); falling back to whatever is "
                "cached in %s",
                year,
                exc,
                self._root,
            )
            return self._cached(path)
        return payload

    def _cached(self, path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except OSError:
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
        """Write the package only after it parses.

        The order is the whole point: a truncated or corrupt download that overwrote the cache
        would leave the watcher with no registry at all until the next successful refresh, and
        the failure would surface as "company not found" rather than as a download problem.
        """
        parse_package(payload)
        self._root.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(path.name + ".part")
        staging.write_bytes(payload)
        os.replace(staging, path)

    @contextmanager
    def _open_client(self) -> Iterator[httpx.Client]:
        if self._client is not None:
            yield self._client
            return
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            yield client
