"""Configuration: discovery chain, parsing, and validation.

The chain is fixed and first hit wins:

1. ``--config PATH``
2. ``$CO_WATCHER_CONFIG``
3. ``./config.toml``
4. ``./co-docs-watcher.toml``
5. ``~/.config/co-docs-watcher/config.toml``
6. built-in defaults

Step 6 logs a deliberate warning. The defaults point at ``./var/…``, relative to whatever
directory the process happens to be in, so a silent fallback means operating on a different
archive than intended — and an archive that looks empty is indistinguishable from a quiet
market. Steps 1 and 2 are explicit requests: if the file named there does not exist, the
watcher refuses to start rather than quietly moving down the chain.

Anything invalid — unreadable TOML, an unknown key, a relative root, a timezone name the
system does not know — raises ``ConfigError`` and the CLI exits ``2``.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from co_docs_watcher.clock import install_source_timezone
from co_docs_watcher.errors import ConfigError

__all__ = [
    "CONFIG_ENV_VAR",
    "DEFAULT_MAX_REQUESTS_PER_RUN",
    "DEFAULT_MIN_REQUEST_INTERVAL",
    "DEFAULT_RETENTION_DAYS",
    "DEFAULT_TIMEZONE",
    "Config",
    "discover_config_path",
    "load_config",
]

logger = logging.getLogger(__name__)

CONFIG_ENV_VAR = "CO_WATCHER_CONFIG"

#: Timezone of the source. Delivery dates, "today", and directory names are all read in it.
DEFAULT_TIMEZONE = "America/Sao_Paulo"

#: Retained dates, counting today. ``first_retained_date = today - (N - 1)``.
DEFAULT_RETENTION_DAYS = 7

#: Seconds between requests. The backend behind the page drops under load; this is the floor.
DEFAULT_MIN_REQUEST_INTERVAL = 5.0

#: Safety fuse: a single run never issues more requests than this, whatever it still has to do.
DEFAULT_MAX_REQUESTS_PER_RUN = 200

#: Relative and deliberately unusable-by-accident: reaching these means the warning fired.
DEFAULT_DATA_ROOT = Path("var/data")
DEFAULT_DOCUMENTS_ROOT = Path("var/documents")

_SCHEMA: dict[str, set[str]] = {
    "paths": {"data_root", "documents_root"},
    "retention": {"days"},
    "source": {"timezone", "min_request_interval", "max_requests_per_run"},
}


@dataclass(frozen=True, slots=True)
class Config:
    """The loaded configuration.

    ``data_root`` is private — YAML watch list, SQLite manifest, lock, registry cache — and
    must live on a filesystem local to the process: SQLite locking over SMB/NFS is unreliable.
    ``documents_root`` is the shareable archive, and holds ``.tmp/`` so that placement stays an
    atomic ``rename`` within one filesystem.

    ``origin`` is the file the values came from, or ``None`` when the built-in defaults were
    used — which is what makes "am I looking at the archive I think I am?" answerable.
    """

    data_root: Path
    documents_root: Path
    timezone: ZoneInfo
    retention_days: int
    min_request_interval: float
    max_requests_per_run: int
    origin: Path | None

    @property
    def timezone_name(self) -> str:
        return str(self.timezone.key)

    @property
    def uses_builtin_defaults(self) -> bool:
        return self.origin is None

    @property
    def lock_path(self) -> Path:
        return self.data_root / "watcher.lock"

    @property
    def manifest_path(self) -> Path:
        return self.data_root / "manifest.sqlite"

    @property
    def staging_root(self) -> Path:
        return self.documents_root / ".tmp"

    @property
    def inbox_root(self) -> Path:
        return self.documents_root / "_inbox"


def discover_config_path(
    explicit: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Path | None:
    """Walk the discovery chain and return the first hit, or ``None`` for the defaults.

    ``cwd``, ``home`` and ``env`` are injectable so the chain can be tested without touching
    the machine running the tests.
    """
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd
    home = Path.home() if home is None else home

    if explicit is not None:
        return _required(Path(explicit).expanduser(), why="--config")

    from_env = env.get(CONFIG_ENV_VAR)
    if from_env:
        return _required(Path(from_env).expanduser(), why=f"${CONFIG_ENV_VAR}")

    candidates = [
        cwd / "config.toml",
        cwd / "co-docs-watcher.toml",
        home / ".config" / "co-docs-watcher" / "config.toml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_config(
    explicit: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Config:
    """Resolve the chain, read the file if there is one, and validate everything."""
    path = discover_config_path(explicit, env=env, cwd=cwd, home=home)
    if path is None:
        logger.warning(
            "No configuration file found (looked at $%s, ./config.toml, ./co-docs-watcher.toml, "
            "~/.config/co-docs-watcher/config.toml); falling back to built-in defaults, which "
            "point at %s and %s relative to the current directory",
            CONFIG_ENV_VAR,
            DEFAULT_DATA_ROOT,
            DEFAULT_DOCUMENTS_ROOT,
        )
        cwd = Path.cwd() if cwd is None else cwd
        defaults = Config(
            data_root=cwd / DEFAULT_DATA_ROOT,
            documents_root=cwd / DEFAULT_DOCUMENTS_ROOT,
            timezone=_timezone(DEFAULT_TIMEZONE),
            retention_days=DEFAULT_RETENTION_DAYS,
            min_request_interval=DEFAULT_MIN_REQUEST_INTERVAL,
            max_requests_per_run=DEFAULT_MAX_REQUESTS_PER_RUN,
            origin=None,
        )
        return _installed(defaults)
    return _installed(_from_file(path))


def _installed(config: Config) -> Config:
    """Install the source timezone process-wide.

    Config load is the single place this happens: clock and logging read it from there, and
    nothing downstream gets to pick a zone of its own.
    """
    install_source_timezone(config.timezone)
    return config


def _required(path: Path, *, why: str) -> Path:
    if not path.is_file():
        raise ConfigError(f"configuration file named by {why} does not exist: {path}")
    return path


def _from_file(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read: {exc}") from exc

    _reject_unknown_keys(raw, path)
    paths = _section(raw, "paths", path)
    retention = _section(raw, "retention", path)
    source = _section(raw, "source", path)

    return Config(
        data_root=_absolute_path(paths, "data_root", where="paths", path=path),
        documents_root=_absolute_path(paths, "documents_root", where="paths", path=path),
        timezone=_timezone(
            _string(source, "timezone", DEFAULT_TIMEZONE, where="source", path=path)
        ),
        retention_days=_positive_int(
            retention, "days", DEFAULT_RETENTION_DAYS, where="retention", path=path
        ),
        min_request_interval=_positive_float(
            source, "min_request_interval", DEFAULT_MIN_REQUEST_INTERVAL, where="source", path=path
        ),
        max_requests_per_run=_positive_int(
            source, "max_requests_per_run", DEFAULT_MAX_REQUESTS_PER_RUN, where="source", path=path
        ),
        origin=path,
    )


def _reject_unknown_keys(raw: Mapping[str, Any], path: Path) -> None:
    unknown_sections = sorted(set(raw) - set(_SCHEMA))
    if unknown_sections:
        raise ConfigError(f"{path}: unknown section(s): {', '.join(unknown_sections)}")
    for name, allowed in _SCHEMA.items():
        section = raw.get(name)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise ConfigError(f"{path}: [{name}] must be a table")
        unknown = sorted(set(section) - allowed)
        if unknown:
            raise ConfigError(f"{path}: unknown key(s) in [{name}]: {', '.join(unknown)}")


def _section(raw: Mapping[str, Any], name: str, path: Path) -> Mapping[str, Any]:
    section = raw.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: [{name}] must be a table")
    return section


def _absolute_path(section: Mapping[str, Any], key: str, *, where: str, path: Path) -> Path:
    if key not in section:
        raise ConfigError(f"{path}: [{where}] {key} is required")
    value = section[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: [{where}] {key} must be a non-empty string")
    resolved = Path(value).expanduser()
    if not resolved.is_absolute():
        raise ConfigError(
            f"{path}: [{where}] {key} must be an absolute path (got {value!r}); relative roots "
            "exist only in the built-in defaults, and only with a warning"
        )
    return resolved


def _string(section: Mapping[str, Any], key: str, default: str, *, where: str, path: Path) -> str:
    if key not in section:
        return default
    value = section[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: [{where}] {key} must be a non-empty string")
    return value


def _positive_int(
    section: Mapping[str, Any], key: str, default: int, *, where: str, path: Path
) -> int:
    if key not in section:
        return default
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{path}: [{where}] {key} must be an integer >= 1")
    return value


def _positive_float(
    section: Mapping[str, Any], key: str, default: float, *, where: str, path: Path
) -> float:
    if key not in section:
        return default
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"{path}: [{where}] {key} must be a number > 0")
    return float(value)


def _timezone(name: str) -> ZoneInfo:
    """Validate the source timezone. An unknown name refuses to start; it never falls back.

    A minimal Linux image ships no IANA database, which is why ``tzdata`` is a runtime
    dependency: without it this would crash before any error handling runs.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"unknown timezone {name!r}: {exc}") from exc
