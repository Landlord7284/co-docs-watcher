"""The full flow against the wire-accurate fake server, through the CLI.

Everything the invariants promise, exercised end to end: whole-window sweeps, idempotence on
``(document_id, version)``, atomic placement, the state machine, one frontier for purge and
discovery, rewrite-never-invent inboxes, and the exit-code contract. Most scenarios drive the
CLI in-process — same argv surface, same code — and the handful that pin the *process*
contract (exit codes across ``exec``, ``$CO_WATCHER_CONFIG``) run as real subprocesses.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from co_docs_watcher import cli
from co_docs_watcher.manifest.db import open_manifest
from co_docs_watcher.manifest.repo import Manifest
from co_docs_watcher.models import LocalState
from tests.conftest import CLOCK
from tests.fca import build_package
from tests.radserver import FakeRad, ServedDocument
from tests.test_models import make_document

#: The suite runs against the real wall clock, read the way the watcher reads it.
TODAY = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
YESTERDAY = TODAY - timedelta(days=1)

WATCH_LIST = """\
companies:
  - cvm_code: '009512'
    prefix: PETR
    prefix_source: ticker
    matched_by: ticker
    legal_name: PETROLEO BRASILEIRO S.A. PETROBRAS
"""


@dataclass
class Site:
    """One installation: config file, the two roots, and the server it points at."""

    config: Path
    data_root: Path
    documents_root: Path
    logs_root: Path
    server: FakeRad

    def cli(self, *args: str) -> int:
        return cli.main(["--config", str(self.config), *args])

    def write_config(self, *, retention_days: int = 7) -> None:
        self.config.write_text(
            "[paths]\n"
            f'data_root = "{self.data_root}"\n'
            f'documents_root = "{self.documents_root}"\n'
            f'logs_root = "{self.logs_root}"\n'
            "[retention]\n"
            f"days = {retention_days}\n"
            "[source]\n"
            f'base_url = "{self.server.base_url}"\n'
            "min_request_interval = 0.001\n",
            encoding="utf-8",
        )

    def day_dir(self, day: date) -> Path:
        return self.documents_root / day.isoformat()

    def inbox(self, day: date) -> Path:
        return self.documents_root / "_inbox" / f"{day.isoformat()}.md"

    def manifest(self) -> Manifest:
        return Manifest.over(open_manifest(self.data_root / "manifest.sqlite"), CLOCK)

    def archive_snapshot(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.documents_root))
            for path in self.documents_root.rglob("*")
            if path.is_file()
        )


@pytest.fixture
def server() -> FakeRad:
    server = FakeRad().start()
    yield server
    server.stop()


@pytest.fixture
def site(tmp_path: Path, server: FakeRad) -> Site:
    data_root = tmp_path / "data"
    cache = data_root / "cvm-cache"
    cache.mkdir(parents=True)
    for year in (TODAY.year - 1, TODAY.year):
        (cache / f"fca_cia_aberta_{year}.zip").write_bytes(build_package(year=year))
    (data_root / "companies.yaml").write_text(WATCH_LIST, encoding="utf-8")
    site = Site(
        config=tmp_path / "config.toml",
        data_root=data_root,
        documents_root=tmp_path / "documents",
        logs_root=tmp_path / "logs",
        server=server,
    )
    site.write_config()
    return site


def pdf_today() -> ServedDocument:
    return ServedDocument(document_id=101, delivery=TODAY, subject="Fato de hoje")


def itr_today() -> ServedDocument:
    """Documents are mutable scenario state, so every test gets instances of its own."""
    return ServedDocument(
        document_id=102,
        delivery=TODAY,
        category="ITR - Informações Trimestrais",
        subject="",
        kind="zip",
    )


def test_first_run_on_an_empty_archive(site: Site) -> None:
    site.server.scenario.documents += [pdf_today(), itr_today()]

    assert site.cli("run") == 0

    company = site.day_dir(TODAY) / "PETR"
    pdf = company / "Fato-Relevante_101_V01.pdf"
    assert pdf.read_bytes().startswith(b"%PDF-")
    # The structured delivery: category subfolder, imposed name on the generated copy,
    # origin names on the stable members.
    assert (company / "ITR" / "ITR_102_V01.pdf").is_file()
    assert (company / "ITR" / "009512ITR30-06-2026v1.xml").is_file()
    assert not (site.documents_root / ".tmp").exists() or not list(
        (site.documents_root / ".tmp").iterdir()
    )

    index = site.inbox(TODAY).read_text(encoding="utf-8")
    assert "Fato de hoje" in index
    assert "Fato-Relevante_101_V01.pdf" in index
    # The whole window was swept, one request per day, most recent day first — so a run cut
    # short has spent what it had on the days a reader opens first.
    assert len(site.server.listing_requests) == 7
    assert site.server.listing_requests == sorted(site.server.listing_requests, reverse=True)
    assert site.server.listing_requests[0] == TODAY


def test_a_second_run_with_nothing_new_is_fully_idempotent(site: Site) -> None:
    site.server.scenario.documents += [pdf_today(), itr_today()]
    assert site.cli("run") == 0
    downloads = list(site.server.download_requests)
    index = site.inbox(TODAY).read_bytes()
    snapshot = site.archive_snapshot()

    assert site.cli("run") == 0

    # No re-download — identity is (document_id, version), and a structured package would
    # even hash differently if it were fetched again.
    assert site.server.download_requests == downloads
    assert site.inbox(TODAY).read_bytes() == index
    assert site.archive_snapshot() == snapshot


def test_a_monitor_run_sweeps_two_days_and_touches_no_other(site: Site) -> None:
    """A document delivered inside retention but before the monitor window survives a
    monitor run — neither purged nor dropped from its day's index."""
    older_day = TODAY - timedelta(days=2)
    site.server.scenario.documents += [
        ServedDocument(document_id=107, delivery=older_day, subject="De anteontem")
    ]
    assert site.cli("run") == 0
    older_index = site.inbox(older_day).read_bytes()
    site.server.listing_requests.clear()

    site.server.scenario.documents += [pdf_today()]
    assert site.cli("run", "--monitor") == 0

    assert site.server.listing_requests == [TODAY, YESTERDAY]
    assert (site.day_dir(TODAY) / "PETR" / "Fato-Relevante_101_V01.pdf").is_file()
    assert (site.day_dir(older_day) / "PETR" / "Fato-Relevante_107_V01.pdf").is_file()
    assert site.inbox(older_day).read_bytes() == older_index
    assert site.manifest().documents.require((107, 1)).local_state is LocalState.AVAILABLE


