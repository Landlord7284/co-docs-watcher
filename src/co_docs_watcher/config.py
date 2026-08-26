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

Roots may be written relative, and are then resolved against the directory of the
configuration file itself — a project-local installation is a checkout with a ``config.toml``
naming ``var/data``, ``var/documents`` and ``var/logs``, and it archives beside that file
wherever the command is typed from.

Anything invalid — unreadable TOML, an unknown key, a timezone name the system does not
know — raises ``ConfigError`` and the CLI exits ``2``.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from co_docs_watcher.clock import install_source_timezone
from co_docs_watcher.errors import ConfigError
from co_docs_watcher.text import normalize_cvm_code

__all__ = [
    "CONFIG_ENV_VAR",
    "DEFAULT_LOG_BACKUPS",
    "DEFAULT_LOG_MAX_BYTES",
    "DEFAULT_MAX_REQUESTS_PER_RUN",
    "DEFAULT_MIN_REQUEST_INTERVAL",
    "DEFAULT_MONITOR_DAYS",
    "DEFAULT_REGISTRY_MAX_AGE_DAYS",
    "DEFAULT_RETENTION_DAYS",
    "DEFAULT_SOURCE_BASE_URL",
    "DEFAULT_TIMEZONE",
    "Config",
    "discover_config_path",
    "load_config",
]

logger = logging.getLogger(__name__)

CONFIG_ENV_VAR = "CO_WATCHER_CONFIG"

#: Timezone of the source. Delivery dates, "today", and directory names are all read in it.
DEFAULT_TIMEZONE = "America/Sao_Paulo"

#: Where the RAD front end lives. Overridden to point a test server or a mirror; the adapter
#: keeps its own copy of this default, because nothing outside ``rad/`` may import from it.
DEFAULT_SOURCE_BASE_URL = "https://www.rad.cvm.gov.br/ENETWeb/"

#: Retained dates, counting today. ``first_retained_date = today - (N - 1)``.
DEFAULT_RETENTION_DAYS = 7

#: Days swept by ``run --monitor``, counting today. Two, because a document delivered late
#: yesterday must still be caught by a monitor that last ran before it arrived.
DEFAULT_MONITOR_DAYS = 2

#: Seconds between requests. The backend behind the page drops under load; this is the floor.
DEFAULT_MIN_REQUEST_INTERVAL = 15.0

#: Safety fuse: a single run never issues more requests than this, whatever it still has to do.
DEFAULT_MAX_REQUESTS_PER_RUN = 200

#: Days a cached FCA package is considered current. The registry only moves when a company
#: files a registration form, so a week-old snapshot names companies exactly as today's does.
DEFAULT_REGISTRY_MAX_AGE_DAYS = 7

#: Relative and deliberately unusable-by-accident: reaching these means the warning fired.
DEFAULT_DATA_ROOT = Path("var/data")
DEFAULT_DOCUMENTS_ROOT = Path("var/documents")
DEFAULT_LOGS_ROOT = Path("var/logs")

#: The one file under ``logs_root``. Named after the program rather than after the run, so
#: that the rotation below is the only thing that ever creates a second file there.
LOG_FILE_NAME = "co-docs-watcher.log"

#: Bytes the log file reaches before it is rotated, and how many rotations are kept. A
#: watcher writes a few dozen lines per run, so the defaults hold months of history; what
#: they buy is the guarantee that an unattended archive never fills its filesystem with log.
DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 5

#: Folder-name overrides, keyed by CVM code. The keys are data, not schema, so this section is
#: the one place where an unknown key is not a typo.
PREFIX_OVERRIDES_SECTION = "prefix_overrides"

#: What an override may look like: a folder name, in the same charset the resolver produces.
_PREFIX_RULE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")

_SCHEMA: dict[str, set[str]] = {
    "paths": {"data_root", "documents_root", "logs_root"},
    "logging": {"max_bytes", "backups"},
    "retention": {"days"},
    "discovery": {"days", "monitor_days"},
    "registry": {"max_age_days"},
    "source": {"timezone", "min_request_interval", "max_requests_per_run", "base_url"},
}


