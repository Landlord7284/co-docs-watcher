"""The discovery chain is a contract: order, refusal to guess, and the deliberate warning."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from co_docs_watcher.config import (
    CONFIG_ENV_VAR,
    DEFAULT_MAX_REQUESTS_PER_RUN,
    DEFAULT_MIN_REQUEST_INTERVAL,
    DEFAULT_REGISTRY_MAX_AGE_DAYS,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_TIMEZONE,
    discover_config_path,
    load_config,
)
from co_docs_watcher.errors import ConfigError, ExitCode

VALID = """
[paths]
data_root = "/srv/co-docs-watcher/data"
documents_root = "/srv/co-docs-watcher/documents"
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
    assert config.registry_max_age_days == DEFAULT_REGISTRY_MAX_AGE_DAYS


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


def test_the_roots_derive_the_paths_the_rest_of_the_system_uses(tmp_path: Path) -> None:
    write(tmp_path / "config.toml")
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.lock_path == Path("/srv/co-docs-watcher/data/watcher.lock")
    assert config.manifest_path == Path("/srv/co-docs-watcher/data/manifest.sqlite")
    assert config.registry_cache_root == Path("/srv/co-docs-watcher/data/cvm-cache")
    # .tmp/ lives under documents_root so that placement is an atomic rename.
    assert config.staging_root == Path("/srv/co-docs-watcher/documents/.tmp")
    assert config.inbox_root == Path("/srv/co-docs-watcher/documents/_inbox")


def test_a_home_relative_root_is_expanded_and_accepted(tmp_path: Path) -> None:
    write(
        tmp_path / "config.toml",
        """
[paths]
data_root = "~/watcher/data"
documents_root = "~/watcher/documents"
""",
    )
    config = load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert config.data_root.is_absolute()
    assert config.data_root == Path.home() / "watcher" / "data"


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("[paths\n", "invalid TOML"),
        ('[paths]\ndata_root = "/a"\n', "documents_root is required"),
        ('[paths]\ndocuments_root = "/a"\n', "data_root is required"),
        ('[paths]\ndata_root = "var/data"\ndocuments_root = "/a"\n', "absolute path"),
        ('[paths]\ndata_root = ""\ndocuments_root = "/a"\n', "non-empty string"),
        (VALID + '[source]\ntimezone = "Mars/Olympus"\n', "unknown timezone"),
        (VALID + "[retention]\ndays = 0\n", "integer >= 1"),
        (VALID + '[retention]\ndays = "seven"\n', "integer >= 1"),
        (VALID + "[source]\nmin_request_interval = 0\n", "number > 0"),
        (VALID + "[source]\nmax_requests_per_run = -3\n", "integer >= 1"),
        (VALID + "[retention]\nweeks = 3\n", "unknown key"),
        (VALID + "[registry]\nmax_age_days = 0\n", "integer >= 1"),
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
