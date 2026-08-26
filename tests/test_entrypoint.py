"""The container's shell layer: the exit-code mapping, the rendered crontab, the catch-up.

These are the only tests in the suite that exercise a deployment rather than the package, and
they run the real scripts against stubs on PATH — a stub `co-docs-watcher` that exits with a
demanded code and records how it was called, a stub `supercronic` that records the crontab it
was handed instead of scheduling anything, and both recording the zone they were handed, which
is what makes the derived TZ an assertion about the scheduler rather than about a log line. A
`python3` shim joins them so that the real TOML parse runs under the interpreter running the
tests, whatever the host calls its own. Nothing here needs Docker: what is under test is the
contract the image relies on, and it is written in POSIX shell precisely so it can be run
without building an image to reach it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from co_docs_watcher.errors import ExitCode

DOCKER = Path(__file__).resolve().parents[1] / "docker"
ENTRYPOINT = DOCKER / "entrypoint.sh"
RUN_PROFILE = DOCKER / "run-profile.sh"
CONFIG_TIMEZONE = DOCKER / "config-timezone.py"

#: What the stub CLI exits with unless a test says otherwise, and where it records its argv.
CLI_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_CALLS"
printf '%s\\n' "${TZ:-}" > "$STUB_CLI_TZ"
exit "${STUB_EXIT_CODE:-0}"
"""

