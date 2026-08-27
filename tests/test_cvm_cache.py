"""The cache decides when the network is touched, and protects what it already has."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from co_docs_watcher.cvm.cache import RegistryCache
from co_docs_watcher.errors import RegistryError
from tests import fca

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
URL = "https://registry.invalid/fca_cia_aberta_{year}.zip"

Handler = Callable[[httpx.Request], httpx.Response]


def client_answering(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def serving(**by_year: bytes | int) -> tuple[httpx.Client, list[int]]:
    """A client that serves a payload per year, and the list of years it was asked for."""
    asked: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        year = int(str(request.url).rsplit("_", 1)[-1].removesuffix(".zip"))
        asked.append(year)
        answer = by_year.get(f"y{year}")
        if answer is None:
            return httpx.Response(404)
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, content=answer)

    return client_answering(handler), asked


def cache(tmp_path: Path, client: httpx.Client, **kwargs: object) -> RegistryCache:
    return RegistryCache(tmp_path / "cvm-cache", url_template=URL, client=client, **kwargs)  # type: ignore[arg-type]


def age(path: Path, days: float) -> None:
    when = NOW.timestamp() - days * 86_400
    os.utime(path, (when, when))


def test_both_years_are_fetched_and_cached(tmp_path: Path) -> None:
    client, asked = serving(y2025=fca.build_package(year=2025), y2026=fca.build_package())
    store = cache(tmp_path, client)

    registry = store.load(now=NOW)

    assert asked == [2025, 2026]
    assert len(registry) == len(fca.GENERAL_ROWS)
    assert store.path_for(2025).is_file()
    assert store.path_for(2026).is_file()


def test_a_fresh_cache_skips_the_network_entirely(tmp_path: Path) -> None:
    client, asked = serving(y2025=fca.build_package(year=2025), y2026=fca.build_package())
    store = cache(tmp_path, client, max_age_days=7)
    store.load(now=NOW)
    asked.clear()

    store.load(now=NOW + timedelta(days=6, hours=23))

    assert asked == []


def test_a_cache_older_than_the_policy_is_refreshed(tmp_path: Path) -> None:
    client, asked = serving(y2025=fca.build_package(year=2025), y2026=fca.build_package())
    store = cache(tmp_path, client, max_age_days=7)
    store.load(now=NOW)
    age(store.path_for(2026), days=8)
    age(store.path_for(2025), days=2)
    asked.clear()

    store.load(now=NOW)

    assert asked == [2026]


def test_a_failed_download_leaves_the_previous_snapshot_in_place(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    good = fca.build_package()
    client, _ = serving(y2025=fca.build_package(year=2025), y2026=good)
    store = cache(tmp_path, client)
    store.load(now=NOW)
    age(store.path_for(2025), days=30)
    age(store.path_for(2026), days=30)

    broken, _ = serving(y2025=503, y2026=503)
    stale = cache(tmp_path, broken, max_age_days=0)
    with caplog.at_level(logging.WARNING):
        registry = stale.load(now=NOW)

    assert "could not refresh" in caplog.text
    assert store.path_for(2026).read_bytes() == good
    assert registry.by_cnpj(fca.PETROBRAS) is not None


def test_a_corrupt_download_never_replaces_a_good_snapshot(tmp_path: Path) -> None:
    good = fca.build_package()
    client, _ = serving(y2025=fca.build_package(year=2025), y2026=good)
    store = cache(tmp_path, client)
    store.load(now=NOW)
    age(store.path_for(2025), days=30)
    age(store.path_for(2026), days=30)

    truncated, _ = serving(y2025=fca.build_package(year=2025), y2026=good[: len(good) // 2])
    poisoned = cache(tmp_path, truncated, max_age_days=0)
    registry = poisoned.load(now=NOW)

    assert store.path_for(2026).read_bytes() == good
    assert list(store.path_for(2026).parent.glob("*.part")) == []
    assert registry.by_cnpj(fca.PETROBRAS) is not None


def test_a_year_that_is_not_published_yet_is_not_a_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Every January, until the first company files.
    client, asked = serving(y2025=fca.build_package(year=2025))
    store = cache(tmp_path, client)

    with caplog.at_level(logging.WARNING):
        registry = store.load(now=datetime(2026, 1, 3, tzinfo=UTC))

    assert asked == [2025, 2026]
    assert "not published yet" in caplog.text
    assert len(registry) == len(fca.GENERAL_ROWS)


def test_nothing_downloadable_and_nothing_cached_raises(tmp_path: Path) -> None:
    client, _ = serving()
    store = cache(tmp_path, client)

    with pytest.raises(RegistryError, match="no usable FCA package"):
        store.load(now=NOW)


def test_a_response_over_the_cap_is_refused(tmp_path: Path) -> None:
    oversized = fca.build_package()
    client, _ = serving(y2025=oversized, y2026=oversized)
    store = cache(tmp_path, client, max_bytes=16)

    with pytest.raises(RegistryError, match="no usable FCA package"):
        store.load(now=NOW)
    assert not store.path_for(2026).exists()


def test_a_declared_length_over_the_cap_is_refused_before_reading(tmp_path: Path) -> None:
    read = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal read
        read = True
        return httpx.Response(200, content=b"PK\x03\x04", headers={"content-length": "999999999"})

    store = cache(tmp_path, client_answering(handler), max_bytes=1024)

    with pytest.raises(RegistryError, match="no usable FCA package"):
        store.load(now=NOW)
    assert read


def test_the_offline_path_uses_whatever_is_cached(tmp_path: Path) -> None:
    client, asked = serving(y2025=fca.build_package(year=2025), y2026=fca.build_package())
    store = cache(tmp_path, client)
    store.load(now=NOW)
    age(store.path_for(2026), days=400)
    asked.clear()

    registry = store.load(now=NOW, refresh=False)

    assert asked == []
    assert registry.by_cnpj(fca.PETROBRAS) is not None


def test_an_unwritable_cache_costs_the_cache_and_never_the_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A read-only ``data_root`` is a plausible mount, and the download itself was fine: the
    # run uses what it fetched, and the failure arrives as a warning rather than as an
    # OSError the CLI has no exit code for.
    client, _ = serving(y2025=fca.build_package(year=2025), y2026=fca.build_package())
    root = tmp_path / "read-only"
    root.mkdir()
    os.chmod(root, 0o500)
    store = RegistryCache(root / "cvm-cache", url_template=URL, client=client)

    try:
        with caplog.at_level(logging.WARNING):
            registry = store.load(now=NOW)
    finally:
        os.chmod(root, 0o700)

    assert "could not be cached" in caplog.text
    assert registry.by_cnpj(fca.PETROBRAS) is not None
    assert not store.path_for(2026).exists()


def test_a_corrupt_cached_year_does_not_take_the_other_one_down(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Two years are read and only one is damaged: a pair that fails together over a single
    # unreadable file reads as "the registry is gone" when half of it is intact.
    client, _ = serving(y2025=fca.build_package(year=2025), y2026=fca.build_package())
    store = cache(tmp_path, client)
    store.load(now=NOW)
    store.path_for(2025).write_bytes(b"PK\x03\x04 and then nothing a reader can use")

    with caplog.at_level(logging.WARNING):
        registry = store.load(now=NOW, refresh=False)

    assert "is unusable" in caplog.text
    assert registry.by_cnpj(fca.PETROBRAS) is not None


def test_a_cached_package_that_cannot_be_read_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Unreadable is not the same as absent, and the difference is the whole diagnosis: the
    # bare "no usable FCA package" names the wrong cause for a permission problem.
    client, _ = serving(y2025=fca.build_package(year=2025), y2026=fca.build_package())
    store = cache(tmp_path, client)
    store.load(now=NOW)
    os.chmod(store.path_for(2026), 0o000)

    try:
        with caplog.at_level(logging.WARNING):
            registry = store.load(now=NOW, refresh=False)
    finally:
        os.chmod(store.path_for(2026), 0o600)

    assert "could not be read" in caplog.text
    assert registry.by_cnpj(fca.PETROBRAS) is not None