def test_a_resubmission_replaces_the_file_and_keeps_the_row(site: Site) -> None:
    original = ServedDocument(document_id=103, delivery=TODAY, subject="Primeira entrega")
    site.server.scenario.documents.append(original)
    assert site.cli("run") == 0
    old_file = site.day_dir(TODAY) / "PETR" / "Fato-Relevante_103_V01.pdf"
    assert old_file.is_file()

    # The source supersedes by issuing a *new* document_id and demoting the old one.
    original.status = "Inativo"
    site.server.scenario.documents.append(
        ServedDocument(document_id=104, delivery=TODAY, subject="Segunda entrega")
    )
    assert site.cli("run") == 0

    assert not old_file.exists()
    assert (site.day_dir(TODAY) / "PETR" / "Fato-Relevante_104_V01.pdf").is_file()
    manifest = site.manifest()
    assert manifest.documents.require((103, 1)).local_state is LocalState.DEACTIVATED
    assert manifest.documents.require((104, 1)).local_state is LocalState.AVAILABLE
    index = site.inbox(TODAY).read_text(encoding="utf-8")
    assert "Fato-Relevante_104_V01.pdf" in index
    assert "Fato-Relevante_103_V01.pdf" not in index


def test_a_cancellation_removes_the_file_and_the_inbox_says_so(site: Site) -> None:
    document = ServedDocument(document_id=105, delivery=TODAY, subject="Cancelado depois")
    site.server.scenario.documents.append(document)
    assert site.cli("run") == 0
    archived = site.day_dir(TODAY) / "PETR" / "Fato-Relevante_105_V01.pdf"
    assert archived.is_file()

    document.status = "Cancelado"
    assert site.cli("run") == 0

    assert not archived.exists()
    assert site.manifest().documents.require((105, 1)).local_state is LocalState.CANCELLED
    index = site.inbox(TODAY).read_text(encoding="utf-8")
    assert "Cancelado depois" in index and "cancelled at the source" in index


