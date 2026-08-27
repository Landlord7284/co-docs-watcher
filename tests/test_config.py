"""The discovery chain is a contract: order, refusal to guess, and the deliberate warning."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from co_docs_watcher.archive_modes import ArchiveModes
from co_docs_watcher.config import (
    CONFIG_ENV_VAR,
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_BACKOFF_INITIAL,
    DEFAULT_DIRECTORY_MODE,
    DEFAULT_FILE_MODE,
    DEFAULT_LOG_BACKUPS,
    DEFAULT_LOG_MAX_BYTES,
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DEFAULT_MAX_EXTRACTED_BYTES,
    DEFAULT_MAX_LISTING_BYTES,
    DEFAULT_MAX_REQUESTS_PER_RUN,
    DEFAULT_MIN_REQUEST_INTERVAL,
    DEFAULT_MONITOR_DAYS,
    DEFAULT_REGISTRY_MAX_AGE_DAYS,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_RETRIES,
    DEFAULT_TIMEZONE,
    discover_config_path,
    load_config,
)
from co_docs_watcher.errors import ConfigError, ExitCode

VALID = """
[paths]
data_root = "/srv/co-docs-watcher/data"
documents_root = "/srv/co-docs-watcher/documents"
logs_root = "/srv/co-docs-watcher/logs"
"""


def write(path: Path, content: str = VALID) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_the_chain_is_walked_in_order(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    home = tmp_path / "home"
    explicit = write(tmp_path / "explicit.toml")
    from_env = write(tmp_path / "env.toml")
    local = write(cwd / "config.toml")
    alternate = write(cwd / "co-docs-watcher.toml")
    user = write(home / ".config" / "co-docs-watcher" / "config.toml")
    env = {CONFIG_ENV_VAR: str(from_env)}

    def resolve(**overrides: object) -> Path | None:
        kwargs: dict[str, object] = {"env": env, "cwd": cwd, "home": home}
        return discover_config_path(**(kwargs | overrides))  # type: ignore[arg-type]

    assert resolve(explicit=explicit) == explicit
    assert resolve() == from_env
    assert resolve(env={}) == local
    local.unlink()
    assert resolve(env={}) == alternate
    alternate.unlink()
    assert resolve(env={}) == user
    user.unlink()
    assert resolve(env={}) is None


def test_an_explicit_path_that_does_not_exist_refuses_to_fall_through(tmp_path: Path) -> None:
    present = write(tmp_path / "config.toml")
    with pytest.raises(ConfigError, match="--config"):
        discover_config_path(tmp_path / "absent.toml", env={}, cwd=tmp_path, home=tmp_path)
    with pytest.raises(ConfigError, match=CONFIG_ENV_VAR):
        discover_config_path(
            env={CONFIG_ENV_VAR: str(tmp_path / "absent.toml")}, cwd=tmp_path, home=tmp_path
        )
    assert present.exists()  # the fallback candidate was never consulted


def test_falling_back_to_defaults_logs_a_deliberate_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="co_docs_watcher.config"):
        config = load_config(env={}, cwd=tmp_path, home=tmp_path)

    assert config.uses_builtin_defaults
    assert config.origin is None
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert "built-in defaults" in caplog.text
    assert config.data_root == tmp_path / "var" / "data"
    assert config.documents_root == tmp_path / "var" / "documents"


def test_a_configured_run_is_silent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    write(tmp_path / "config.toml")
    with caplog.at_level(logging.WARNING, logger="co_docs_watcher.config"):
        config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert caplog.records == []
    assert config.origin == tmp_path / "config.toml"


def test_documented_defaults_apply_when_the_file_omits_them(tmp_path: Path) -> None:
    write(tmp_path / "config.toml")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.timezone_name == DEFAULT_TIMEZONE
    assert config.retention_days == DEFAULT_RETENTION_DAYS
    assert config.min_request_interval == DEFAULT_MIN_REQUEST_INTERVAL
    assert config.max_requests_per_run == DEFAULT_MAX_REQUESTS_PER_RUN
    assert config.max_listing_bytes == DEFAULT_MAX_LISTING_BYTES
    assert config.max_download_bytes == DEFAULT_MAX_DOWNLOAD_BYTES
    assert config.max_extracted_bytes == DEFAULT_MAX_EXTRACTED_BYTES
    assert config.retries == DEFAULT_RETRIES
    assert config.backoff_initial == DEFAULT_BACKOFF_INITIAL
    assert config.backoff_factor == DEFAULT_BACKOFF_FACTOR
    assert config.registry_max_age_days == DEFAULT_REGISTRY_MAX_AGE_DAYS
    assert config.discovery_days == DEFAULT_RETENTION_DAYS
    assert config.monitor_days == DEFAULT_MONITOR_DAYS
    assert config.directory_mode == DEFAULT_DIRECTORY_MODE
    assert config.file_mode == DEFAULT_FILE_MODE


def test_the_shipped_example_loads_and_shows_the_defaults(tmp_path: Path) -> None:
    """The example is copied to make a first configuration, so a key the schema does not know
    is a first run that refuses to start — and every value in it claims to be the default."""
    example = Path(__file__).resolve().parent.parent / "config.example.toml"
    shutil.copy(example, tmp_path / "config.toml")

    config = load_config(env={}, cwd=tmp_path, home=tmp_path)

    assert config.timezone_name == DEFAULT_TIMEZONE
    assert config.retention_days == DEFAULT_RETENTION_DAYS
    assert config.discovery_days == DEFAULT_RETENTION_DAYS
    assert config.monitor_days == DEFAULT_MONITOR_DAYS
    assert config.registry_max_age_days == DEFAULT_REGISTRY_MAX_AGE_DAYS
    assert config.log_max_bytes == DEFAULT_LOG_MAX_BYTES
    assert config.log_backups == DEFAULT_LOG_BACKUPS
    assert (config.directory_mode, config.file_mode) == (DEFAULT_DIRECTORY_MODE, DEFAULT_FILE_MODE)
    assert config.min_request_interval == DEFAULT_MIN_REQUEST_INTERVAL
    assert config.max_requests_per_run == DEFAULT_MAX_REQUESTS_PER_RUN
    assert config.max_listing_bytes == DEFAULT_MAX_LISTING_BYTES
    assert config.max_download_bytes == DEFAULT_MAX_DOWNLOAD_BYTES
    assert config.max_extracted_bytes == DEFAULT_MAX_EXTRACTED_BYTES
    assert config.retries == DEFAULT_RETRIES
    assert config.backoff_initial == DEFAULT_BACKOFF_INITIAL
    assert config.backoff_factor == DEFAULT_BACKOFF_FACTOR


def test_the_caps_and_the_retry_policy_are_read_from_the_file(tmp_path: Path) -> None:
    write(
        tmp_path / "config.toml",
        VALID
        + """