@dataclass(frozen=True, slots=True)
class Config:
    """The loaded configuration.

    ``data_root`` is private — YAML watch list, SQLite manifest, lock, registry cache — and
    must live on a filesystem local to the process: SQLite locking over SMB/NFS is unreliable.
    ``documents_root`` is the shareable archive, and holds ``.tmp/`` so that placement stays an
    atomic ``rename`` within one filesystem.

    ``logs_root`` holds the log file. It is a root of its own rather than a directory under
    either of the other two: the log is neither private state the watcher reads back nor part
    of the archive people are given, and an operator who mounts the three somewhere else
    mounts them separately. Logging to the streams is unconditional; the file is a copy.

    ``origin`` is the file the values came from, or ``None`` when the built-in defaults were
    used — which is what makes "am I looking at the archive I think I am?" answerable.
    """

    data_root: Path
    documents_root: Path
    logs_root: Path
    log_max_bytes: int
    log_backups: int
    timezone: ZoneInfo
    retention_days: int
    discovery_days: int
    monitor_days: int
    min_request_interval: float
    max_requests_per_run: int
    registry_max_age_days: int
    source_base_url: str
    prefix_overrides: Mapping[str, str]
    origin: Path | None

    @property
    def timezone_name(self) -> str:
        return str(self.timezone.key)

    def sweep_days(self, *, monitor: bool) -> int:
        """How many days the requested profile sweeps.

        The profile selects which configured integer becomes the discovery window and
        nothing else — answered here so that no caller does the arithmetic, and no
        ``if monitor:`` exists below the CLI.
        """
        return self.monitor_days if monitor else self.discovery_days

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
    def registry_cache_root(self) -> Path:
        return self.data_root / "cvm-cache"

    @property
    def watch_list_path(self) -> Path:
        return self.data_root / "companies.yaml"

    @property
    def staging_root(self) -> Path:
        return self.documents_root / ".tmp"

    @property
    def inbox_root(self) -> Path:
        return self.documents_root / "_inbox"

    @property
    def log_path(self) -> Path:
        return self.logs_root / LOG_FILE_NAME


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
            logs_root=cwd / DEFAULT_LOGS_ROOT,
            log_max_bytes=DEFAULT_LOG_MAX_BYTES,
            log_backups=DEFAULT_LOG_BACKUPS,
            timezone=_timezone(DEFAULT_TIMEZONE),
            retention_days=DEFAULT_RETENTION_DAYS,
            discovery_days=DEFAULT_RETENTION_DAYS,
            monitor_days=DEFAULT_MONITOR_DAYS,
            min_request_interval=DEFAULT_MIN_REQUEST_INTERVAL,
            max_requests_per_run=DEFAULT_MAX_REQUESTS_PER_RUN,
            registry_max_age_days=DEFAULT_REGISTRY_MAX_AGE_DAYS,
            source_base_url=DEFAULT_SOURCE_BASE_URL,
            prefix_overrides={},
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
    logging_section = _section(raw, "logging", path)
    retention = _section(raw, "retention", path)
    discovery = _section(raw, "discovery", path)
    registry = _section(raw, "registry", path)
    source = _section(raw, "source", path)

    retention_days = _positive_int(
        retention, "days", DEFAULT_RETENTION_DAYS, where="retention", path=path
    )
    discovery_days, monitor_days = _discovery_windows(
        discovery, retention_days=retention_days, path=path
    )

    return Config(
        data_root=_root_path(paths, "data_root", where="paths", path=path),
        documents_root=_root_path(paths, "documents_root", where="paths", path=path),
        logs_root=_root_path(paths, "logs_root", where="paths", path=path),
        log_max_bytes=_positive_int(
            logging_section, "max_bytes", DEFAULT_LOG_MAX_BYTES, where="logging", path=path
        ),
        log_backups=_positive_int(
            logging_section, "backups", DEFAULT_LOG_BACKUPS, where="logging", path=path
        ),
        timezone=_timezone(
            _string(source, "timezone", DEFAULT_TIMEZONE, where="source", path=path)
        ),
        retention_days=retention_days,
        discovery_days=discovery_days,
        monitor_days=monitor_days,
        min_request_interval=_positive_float(
            source, "min_request_interval", DEFAULT_MIN_REQUEST_INTERVAL, where="source", path=path
        ),
        max_requests_per_run=_positive_int(
            source, "max_requests_per_run", DEFAULT_MAX_REQUESTS_PER_RUN, where="source", path=path
        ),
        registry_max_age_days=_positive_int(
            registry,
            "max_age_days",
            DEFAULT_REGISTRY_MAX_AGE_DAYS,
            where="registry",
            path=path,
        ),
        source_base_url=_http_url(
            _string(source, "base_url", DEFAULT_SOURCE_BASE_URL, where="source", path=path),
            key="base_url",
            where="source",
            path=path,
        ),
        prefix_overrides=_prefix_overrides(
            _section(raw, PREFIX_OVERRIDES_SECTION, path), path=path
        ),
        origin=path,
    )