def test_the_window_sliding_past_a_day_purges_it_everywhere(site: Site) -> None:
    site.server.scenario.documents += [
        pdf_today(),
        ServedDocument(document_id=106, delivery=YESTERDAY, subject="De ontem"),
    ]
    assert site.cli("run") == 0
    assert site.day_dir(YESTERDAY).is_dir()
    assert site.inbox(YESTERDAY).is_file()

    # The frontier moves because retention shrank; the same code path a passing day takes.
    site.write_config(retention_days=1)
    assert site.cli("run") == 0

    assert not site.day_dir(YESTERDAY).exists()
    assert not site.inbox(YESTERDAY).exists()
    assert site.day_dir(TODAY).is_dir()
    assert site.manifest().documents.require((106, 1)).local_state is LocalState.PURGED


def test_an_interrupted_fetch_is_reconciled_on_the_next_start(site: Site) -> None:
    site.server.scenario.documents.append(pdf_today())
    assert site.cli("run") == 0

    # A run that died mid-flight: the manifest says downloading, staging holds debris, and
    # the archive holds nothing for the document.
    interrupted = ServedDocument(document_id=107, delivery=TODAY, subject="Interrompido")
    site.server.scenario.documents.append(interrupted)
    manifest = site.manifest()
    manifest.documents.upsert_observed(
        make_document(document_id=107, delivery_date=TODAY, subject="Interrompido")
    )
    manifest.documents.transition((107, 1), LocalState.DOWNLOADING)
    staging = site.documents_root / ".tmp" / "107-v1"
    staging.mkdir(parents=True)
    (staging / "document.pdf").write_bytes(b"half a download")

    assert site.cli("reconcile") == 0
    assert site.manifest().documents.require((107, 1)).local_state is LocalState.DISCOVERED
    assert not list((site.documents_root / ".tmp").iterdir())

    assert site.cli("run") == 0
    assert (site.day_dir(TODAY) / "PETR" / "Fato-Relevante_107_V01.pdf").is_file()


def test_a_captcha_demand_exits_4_and_corrupts_nothing(site: Site) -> None:
    site.server.scenario.documents.append(pdf_today())
    assert site.cli("run") == 0
    snapshot = site.archive_snapshot()

    site.server.scenario.captcha = True
    assert site.cli("run") == 4

    assert site.archive_snapshot() == snapshot
    assert site.manifest().documents.require((101, 1)).local_state is LocalState.AVAILABLE
    site.server.scenario.captcha = False
    assert site.cli("run") == 0


def test_a_backend_that_stays_down_backs_off_and_exits_1(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    site.server.scenario.documents.append(pdf_today())
    assert site.cli("run") == 0
    snapshot = site.archive_snapshot()

    site.server.scenario.failing_listings = 10**9
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    assert site.cli("run") == 1

    # It backed off between attempts instead of hammering, and gave the batch back intact.
    assert [pause for pause in slept if pause >= 15] == [15.0, 60.0, 240.0]
    assert site.archive_snapshot() == snapshot

    site.server.scenario.failing_listings = 0
    assert site.cli("run") == 0


# --- The process contract: real subprocesses, real exit codes. ---


def run_cli(site: Site, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "co_docs_watcher", "--config", str(site.config), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_doctor_on_a_valid_and_an_invalid_config(site: Site, tmp_path: Path) -> None:
    healthy = run_cli(site, "doctor")
    assert healthy.returncode == 0, healthy.stderr
    assert "source: answered" in healthy.stdout

    broken = tmp_path / "broken.toml"
    broken.write_text('[paths]\ndata_root = "var/data"\n', encoding="utf-8")
    sick = subprocess.run(
        [sys.executable, "-m", "co_docs_watcher", "--config", str(broken), "doctor"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert sick.returncode == 2
    assert "documents_root is required" in sick.stderr


def test_add_list_rm_round_trip_as_a_subprocess(site: Site) -> None:
    added = run_cli(site, "add", "--ticker", "VALE3")
    assert added.returncode == 0, added.stderr
    assert "VALE S.A." in added.stdout

    listed = run_cli(site, "list")
    assert listed.returncode == 0
    assert "VALE" in listed.stdout and "PETR" in listed.stdout

    removed = run_cli(site, "rm", "VALE")
    assert removed.returncode == 0
    assert "VALE" not in run_cli(site, "list").stdout


def test_run_against_the_fake_server_as_a_subprocess(site: Site) -> None:
    site.server.scenario.documents.append(pdf_today())
    finished = run_cli(site, "run")
    assert finished.returncode == 0, finished.stderr
    assert (site.day_dir(TODAY) / "PETR" / "Fato-Relevante_101_V01.pdf").is_file()
    # The streams are what the operator watched; the file is what answers the question later.
    written = (site.logs_root / "co-docs-watcher.log").read_text(encoding="utf-8")
    assert "run: finished clean" in written