[source]
max_listing_bytes = 1024
max_download_bytes = 2048
max_extracted_bytes = 4096
retries = 1
backoff_initial = 20.0
backoff_factor = 1.5
""",
    )
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert (config.max_listing_bytes, config.max_download_bytes) == (1024, 2048)
    assert config.max_extracted_bytes == 4096
    assert (config.retries, config.backoff_initial, config.backoff_factor) == (1, 20.0, 1.5)


def test_no_retries_at_all_is_a_policy_and_a_negative_count_is_not(tmp_path: Path) -> None:
    write(tmp_path / "config.toml", VALID + "\n[source]\nretries = 0\n")
    assert load_config(env={}, cwd=tmp_path, home=tmp_path).retries == 0

    write(tmp_path / "config.toml", VALID + "\n[source]\nretries = -1\n")
    with pytest.raises(ConfigError, match="retries must be an integer >= 0"):
        load_config(env={}, cwd=tmp_path, home=tmp_path)


def test_a_backoff_factor_below_one_is_refused(tmp_path: Path) -> None:
    # A factor under 1 shrinks the wait on every attempt, which is not a backoff.
    write(tmp_path / "config.toml", VALID + "\n[source]\nbackoff_factor = 0.5\n")
    with pytest.raises(ConfigError, match="backoff_factor must be a number >= 1"):
        load_config(env={}, cwd=tmp_path, home=tmp_path)


def test_a_written_backoff_below_the_request_floor_is_refused(tmp_path: Path) -> None:
    # Both waits happen before a retry and the floor covers what the backoff left, so a
    # backoff under it is a number with no effect — refused rather than silently overridden.
    write(
        tmp_path / "config.toml",
        VALID + "\n[source]\nmin_request_interval = 30.0\nbackoff_initial = 10.0\n",
    )
    with pytest.raises(ConfigError, match=r"backoff_initial .* is below min_request_interval"):
        load_config(env={}, cwd=tmp_path, home=tmp_path)


def test_the_default_backoff_follows_a_raised_floor(tmp_path: Path) -> None:
    # Only the default accommodates: a file that raises the interval and names no backoff
    # still has one that means something.
    write(tmp_path / "config.toml", VALID + "\n[source]\nmin_request_interval = 30.0\n")
    assert load_config(env={}, cwd=tmp_path, home=tmp_path).backoff_initial == 30.0


def test_the_archive_modes_are_declared_even_when_the_file_omits_them(tmp_path: Path) -> None:
    """A configuration naming neither behaves as the defaults, never as the process umask."""
    write(tmp_path / "config.toml")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.archive_modes == ArchiveModes(0o755, 0o644)


def test_a_mode_is_read_as_the_octal_it_is_written_as(tmp_path: Path) -> None:
    write(tmp_path / "config.toml", VALID + "[files]\ndirectory_mode = 0o750\nfile_mode = 0o640\n")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.directory_mode == 0o750 == 488
    assert config.archive_modes == ArchiveModes(0o750, 0o640)


def test_a_decimal_mode_means_what_it_says(tmp_path: Path) -> None:
    """Octal is the notation an operator reads; the value is an integer either way."""
    write(tmp_path / "config.toml", VALID + "[files]\ndirectory_mode = 493\n")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.directory_mode == 0o755


def test_discovery_days_follows_retention_when_unset(tmp_path: Path) -> None:
    write(tmp_path / "config.toml", VALID + "[retention]\ndays = 10\n")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.discovery_days == 10
    assert config.sweep_days(monitor=False) == 10
    assert config.sweep_days(monitor=True) == DEFAULT_MONITOR_DAYS


def test_a_one_day_archive_needs_no_discovery_section(tmp_path: Path) -> None:
    """The monitor_days default accommodates the window; only a written value is refused."""
    write(tmp_path / "config.toml", VALID + "[retention]\ndays = 1\n")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.discovery_days == 1
    assert config.monitor_days == 1


def test_the_discovery_section_settles_both_sweep_widths(tmp_path: Path) -> None:
    write(
        tmp_path / "config.toml",
        VALID + "[retention]\ndays = 10\n[discovery]\ndays = 5\nmonitor_days = 3\n",
    )
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.sweep_days(monitor=False) == 5
    assert config.sweep_days(monitor=True) == 3


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (VALID + "[discovery]\ndays = 9\n", r"exceeds \[retention\] days \(7\)"),
        (
            VALID + "[discovery]\ndays = 3\nmonitor_days = 4\n",
            r"exceeds \[discovery\] days \(3\)",
        ),
        (VALID + "[discovery]\nmonitor_days = 0\n", "integer >= 1"),
        (VALID + "[discovery]\nweeks = 1\n", r"unknown key\(s\) in \[discovery\]"),
    ],
)
def test_a_disordered_discovery_window_refuses_to_start(
    tmp_path: Path, content: str, match: str
) -> None:
    write(tmp_path / "config.toml", content)
    with pytest.raises(ConfigError, match=match):
        load_config(env={}, cwd=tmp_path, home=tmp_path)


def test_values_from_the_file_win(tmp_path: Path) -> None:
    write(
        tmp_path / "config.toml",
        VALID
        + """