def _reject_unknown_keys(raw: Mapping[str, Any], path: Path) -> None:
    unknown_sections = sorted(set(raw) - set(_SCHEMA) - {PREFIX_OVERRIDES_SECTION})
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


def _discovery_windows(
    section: Mapping[str, Any], *, retention_days: int, path: Path
) -> tuple[int, int]:
    """The two sweep widths, ordered ``1 <= monitor_days <= days <= retention.days``.

    ``days`` follows ``retention.days`` when unset, so a file that names neither keeps a
    single window. The upper bound is refused rather than warned about: a discovery window
    wider than retention downloads on Wednesday what purge deleted on Tuesday, every week,
    forever. ``_positive_int`` supplies the lower bound.
    """
    discovery_days = _positive_int(
        section, "days", retention_days, where="discovery", path=path
    )
    # The default accommodates a narrow window — a one-day archive names no monitor_days and
    # must stay valid — but only the default: a written value is validated, never clamped.
    monitor_days = _positive_int(
        section,
        "monitor_days",
        min(DEFAULT_MONITOR_DAYS, discovery_days),
        where="discovery",
        path=path,
    )
    if discovery_days > retention_days:
        raise ConfigError(
            f"{path}: [discovery] days ({discovery_days}) exceeds [retention] days "
            f"({retention_days}): a sweep wider than retention re-downloads what purge deletes"
        )
    if monitor_days > discovery_days:
        raise ConfigError(
            f"{path}: [discovery] monitor_days ({monitor_days}) exceeds [discovery] days "
            f"({discovery_days}): the monitor is the narrower profile"
        )
    return discovery_days, monitor_days


def _prefix_overrides(section: Mapping[str, Any], *, path: Path) -> dict[str, str]:
    """Read the folder-name overrides, keyed by CVM code.

    Validated rather than sanitized: an override is a deliberate act, and quietly repairing a
    malformed one would name a folder after something the operator did not write.
    """
    overrides: dict[str, str] = {}
    for key, value in section.items():
        code = normalize_cvm_code(str(key))
        if not code or len(code) > 6:
            raise ConfigError(
                f"{path}: [{PREFIX_OVERRIDES_SECTION}] {key!r} is not a CVM code; the keys of "
                "this section are the companies the override applies to"
            )
        if not isinstance(value, str) or not _PREFIX_RULE.match(value.strip().upper()):
            raise ConfigError(
                f"{path}: [{PREFIX_OVERRIDES_SECTION}] {key} must be a folder name of letters, "
                f"digits and hyphens (got {value!r})"
            )
        overrides[code] = value.strip().upper()
    return overrides


def _section(raw: Mapping[str, Any], name: str, path: Path) -> Mapping[str, Any]:
    section = raw.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: [{name}] must be a table")
    return section


def _root_path(section: Mapping[str, Any], key: str, *, where: str, path: Path) -> Path:
    """A root directory, always returned absolute.

    A relative root is anchored on the **directory holding the configuration file**, never on
    the current one: that is what makes a project-local installation portable — clone, edit
    nothing, run, and the archive appears beside the configuration that named it. Anchoring on
    the working directory instead would make the same file mean a different archive depending
    on where the command was typed, and a run from cron would quietly build a second, empty
    archive somewhere else.
    """
    if key not in section:
        raise ConfigError(f"{path}: [{where}] {key} is required")
    value = section[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: [{where}] {key} must be a non-empty string")
    resolved = Path(value).expanduser()
    if not resolved.is_absolute():
        # ``path`` itself can be relative — ``--config config.toml`` is the common spelling —
        # so the anchor is resolved before joining: a root is absolute by the time anything
        # downstream sees it, whatever the command line looked like.
        resolved = path.resolve().parent / resolved
    return Path(os.path.normpath(resolved))


def _http_url(value: str, *, key: str, where: str, path: Path | None) -> str:
    """An http(s) URL. Anything else is a typo that would only surface as a network error."""
    if not value.startswith(("http://", "https://")):
        origin = f"{path}: " if path is not None else ""
        raise ConfigError(f"{origin}[{where}] {key} must be an http:// or https:// URL")
    return value


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
