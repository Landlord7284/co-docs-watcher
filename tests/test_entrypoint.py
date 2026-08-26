"""The container's shell layer: the exit-code mapping, the rendered crontab, the catch-up.

These are the only tests in the suite that exercise a deployment rather than the package, and
they run the real scripts against stubs on PATH — a stub `co-docs-watcher` that exits with a
demanded code and records how it was called, and a stub `supercronic` that records the crontab
it was handed instead of scheduling anything. Nothing here needs Docker: what is under test is
the contract the image relies on, and it is written in POSIX shell precisely so it can be run
without building an image to reach it.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from co_docs_watcher.errors import ExitCode

DOCKER = Path(__file__).resolve().parents[1] / "docker"
ENTRYPOINT = DOCKER / "entrypoint.sh"
RUN_PROFILE = DOCKER / "run-profile.sh"

#: What the stub CLI exits with unless a test says otherwise, and where it records its argv.
CLI_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_CALLS"
exit "${STUB_EXIT_CODE:-0}"
"""

#: The scheduler never runs here: it copies the crontab it was given and returns.
SUPERCRONIC_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_CALLS"
cp "$2" "$STUB_CRONTAB"
"""


@pytest.fixture
def stubs(tmp_path: Path) -> Iterator[Path]:
    """A directory holding the stubbed binaries, first on PATH for the scripts under test."""
    stub_root = tmp_path / "bin"
    stub_root.mkdir()
    for name, body in (("co-docs-watcher", CLI_STUB), ("supercronic", SUPERCRONIC_STUB)):
        stub = stub_root / name
        stub.write_text(body)
        stub.chmod(0o755)
    yield stub_root


def invoke(
    script: Path,
    *arguments: str,
    stubs: Path,
    tmp_path: Path,
    **environment: str,
) -> subprocess.CompletedProcess[str]:
    """Run one of the scripts with the stubs on PATH and a scratch TMPDIR of its own."""
    env = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        # The entrypoint renders its crontab under TMPDIR; pointing that at the test's own
        # directory is what makes the rendered file readable without a fixed path in the
        # script — a fixed one would let an earlier start's crontab be the one scheduled.
        "TMPDIR": str(tmp_path),
        "STUB_CALLS": str(tmp_path / "calls"),
        "STUB_CRONTAB": str(tmp_path / "crontab"),
        **environment,
    }
    return subprocess.run(
        [str(script), *arguments],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def calls(tmp_path: Path) -> list[str]:
    """Every invocation the stubs recorded, in order, as the argument line each received."""
    recorded = tmp_path / "calls"
    return recorded.read_text().splitlines() if recorded.exists() else []


def crontab(tmp_path: Path) -> list[str]:
    return (tmp_path / "crontab").read_text().splitlines()


# --- the exit-code mapping ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (int(ExitCode.CLEAN), 0),
        (int(ExitCode.PARTIAL_FAILURE), 1),
        (int(ExitCode.INVALID_CONFIG), 2),
        # The one mapping: a profile that fired while another run held the flock did nothing
        # wrong, and reporting it as a failure trains its reader to ignore failures.
        (int(ExitCode.LOCK_HELD), 0),
        # And the one that must never be softened: a captcha is not transient, and the
        # scheduler is exactly who needs to hear about it.
        (int(ExitCode.CAPTCHA_REQUIRED), 4),
    ],
)
def test_only_the_held_lock_is_mapped(
    code: int, expected: int, stubs: Path, tmp_path: Path
) -> None:
    result = invoke(
        RUN_PROFILE, "monitor", stubs=stubs, tmp_path=tmp_path, STUB_EXIT_CODE=str(code)
    )
    assert result.returncode == expected


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("sweep", "run"), ("monitor", "run --monitor")],
)
def test_a_profile_is_a_flag_and_never_a_window(
    profile: str, expected: str, stubs: Path, tmp_path: Path
) -> None:
    result = invoke(RUN_PROFILE, profile, stubs=stubs, tmp_path=tmp_path)

    assert result.returncode == 0
    assert calls(tmp_path) == [expected]


@pytest.mark.parametrize("arguments", [(), ("daily",)])
def test_an_unknown_profile_is_refused(
    arguments: tuple[str, ...], stubs: Path, tmp_path: Path
) -> None:
    result = invoke(RUN_PROFILE, *arguments, stubs=stubs, tmp_path=tmp_path)

    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert calls(tmp_path) == []


# --- the rendered crontab -----------------------------------------------------------------


def test_both_profiles_are_scheduled_from_the_environment(stubs: Path, tmp_path: Path) -> None:
    result = invoke(
        ENTRYPOINT,
        stubs=stubs,
        tmp_path=tmp_path,
        TZ="America/Sao_Paulo",
        MONITOR_SCHEDULE="0 7-23 * * *",
        SWEEP_SCHEDULE="10 5 * * *",
        RUN_ON_START="none",
    )

    assert result.returncode == 0
    assert crontab(tmp_path) == [
        f"0 7-23 * * * {RUN_PROFILE} monitor",
        f"10 5 * * * {RUN_PROFILE} sweep",
    ]
    # The schedule the container is actually running, logged rather than left to be read by
    # exec'ing into it.
    assert "0 7-23 * * *" in result.stdout


@pytest.mark.parametrize(
    ("disabled", "remaining"),
    [("MONITOR_ENABLED", "sweep"), ("SWEEP_ENABLED", "monitor")],
)
def test_a_disabled_profile_loses_its_line(
    disabled: str, remaining: str, stubs: Path, tmp_path: Path
) -> None:
    environment = {disabled: "false", "RUN_ON_START": "none"}
    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, **environment)

    assert result.returncode == 0
    assert [line.split()[-1] for line in crontab(tmp_path)] == [remaining]


def test_a_container_that_would_schedule_nothing_refuses_to_start(
    stubs: Path, tmp_path: Path
) -> None:
    result = invoke(
        ENTRYPOINT,
        stubs=stubs,
        tmp_path=tmp_path,
        MONITOR_ENABLED="false",
        SWEEP_ENABLED="false",
        RUN_ON_START="none",
    )

    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert calls(tmp_path) == []


@pytest.mark.parametrize(
    ("variable", "value"),
    [("MONITOR_ENABLED", "sim"), ("SWEEP_ENABLED", ""), ("RUN_ON_START", "hourly")],
)
def test_an_environment_that_cannot_be_read_refuses_to_start(
    variable: str, value: str, stubs: Path, tmp_path: Path
) -> None:
    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, **{variable: value})

    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert variable in result.stderr
    assert not (tmp_path / "crontab").exists()


def test_an_unset_tz_is_announced(stubs: Path, tmp_path: Path) -> None:
    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="none")

    assert result.returncode == 0
    assert "TZ is unset" in result.stderr


# --- the catch-up run ---------------------------------------------------------------------


def test_the_catch_up_is_the_full_sweep(stubs: Path, tmp_path: Path) -> None:
    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="sweep")

    assert result.returncode == 0
    # The sweep first, and only then the crontab the scheduler is handed: a start follows a
    # gap, and the gap is what the narrow window cannot see.
    assert calls(tmp_path)[0] == "run"


def test_the_catch_up_can_be_the_monitor_or_nothing(stubs: Path, tmp_path: Path) -> None:
    monitor = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="monitor")
    assert monitor.returncode == 0
    assert calls(tmp_path)[0] == "run --monitor"

    (tmp_path / "calls").unlink()
    nothing = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="none")
    assert nothing.returncode == 0
    assert [call for call in calls(tmp_path) if not call.startswith("-")] == []


def test_a_failed_catch_up_does_not_cost_the_schedule(stubs: Path, tmp_path: Path) -> None:
    result = invoke(
        ENTRYPOINT,
        stubs=stubs,
        tmp_path=tmp_path,
        RUN_ON_START="sweep",
        STUB_EXIT_CODE=str(int(ExitCode.PARTIAL_FAILURE)),
    )

    # A container that refuses to start over one bad run stops monitoring altogether over a
    # source that was briefly down. The failure is said out loud; the next firing is the retry.
    assert result.returncode == 0
    assert "exited 1" in result.stderr
    assert crontab(tmp_path)


# --- ad-hoc commands ----------------------------------------------------------------------


def test_any_other_argument_is_the_cli_itself(stubs: Path, tmp_path: Path) -> None:
    result = invoke(ENTRYPOINT, "doctor", stubs=stubs, tmp_path=tmp_path)

    assert result.returncode == 0
    assert calls(tmp_path) == ["doctor"]
    assert not (tmp_path / "crontab").exists()


def test_an_ad_hoc_command_keeps_the_exit_code_it_earned(stubs: Path, tmp_path: Path) -> None:
    result = invoke(
        ENTRYPOINT,
        "run",
        stubs=stubs,
        tmp_path=tmp_path,
        STUB_EXIT_CODE=str(int(ExitCode.LOCK_HELD)),
    )

    # Unmapped, unlike the scheduled path: someone typed this and is owed the truth that a
    # run was already in flight.
    assert result.returncode == int(ExitCode.LOCK_HELD)


# --- the mounted configuration ------------------------------------------------------------


def test_a_configuration_that_is_not_a_file_refuses_to_start(stubs: Path, tmp_path: Path) -> None:
    # Docker creates a directory where a bind mount's source is missing, so this is what a
    # first start without the copy step actually looks like from inside the container.
    mounted = tmp_path / "config.toml"
    mounted.mkdir()

    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, CO_WATCHER_CONFIG=str(mounted))

    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert "config.example.toml" in result.stderr
    assert calls(tmp_path) == []