[retention]
days = 21

[registry]
max_age_days = 30

[source]
timezone = "UTC"
min_request_interval = 8.5
max_requests_per_run = 40
""",
    )
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.retention_days == 21
    assert config.registry_max_age_days == 30
    assert config.timezone_name == "UTC"
    assert config.min_request_interval == 8.5
    assert config.max_requests_per_run == 40


def test_prefix_overrides_are_keyed_by_cvm_code(tmp_path: Path) -> None:
    write(
        tmp_path / "config.toml",
        VALID
        + """
[prefix_overrides]
"003549" = "schlosser"
3271 = "ENERGISA-MINAS"
""",
    )
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    # Keys are data, not schema: this is the one section where an unknown key is not a typo.
    assert config.prefix_overrides == {"003549": "SCHLOSSER", "003271": "ENERGISA-MINAS"}


def test_the_file_without_overrides_has_none(tmp_path: Path) -> None:
    write(tmp_path / "config.toml")
    assert load_config(env={}, cwd=tmp_path, home=tmp_path).prefix_overrides == {}


def test_the_roots_derive_the_paths_the_rest_of_the_system_uses(tmp_path: Path) -> None:
    write(tmp_path / "config.toml")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.lock_path == Path("/srv/co-docs-watcher/data/watcher.lock")
    assert config.manifest_path == Path("/srv/co-docs-watcher/data/manifest.sqlite")
    assert config.registry_cache_root == Path("/srv/co-docs-watcher/data/cvm-cache")
    # .tmp/ lives under documents_root so that placement is an atomic rename.
    assert config.staging_root == Path("/srv/co-docs-watcher/documents/.tmp")
    assert config.inbox_root == Path("/srv/co-docs-watcher/documents/_inbox")


def test_the_log_file_is_named_under_the_logs_root(tmp_path: Path) -> None:
    write(tmp_path / "config.toml")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)

    assert config.logs_root == Path("/srv/co-docs-watcher/logs")
    assert config.log_path == Path("/srv/co-docs-watcher/logs/co-docs-watcher.log")
    assert config.log_max_bytes == DEFAULT_LOG_MAX_BYTES
    assert config.log_backups == DEFAULT_LOG_BACKUPS


def test_the_rotation_policy_is_configurable(tmp_path: Path) -> None:
    write(tmp_path / "config.toml", VALID + "[logging]\nmax_bytes = 1048576\nbackups = 2\n")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)

    assert config.log_max_bytes == 1048576
    assert config.log_backups == 2


def test_a_relative_root_is_anchored_on_the_configuration_file(tmp_path: Path) -> None:
    """The project-local posture: a checkout that archives beside its own config file."""
    project = tmp_path / "project"
    project.mkdir()
    write(
        project / "config.toml",
        """
