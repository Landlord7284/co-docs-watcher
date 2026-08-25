"""The CLI surface: flag wiring, the metavar rule, dispatch, and the exit-code mapping.

Everything here runs in-process. The full flow — subprocesses, the fake server, the wire —
belongs to the integration suite; these tests pin the glue itself: that ``--config`` works in
both positions, that no flag leaks an internal name into ``--help``, and that each failure
mode reaches the operator as the documented number.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from co_docs_watcher import cli
from co_docs_watcher.lock import RunLock
from tests.fca import build_package

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
        f'documents_root = "{tmp_path / "documents"}"\n',
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