#: The scheduler never runs here: it copies the crontab it was given and returns.
SUPERCRONIC_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_CALLS"
printf '%s\\n' "${TZ:-}" > "$STUB_TZ"
cp "$2" "$STUB_CRONTAB"
"""

#: The entrypoint reaches its TOML parser through `#!/usr/bin/env python3`, and the host's
#: python3 need not be the one running these tests — or have `tomllib` at all.
PYTHON_SHIM = """#!/bin/sh
exec {interpreter} "$@"
"""

#: The zone the tests declare unless one of them says otherwise.
DECLARED_ZONE = "America/Sao_Paulo"


@pytest.fixture
def stubs(tmp_path: Path) -> Iterator[Path]:
    """A directory holding the stubbed binaries, first on PATH for the scripts under test."""
    stub_root = tmp_path / "bin"
    stub_root.mkdir()
    bodies = (
        ("co-docs-watcher", CLI_STUB),
        ("supercronic", SUPERCRONIC_STUB),
        ("python3", PYTHON_SHIM.format(interpreter=sys.executable)),
    )
    for name, body in bodies:
        stub = stub_root / name
        stub.write_text(body)
        stub.chmod(0o755)
    yield stub_root


@pytest.fixture(autouse=True)
def mounted_config(tmp_path: Path) -> Path:
    """The configuration every start has.

    Autouse because the image always names one: `CO_WATCHER_CONFIG` is set in the Dockerfile,
    and the entrypoint refuses to start without a readable file behind it. A test that wants
    another zone, or no zone at all, rewrites this file.
    """
    return write_config(tmp_path, DECLARED_ZONE)


def write_config(tmp_path: Path, zone: str | None) -> Path:
    """Write the mounted configuration, with or without a declared timezone."""
    path = tmp_path / "config.toml"
    declaration = "" if zone is None else f'timezone = "{zone}"\n'
    path.write_text(f"[source]\n{declaration}")
    return path


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
        "STUB_TZ": str(tmp_path / "scheduler-tz"),
        "STUB_CLI_TZ": str(tmp_path / "cli-tz"),
        # The image sets this; a start without it is refused rather than guessed at, so the
        # tests carry it exactly as the image does.
        "CO_WATCHER_CONFIG": str(tmp_path / "config.toml"),
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


def scheduler_zone(tmp_path: Path) -> str:
    """The zone the scheduler was handed — the one its crontab is actually evaluated in."""
    return (tmp_path / "scheduler-tz").read_text().strip()


def cli_zone(tmp_path: Path) -> str:
    return (tmp_path / "cli-tz").read_text().strip()


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


# --- the clock ----------------------------------------------------------------------------


def test_the_scheduler_is_handed_the_declared_zone(stubs: Path, tmp_path: Path) -> None:
    write_config(tmp_path, "Asia/Tokyo")

    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="none")

    assert result.returncode == 0
    # Not the log line: what the crontab is evaluated in is the zone the scheduler process
    # was started with, and that is what is asserted here.
    assert scheduler_zone(tmp_path) == "Asia/Tokyo"
    assert "Asia/Tokyo" in result.stdout


def test_an_agreeing_tz_is_redundant_and_harmless(stubs: Path, tmp_path: Path) -> None:
    write_config(tmp_path, "Asia/Tokyo")

    result = invoke(
        ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="none", TZ="Asia/Tokyo"
    )

    # Orchestrators inject TZ unasked. A value that says what source.timezone already says is
    # not a second answer to the question.
    assert result.returncode == 0
    assert scheduler_zone(tmp_path) == "Asia/Tokyo"


def test_an_empty_tz_is_not_a_declaration(stubs: Path, tmp_path: Path) -> None:
    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="none", TZ="")

    assert result.returncode == 0
    assert scheduler_zone(tmp_path) == DECLARED_ZONE


def test_a_contradicting_tz_refuses_to_start(stubs: Path, tmp_path: Path) -> None:
    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="none", TZ="UTC")

    # The failure this derivation exists to remove: the schedule firing in one zone while the
    # archive is written in another, with nothing said about it.
    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert "UTC" in result.stderr
    assert DECLARED_ZONE in result.stderr
    assert calls(tmp_path) == []


def test_an_undeclared_timezone_refuses_to_start(stubs: Path, tmp_path: Path) -> None:
    write_config(tmp_path, None)

    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="none")

    # The shell carries no default of its own: America/Sao_Paulo is a value the example file
    # ships, not a rule reimplemented here.
    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert "timezone" in result.stderr
    assert calls(tmp_path) == []


def test_a_zone_the_system_database_lacks_refuses_to_start(stubs: Path, tmp_path: Path) -> None:
    write_config(tmp_path, "Mars/Olympus")

    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="none")

    # The scheduler resolves the name against the system zone database, and falls back to UTC
    # in silence when it cannot — which would put the drift back where it was.
    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert "UTC" in result.stderr
    assert calls(tmp_path) == []


def test_an_ad_hoc_command_runs_in_the_declared_zone(stubs: Path, tmp_path: Path) -> None:
    write_config(tmp_path, "Asia/Tokyo")

    result = invoke(ENTRYPOINT, "doctor", stubs=stubs, tmp_path=tmp_path)

    # Derived before the ad-hoc branch, so that `doctor` reports the zone the scheduler would
    # run under instead of the absence of one.
    assert result.returncode == 0
    assert cli_zone(tmp_path) == "Asia/Tokyo"


def test_a_contradicting_tz_refuses_an_ad_hoc_command_too(stubs: Path, tmp_path: Path) -> None:
    result = invoke(ENTRYPOINT, "doctor", stubs=stubs, tmp_path=tmp_path, TZ="UTC")

    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert calls(tmp_path) == []


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
    mounted = tmp_path / "mount" / "config.toml"
    mounted.mkdir(parents=True)

    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, CO_WATCHER_CONFIG=str(mounted))

    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert "config.example.toml" in result.stderr
    assert calls(tmp_path) == []


def test_an_unnamed_configuration_refuses_to_start(stubs: Path, tmp_path: Path) -> None:
    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, CO_WATCHER_CONFIG="")

    # Resolving an unnamed configuration here would mean a second copy of the CLI's discovery
    # chain, and the point of the derivation is that the rule exists once.
    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert "CO_WATCHER_CONFIG" in result.stderr
    assert calls(tmp_path) == []


def test_a_configuration_that_is_not_toml_refuses_to_start(stubs: Path, tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("[source\ntimezone = nope\n")

    result = invoke(ENTRYPOINT, stubs=stubs, tmp_path=tmp_path, RUN_ON_START="none")

    assert result.returncode == int(ExitCode.INVALID_CONFIG)
    assert calls(tmp_path) == []


# --- the TOML the shell reads ---------------------------------------------------------------
#
# The parser the entrypoint reaches through a shebang, exercised on its own. It does not import
# the package: the shell layer is tested without one installed, and the file it reads is the
# same file the CLI reads, so there is nothing here to keep in step with anything.


def read_timezone(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CONFIG_TIMEZONE), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_declared_timezone_is_printed(tmp_path: Path) -> None:
    result = read_timezone(write_config(tmp_path, "Asia/Tokyo"))

    assert result.returncode == 0
    assert result.stdout.strip() == "Asia/Tokyo"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", "timezone"),
        ("[source]\n", "timezone"),
        ('[source]\ntimezone = ""\n', "timezone"),
        ("[source]\ntimezone = 3\n", "timezone"),
        ("[source\n", "invalid TOML"),
    ],
)
def test_a_configuration_that_declares_no_zone_says_so(
    body: str, expected: str, tmp_path: Path
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(body)

    result = read_timezone(path)

    assert result.returncode != 0
    assert expected in result.stderr


def test_a_configuration_that_is_absent_says_so(tmp_path: Path) -> None:
    result = read_timezone(tmp_path / "absent.toml")

    assert result.returncode != 0
    assert "cannot be read" in result.stderr