[paths]
data_root = "var/data"
documents_root = "var/documents"
logs_root = "var/logs"
""",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    config = load_config(project / "config.toml", env={}, cwd=elsewhere, home=elsewhere)
    assert config.data_root == project.resolve() / "var" / "data"
    assert config.documents_root == project.resolve() / "var" / "documents"
    assert config.logs_root == project.resolve() / "var" / "logs"


def test_a_relative_config_path_still_anchors_absolutely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--config config.toml`` is the common spelling; the roots come out absolute anyway."""
    write(
        tmp_path / "config.toml",
        """
[paths]
data_root = "var/data"
documents_root = "var/documents"
logs_root = "var/logs"
""",
    )
    monkeypatch.chdir(tmp_path)
    config = load_config(Path("config.toml"), env={}, cwd=tmp_path, home=tmp_path)
    assert config.data_root.is_absolute()
    assert config.data_root == tmp_path.resolve() / "var" / "data"


def test_a_home_relative_root_is_expanded_and_accepted(tmp_path: Path) -> None:
    write(
        tmp_path / "config.toml",
        """
[paths]
data_root = "~/watcher/data"
documents_root = "~/watcher/documents"
logs_root = "~/watcher/logs"
""",
    )
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.data_root.is_absolute()
    assert config.data_root == Path.home() / "watcher" / "data"


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("[paths\n", "invalid TOML"),
        ('[paths]\ndata_root = "/a"\nlogs_root = "/c"\n', "documents_root is required"),
        ('[paths]\ndocuments_root = "/a"\nlogs_root = "/c"\n', "data_root is required"),
        ('[paths]\ndata_root = "/a"\ndocuments_root = "/b"\n', "logs_root is required"),
        ('[paths]\ndata_root = ""\ndocuments_root = "/a"\nlogs_root = "/c"\n', "non-empty string"),
        (VALID + "[logging]\nmax_bytes = 0\n", "integer >= 1"),
        (VALID + "[logging]\nrotate = true\n", "unknown key"),
        (VALID + '[source]\ntimezone = "Mars/Olympus"\n', "unknown timezone"),
        (VALID + "[retention]\ndays = 0\n", "integer >= 1"),
        (VALID + '[retention]\ndays = "seven"\n', "integer >= 1"),
        (VALID + "[source]\nmin_request_interval = 0\n", "number > 0"),
        (VALID + "[source]\nmax_requests_per_run = -3\n", "integer >= 1"),
        (VALID + "[retention]\nweeks = 3\n", "unknown key"),
        (VALID + "[registry]\nmax_age_days = 0\n", "integer >= 1"),
        (VALID + '[prefix_overrides]\nPETR = "PETR"\n', "is not a CVM code"),
        # Normalizing is not validating: stripping the non-digits out of these leaves
        # ``000004`` and ``20260824``, an override aimed at a company nobody named.
        (VALID + '[prefix_overrides]\nPETR4 = "PETR"\n', "is not a CVM code"),
        (VALID + '[prefix_overrides]\n"2026-08-24" = "PETR"\n', "is not a CVM code"),
        (VALID + '[prefix_overrides]\n"009512" = "../escape"\n', "letters, digits and hyphens"),
        (VALID + "[prefix_overrides]\n\"009512\" = 4\n", "letters, digits and hyphens"),
        # Longer than a folder name may be: refused here rather than shortened downstream,
        # which would name the folder after something nobody wrote.
        (
            VALID + '[prefix_overrides]\n"009512" = "' + "A" * 25 + '"\n',
            "at most 24 characters",
        ),
        (VALID + "[files]\ndirectory_mode = 0o10000\n", r"between 0o0 and 0o7777 \(got 0o10000\)"),
        (VALID + "[files]\nfile_mode = -1\n", "between 0o0 and 0o7777"),
        (VALID + '[files]\nfile_mode = "0o644"\n', "must be an integer mode"),
        (VALID + "[files]\numask = 18\n", "unknown key"),
        (VALID + "[archive]\nkeep = true\n", "unknown section"),
        ('paths = "nope"\n', "must be a table"),
    ],
)
def test_invalid_configuration_refuses_to_start(tmp_path: Path, content: str, match: str) -> None:
    write(tmp_path / "config.toml", content)
    with pytest.raises(ConfigError, match=match) as raised:
        load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert raised.value.exit_code is ExitCode.INVALID_CONFIG


def test_the_timezone_is_resolved_once_at_load(tmp_path: Path) -> None:
    write(tmp_path / "config.toml", VALID + '[source]\ntimezone = "America/Sao_Paulo"\n')
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    # A ZoneInfo instance, not a string to be re-resolved later at every call site.
    assert config.timezone.key == "America/Sao_Paulo"
