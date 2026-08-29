"""The CLI surface: flag wiring, the metavar rule, dispatch, and the exit-code mapping.

Everything here runs in-process. The full flow — subprocesses, the fake server, the wire —
belongs to the integration suite; these tests pin the glue itself: that ``--config`` works in
both positions, that no flag leaks an internal name into ``--help``, and that each failure
mode reaches the operator as the documented number.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from co_docs_watcher import cli
from co_docs_watcher.clock import Clock
from co_docs_watcher.config import load_config
from co_docs_watcher.errors import ExitCode
from co_docs_watcher.lock import RunLock
from co_docs_watcher.manifest.db import open_manifest
from co_docs_watcher.manifest.repo import AttemptOutcome, Manifest
from tests import fca
from tests.fca import build_package
from tests.test_models import make_document
from tests.test_summary import STEPS, make_report

WATCH_LIST = """\
companies:
  - cvm_code: '009512'
    prefix: PETR
    prefix_source: ticker
    matched_by: ticker
    legal_name: PETROLEO BRASILEIRO S.A. PETROBRAS
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    data_root = tmp_path / "data"
    cache = data_root / "cvm-cache"
    cache.mkdir(parents=True)
    for year in (2025, 2026):
        (cache / f"fca_cia_aberta_{year}.zip").write_bytes(build_package(year=year))
    path = tmp_path / "config.toml"
    path.write_text(
        "[paths]\n"
        f'data_root = "{data_root}"\n'
        f'documents_root = "{tmp_path / "documents"}"\n'
        f'logs_root = "{tmp_path / "logs"}"\n',
        encoding="utf-8",
    )
    return path


# --- Parser wiring. ---


def test_config_is_accepted_before_and_after_the_subcommand() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["--config", "x.toml", "run"]).config == Path("x.toml")
    assert parser.parse_args(["run", "--config", "x.toml"]).config == Path("x.toml")
    assert parser.parse_args(["run"]).config is None


def test_every_subcommand_dispatches_to_its_handler() -> None:
    parser = cli.build_parser()
    handlers = {
        "doctor": cli._cmd_doctor,
        "run": cli._cmd_run,
        "reconcile": cli._cmd_reconcile,
        "purge": cli._cmd_purge,
        "status": cli._cmd_status,
        "list": cli._cmd_list,
    }
    for command, handler in handlers.items():
        assert parser.parse_args([command]).handler is handler
    assert parser.parse_args(["add", "PETR"]).handler is cli._cmd_add
    assert parser.parse_args(["rm", "PETR"]).handler is cli._cmd_rm
    assert parser.parse_args(["resolve", "PETR"]).handler is cli._cmd_resolve


def test_no_flag_leaks_an_internal_name_into_the_help_text() -> None:
    """The metavar rule: a dest that differs from the option string must carry a metavar."""
    parser = cli.build_parser()
    for candidate in [parser, *_subparsers(parser)]:
        for action in candidate._actions:
            if not action.option_strings or action.nargs == 0:
                continue
            spelled = action.option_strings[-1].lstrip("-")
            if action.dest != spelled:
                assert action.metavar is not None, (
                    f"{action.option_strings} would show as {action.dest.upper()}"
                )


def test_run_carries_a_profile_flag_and_never_a_number() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["run", "--monitor"]).monitor is True
    assert parser.parse_args(["run"]).monitor is False
    # The cron line says which profile, never how many days: retuning is a config edit.
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--discovery-days", "3"])


def test_run_hands_the_profile_to_execute_run(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles: list[bool] = []

    def record(config: object, *, monitor: bool = False) -> object:
        profiles.append(monitor)
        return make_report()

    monkeypatch.setattr(cli, "execute_run", record)
    assert cli.main(["--config", str(config_file), "run"]) == 0
    assert cli.main(["--config", str(config_file), "run", "--monitor"]) == 0
    assert profiles == [False, True]


def test_run_ends_by_printing_the_consolidated_table(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recap is printed for every run, so a scheduled one leaves it in the log too."""
    monkeypatch.setattr(cli, "execute_run", lambda config, *, monitor=False: make_report())

    assert cli.main(["--config", str(config_file), "run"]) == 0

    printed = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in printed[-len(STEPS) :]] == STEPS


def test_the_query_is_one_thing_spelled_one_way() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["add", "PETR", "--ticker", "PETR"])
    with pytest.raises(SystemExit):
        parser.parse_args(["add"])


def _subparsers(parser: argparse.ArgumentParser) -> list[argparse.ArgumentParser]:
    found: list[argparse.ArgumentParser] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            found.extend(action.choices.values())
    return found


# --- Exit-code mapping, one failure mode per documented number. ---


def test_a_config_that_does_not_exist_exits_2_in_either_position(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--config", "/nowhere.toml", "status"]) == 2
    assert cli.main(["status", "--config", "/nowhere.toml"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_a_query_matching_nothing_exits_1(config_file: Path) -> None:
    assert cli.main(["--config", str(config_file), "rm", "NOTHING"]) == 1


def test_a_held_lock_exits_3(config_file: Path, tmp_path: Path) -> None:
    with RunLock(tmp_path / "data" / "watcher.lock"):
        assert cli.main(["--config", str(config_file), "reconcile"]) == 3


def test_an_ambiguous_query_exits_1_and_lists_the_candidates(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["--config", str(config_file), "add", "--name", "S.A."])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert len(captured.err.strip().splitlines()) >= 2  # the error plus the candidates


def test_an_ambiguous_query_is_offered_as_a_numbered_choice(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_has_a_human", lambda: True)
    answers = iter(["nine", "2"])  # a typo is re-asked, never resolved generously
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    exit_code = cli.main(["--config", str(config_file), "add", "--name", "S.A."])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "not one of the choices" in captured.out
    chosen = next(line for line in captured.out.splitlines() if line.startswith("  2  "))
    added = next(line for line in captured.out.splitlines() if line.startswith("added: "))
    assert chosen.split()[1] in added  # the CVM code offered as 2 is the one written


def test_declining_the_choice_adds_nothing_and_is_not_a_failure(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_has_a_human", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    assert cli.main(["--config", str(config_file), "add", "--name", "S.A."]) == 0
    assert "cancelled" in capsys.readouterr().out
    assert cli.main(["--config", str(config_file), "list"]) == 0
    assert "empty" in capsys.readouterr().out


def test_an_interrupted_choice_adds_nothing(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_has_a_human", lambda: True)

    def interrupted(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", interrupted)

    assert cli.main(["--config", str(config_file), "add", "--name", "S.A."]) == 0
    assert "cancelled" in capsys.readouterr().out


def test_a_typed_flag_refuses_a_match_by_another_stage(config_file: Path) -> None:
    # 009512 is findable — but by CVM code, not by the ticker the flag promised.
    assert cli.main(["--config", str(config_file), "add", "--ticker", "009512"]) == 1


# --- The watch-list round trip, in-process. ---


def test_add_list_rm_round_trip(config_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--config", str(config_file), "add", "--ticker", "PETR4"]) == 0
    assert cli.main(["--config", str(config_file), "list"]) == 0
    assert "PETR" in capsys.readouterr().out

    assert cli.main(["--config", str(config_file), "rm", "PETR"]) == 0
    assert cli.main(["--config", str(config_file), "list"]) == 0
    assert "empty" in capsys.readouterr().out


def test_list_prints_a_header_and_orders_by_prefix(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--config", str(config_file), "add", "--ticker", "VALE3"]) == 0
    assert cli.main(["--config", str(config_file), "add", "--ticker", "PETR4"]) == 0
    capsys.readouterr()

    assert cli.main(["--config", str(config_file), "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["prefix", "cvm", "code", "legal", "name"]
    # Added VALE first; the reading is alphabetical, not the order of the file.
    assert [line.split()[0] for line in lines[1:]] == ["PETR", "VALE"]
    # The header is wider than every value under it, and still leaves its column standing.
    assert lines[1] == "PETR    009512    PETROLEO BRASILEIRO S.A. PETROBRAS"


def test_adding_twice_changes_nothing_and_says_so(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--config", str(config_file), "add", "PETR4"]) == 0
    assert cli.main(["--config", str(config_file), "add", "PETR4"]) == 0
    assert "already watched" in capsys.readouterr().out


def test_resolve_writes_nothing(config_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--config", str(config_file), "resolve", "VALE3"]) == 0
    out = capsys.readouterr().out
    assert "cvm_code: 004170" in out and "prefix: VALE" in out
    assert not (config_file.parent / "data" / "companies.yaml").exists()


def test_status_speaks_before_any_run_has_happened(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--config", str(config_file), "status"]) == 0
    out = capsys.readouterr().out
    assert "watched companies: 0" in out
    assert "no run has completed" in out


def _source_today() -> tuple[str, str, str]:
    """Today, yesterday, and the first retained day, read the way the watcher reads them."""
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    return str(today), str(today - timedelta(days=1)), str(today - timedelta(days=6))


def test_doctor_shows_which_window_each_profile_sweeps(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configuration is verifiable without spending a sweep on it."""
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(cli, "probe_source", lambda config: "probed without the network")
    assert cli.main(["--config", str(config_file), "doctor"]) == 0

    out = capsys.readouterr().out
    today, yesterday, week_first = _source_today()
    assert f"discovery window: {week_first} .. {today} (7 dates), swept by `run`" in out
    assert (
        f"monitor window: {yesterday} .. {today} (2 dates), swept by `run --monitor`" in out
    )


def test_doctor_reports_the_zone_the_process_is_running_in(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The container derives TZ from source.timezone, and this is where that is verifiable."""
    monkeypatch.setattr(cli, "probe_source", lambda config: "probed without the network")
    monkeypatch.setenv("TZ", "America/Sao_Paulo")

    assert cli.main(["--config", str(config_file), "doctor"]) == 0

    out = capsys.readouterr().out
    assert "timezone: America/Sao_Paulo" in out
    assert "process TZ: America/Sao_Paulo (matches source.timezone)" in out


def test_doctor_says_nothing_is_reading_an_absent_tz(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "probe_source", lambda config: "probed without the network")
    monkeypatch.delenv("TZ", raising=False)

    assert cli.main(["--config", str(config_file), "doctor"]) == 0
    assert "process TZ: unset" in capsys.readouterr().out


def test_doctor_fails_on_a_tz_that_contradicts_the_source_timezone(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreachable inside the container, and the only place a native install would see it."""
    monkeypatch.setattr(cli, "probe_source", lambda config: "probed without the network")
    monkeypatch.setenv("TZ", "Asia/Tokyo")

    assert cli.main(["--config", str(config_file), "doctor"]) == int(ExitCode.PARTIAL_FAILURE)

    out = capsys.readouterr().out
    assert "FAIL  process TZ: Asia/Tokyo contradicts source.timezone=America/Sao_Paulo" in out


WATCHED_ODONTOPREV = """\
companies:
  - cvm_code: '020125'
    prefix: ODPV
    prefix_source: ticker
    matched_by: ticker
    legal_name: ODONTOPREV S.A.
"""


def _renamed_cache(config_file: Path) -> None:
    """The cached 2026 package carries the rename the stored entry has not seen yet."""
    config = load_config(config_file)
    (config.registry_cache_root / "fca_cia_aberta_2026.zip").write_bytes(
        build_package(
            year=2026,
            general=[*fca.GENERAL_ROWS, fca.BRADSAUDE_GENERAL_2026],
            securities=[*fca.SECURITIES_ROWS, fca.BRADSAUDE_SECURITIES_2026],
        )
    )


def _doctor(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(cli, "probe_source", lambda config: "probed without the network")
    return cli.main(["--config", str(config_file), "doctor"])


def test_doctor_reports_drift_between_the_watch_list_and_the_registry(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename is a finding, never a red line: the next run settles it, and a drift that
    failed the command would train its reader to ignore failures."""
    config = load_config(config_file)
    (config.data_root / "companies.yaml").write_text(WATCHED_ODONTOPREV, encoding="utf-8")
    _renamed_cache(config_file)

    assert _doctor(config_file, monkeypatch) == 0

    out = capsys.readouterr().out
    assert (
        "ok    watch list vs registry: 020125 stored as ODPV/ODONTOPREV S.A., "
        "registry says SAUD/BRADSAÚDE S.A. "
        "(the next run moves the prefix; ODPV/ keeps the days already written)" in out
    )


def test_doctor_says_so_when_the_watch_list_and_the_registry_agree(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(config_file)
    (config.data_root / "companies.yaml").write_text(WATCH_LIST, encoding="utf-8")

    assert _doctor(config_file, monkeypatch) == 0

    out = capsys.readouterr().out
    assert "watch list vs registry: 1 stored entry(ies) agree with the cached registry" in out


def test_doctor_reports_absence_from_the_registry_as_absence_not_drift(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(config_file)
    (config.data_root / "companies.yaml").write_text(
        WATCH_LIST.replace("'009512'", "'007617'"), encoding="utf-8"
    )

    assert _doctor(config_file, monkeypatch) == 0

    out = capsys.readouterr().out
    assert (
        "watch list vs registry: 007617 is not in the cached registry (left alone; "
        "a yearly package only holds companies that filed that year)" in out
    )
    assert "stored as" not in out


def test_doctor_with_no_cached_registry_says_so_once(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(config_file)
    (config.data_root / "companies.yaml").write_text(WATCH_LIST, encoding="utf-8")
    for year in (2025, 2026):
        (config.registry_cache_root / f"fca_cia_aberta_{year}.zip").unlink()

    assert _doctor(config_file, monkeypatch) == 0

    out = capsys.readouterr().out
    assert out.count("watch list vs registry") == 1
    assert "watch list vs registry: not compared" in out


def test_doctor_names_the_override_that_keeps_a_prefix_from_following_the_ticker(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file.write_text(
        config_file.read_text(encoding="utf-8")
        + '\n[prefix_overrides]\n"020125" = "DENTAL"\n',
        encoding="utf-8",
    )
    config = load_config(config_file)
    (config.data_root / "companies.yaml").write_text(
        WATCHED_ODONTOPREV.replace("prefix: ODPV", "prefix: DENTAL").replace(
            "prefix_source: ticker", "prefix_source: override"
        ),
        encoding="utf-8",
    )
    _renamed_cache(config_file)

    assert _doctor(config_file, monkeypatch) == 0

    out = capsys.readouterr().out
    assert "[prefix_overrides] names the prefix DENTAL" in out
    assert "the folder stays DENTAL/" in out


def test_status_labels_retention_and_reports_both_discovery_windows(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--config", str(config_file), "status"]) == 0

    out = capsys.readouterr().out
    today, yesterday, week_first = _source_today()
    assert f"retention window: {week_first} .. {today} (7 dates)" in out
    assert f"discovery window: {week_first} .. {today} (7 dates), swept by `run`" in out
    assert (
        f"monitor window: {yesterday} .. {today} (2 dates), swept by `run --monitor`" in out
    )


def test_status_explains_why_each_pending_document_is_pending(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A count answers "is anything missing?"; only the reason answers "why?"."""
    config = load_config(config_file)
    connection = open_manifest(config.manifest_path)
    manifest = Manifest.over(connection, Clock.installed())
    waiting = make_document(document_id=161009, version=6, category="FRE")
    untried = make_document(document_id=161010, version=1, category="ITR")
    for document in (waiting, untried):
        manifest.documents.upsert_observed(document)
    manifest.attempts.record(waiting.identity, AttemptOutcome.FAILURE, "not well-formed XML")
    connection.close()

    assert cli.main(["--config", str(config_file), "status"]) == 0

    out = capsys.readouterr().out
    assert "pending (2):" in out
    assert "(161009, 6) discovered" in out
    assert "1 failed attempt(s)" in out and "not well-formed XML" in out
    assert "not attempted yet" in out


def test_status_says_nothing_about_pending_when_nothing_is_pending(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = load_config(config_file)
    open_manifest(config.manifest_path).close()

    assert cli.main(["--config", str(config_file), "status"]) == 0
    assert "pending" not in capsys.readouterr().out


def test_doctor_says_what_an_fre_will_be_archived_as(
    config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Printed either way: silence about it reads as "there is nothing to know here", and
    what nobody was told is that this archive files a Formulário de Referência as markup."""
    assert _doctor(config_file, monkeypatch) == 0
    assert "reading copies: off" in capsys.readouterr().out

    config_file.write_text(
        config_file.read_text(encoding="utf-8") + "\n[source]\nfre_reading_pdf = true\n",
        encoding="utf-8",
    )
    assert _doctor(config_file, monkeypatch) == 0
    assert "reading copies: on for FRE" in capsys.readouterr().out
