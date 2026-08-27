"""The command-line surface: subcommands, flags, and the exit-code contract.

The canonical mode is one-shot: ``run`` does one complete pass and exits. There is no daemon
in the package — a periodic mode, if it ever exists, is built on top of the one-shot, outside
of it. Everything here is glue: parsing flags, loading the configuration, and translating the
exception hierarchy into the documented exit codes. Decisions live in the modules the
subcommands call.

``--config`` is accepted before or after the subcommand, because both spellings are natural
and refusing one of them is a papercut nobody needs. Every flag whose destination differs
from its option string carries an explicit ``metavar`` — without one, ``argparse`` leaks the
internal attribute name into the help text.

This is also the only layer that talks to a human mid-command: ``add`` numbers the candidates
of an ambiguous query and asks which one. The prompt is offered only when both streams are a
terminal — with input redirected, from cron, or in a pipeline there is nobody to answer, and
the ambiguity is refused with the list exactly as before. Nothing below the CLI ever prompts.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from co_docs_watcher import __version__
from co_docs_watcher.clock import Clock, window_ending
from co_docs_watcher.config import Config, load_config
from co_docs_watcher.cvm.cache import RegistryCache
from co_docs_watcher.cvm.registry import Registry, RegistryRecord
from co_docs_watcher.cvm.search import MatchKind, SearchResult
from co_docs_watcher.cvm.ticker import PrefixSource, company_prefix
from co_docs_watcher.errors import (
    AmbiguousQueryError,
    CaptchaRequiredError,
    CompanyError,
    ConfigError,
    ExitCode,
    RegistryError,
    SourceError,
    WatcherError,
    exit_code_for,
)
from co_docs_watcher.lock import RunLock
from co_docs_watcher.logging_setup import configure_logging
from co_docs_watcher.manifest.db import open_manifest
from co_docs_watcher.manifest.repo import Manifest
from co_docs_watcher.models import LocalState
from co_docs_watcher.pipeline import purge, reconcile, regenerate
from co_docs_watcher.run import execute_run, probe_source
from co_docs_watcher.scope.models import WatchedCompany
from co_docs_watcher.scope.resolver import Chooser, describe, resolve
from co_docs_watcher.scope.store import WatchList
from co_docs_watcher.summary import summary_lines
from co_docs_watcher.text import normalize_cvm_code, normalize_key

__all__ = ["build_parser", "main"]

logger = logging.getLogger(__name__)

#: The typed query flags of ``add`` and ``resolve``, and the match stages each one accepts.
#: A value found by a different stage than the flag named is a wrong company that happens to
#: exist, which is the worst kind of success.
_QUERY_FLAGS: tuple[tuple[str, tuple[MatchKind, ...]], ...] = (
    ("ticker", (MatchKind.TICKER,)),
    ("cvm_code", (MatchKind.CVM_CODE,)),
    ("cnpj", (MatchKind.CNPJ,)),
    ("name", (MatchKind.LEGAL_NAME, MatchKind.PREVIOUS_LEGAL_NAME)),
)


class _Cancelled(Exception):
    """The human was asked which company they meant and declined to say.

    Not a failure: nothing was going to be written, and the operator who typed Enter knows
    exactly what happened. It exists so that declining is distinguishable from a caller that
    was never able to choose — that one still gets the refusal and the candidate list.
    """


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, dispatch, and map whatever went wrong to the documented exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        # Logging is configured after the config loads (it needs the timezone), so this one
        # failure mode speaks plainly to stderr instead.
        print(f"error: {error}", file=sys.stderr)
        return int(ExitCode.INVALID_CONFIG)

    configure_logging(
        log_path=config.log_path,
        max_bytes=config.log_max_bytes,
        backups=config.log_backups,
    )
    try:
        return int(args.handler(config, args))
    except AmbiguousQueryError as error:
        logger.error("%s", error)
        for candidate in error.candidates:
            print(f"  {candidate}", file=sys.stderr)
        return int(exit_code_for(error))
    except WatcherError as error:
        logger.log(error.severity, "%s", error)
        return int(exit_code_for(error))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="co-docs-watcher",
        description="Reading queue for documents published on RAD/CVM by watched companies.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    _config_flag(parser, default=None)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    doctor = _command(commands, "doctor", _cmd_doctor, "check config, roots, timezone, source")
    del doctor

    add = _command(commands, "add", _cmd_add, "resolve a company and add it to the watch list")
    _query_arguments(add)

    listing = _command(commands, "list", _cmd_list, "show the watch list")
    listing.add_argument(
        "query", nargs="?", metavar="QUERY", help="narrow by prefix, CVM code, or name"
    )

    rm = _command(commands, "rm", _cmd_rm, "remove a company from the watch list")
    rm.add_argument("query", metavar="QUERY", help="prefix, CVM code, or part of the name")

    resolve_ = _command(
        commands, "resolve", _cmd_resolve, "show what `add` would write, without writing"
    )
    _query_arguments(resolve_)

    run = _command(commands, "run", _cmd_run, "one complete pass: the canonical mode")
    # A profile, never a number: the cron line carries which profile sweeps, and retuning a
    # window is a configuration edit, not a crontab edit.
    run.add_argument(
        "--monitor",
        action="store_true",
        help="sweep the narrow discovery.monitor_days window instead of discovery.days",
    )
    _command(commands, "reconcile", _cmd_reconcile, "repair what an interrupted run left")
    _command(commands, "purge", _cmd_purge, "delete what aged out of the window")
    _command(commands, "status", _cmd_status, "what the archive holds, without touching it")
    return parser


def _command(commands, name: str, handler, help_text: str) -> argparse.ArgumentParser:
    subparser = commands.add_parser(name, help=help_text, description=help_text)
    # SUPPRESS keeps a trailing `--config` from erasing one given before the subcommand.
    _config_flag(subparser, default=argparse.SUPPRESS)
    subparser.set_defaults(handler=handler)
    return subparser


def _config_flag(parser: argparse.ArgumentParser, *, default: object) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=default,
        metavar="PATH",
        help="configuration file; also $CO_WATCHER_CONFIG",
    )


def _query_arguments(parser: argparse.ArgumentParser) -> None:
    """One query, spelled either as a bare positional or as a typed flag."""
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "query",
        nargs="?",
        metavar="QUERY",
        help="ticker, CNPJ, CVM code, or part of a legal name",
    )
    group.add_argument("--ticker", metavar="TICKER", help="a trading code or its root")
    group.add_argument("--cvm-code", metavar="CODE", help="the numeric CVM code")
    group.add_argument("--cnpj", metavar="CNPJ", help="the CNPJ, punctuated or not")
    group.add_argument("--name", metavar="TEXT", help="part of the legal name, current or previous")


# --- Subcommand handlers. Each returns the exit code; errors travel as exceptions. ---


def _cmd_doctor(config: Config, args: argparse.Namespace) -> ExitCode:
    """Check everything a run depends on, and say what was found — all of it, always."""
    findings: list[tuple[bool, str]] = []
    origin = config.origin if config.origin is not None else "built-in defaults (warned above)"
    findings.append((True, f"config: {origin}"))
    findings.append(_root_finding("data root", config.data_root))
    findings.append(_root_finding("documents root", config.documents_root))
    findings.append(_root_finding("logs root", config.logs_root))
    findings.append((True, f"timezone: {config.timezone_name}"))
    findings.append(_process_zone_finding(config))
    for finding in _window_findings(config):
        findings.append((True, finding))

    watched: tuple[WatchedCompany, ...] | None = None
    try:
        watched = WatchList.load(config.watch_list_path).companies
    except WatcherError as error:
        findings.append((False, f"watch list: {error}"))
    else:
        findings.append((True, f"watch list: {len(watched)} company(ies)"))

    now = Clock.installed().now()
    cache = RegistryCache(config.registry_cache_root, max_age_days=config.registry_max_age_days)
    ages = ", ".join(
        f"{year} {'fresh' if cache.is_fresh(year, now=now) else 'stale or absent'}"
        for year in (now.year - 1, now.year)
    )
    findings.append((True, f"registry cache: {ages} (refreshed by `run` and `add`)"))
    if watched is not None:
        findings.extend(_drift_findings(config, watched, cache, now))

    captcha = False
    try:
        findings.append((True, f"source: {probe_source(config)}"))
    except CaptchaRequiredError as error:
        captcha = True
        findings.append((False, f"source: demanded a captcha ({error}); reduce frequency"))
    except (SourceError, OSError) as error:
        findings.append((False, f"source: {error}"))

    for good, finding in findings:
        print(f"{'ok  ' if good else 'FAIL'}  {finding}")
    if captcha:
        return ExitCode.CAPTCHA_REQUIRED
    if all(good for good, _ in findings):
        return ExitCode.CLEAN
    return ExitCode.PARTIAL_FAILURE


def _window_findings(config: Config) -> list[str]:
    """The two discovery windows, resolved against today, each named after its profile.

    Printed so that which days each of ``run`` and ``run --monitor`` sweeps is verifiable
    without spending a sweep on it.
    """
    today = Clock.installed().today()
    lines = []
    for label, days, command in (
        ("discovery window", config.discovery_days, "run"),
        ("monitor window", config.monitor_days, "run --monitor"),
    ):
        window = window_ending(today, days)
        lines.append(
            f"{label}: {window.first} .. {window.last} "
            f"({window.days} dates), swept by `{command}`"
        )
    return lines


def _drift_findings(
    config: Config,
    watched: tuple[WatchedCompany, ...],
    cache: RegistryCache,
    now: datetime,
) -> list[tuple[bool, str]]:
    """Every watched company whose stored entry differs from the cached registry.

    Read with ``refresh=False``: ``doctor`` diagnoses without going to the network, and a
    run right after it would refresh anyway. Drift never fails the command — the next run
    settles it, and a finding that turned a rename into a red line would train its reader
    to ignore red lines. Silence when everything agrees would be the wrong answer too, so
    agreement is a line of its own.
    """
    label = "watch list vs registry"
    if not watched:
        return [(True, f"{label}: nothing is watched, nothing to compare")]
    try:
        registry = cache.load(now=now, refresh=False)
    except RegistryError as error:
        return [(True, f"{label}: not compared ({error})")]

    lines: list[tuple[bool, str]] = []
    for company in watched:
        record = registry.by_cvm_code(company.cvm_code)
        if record is None:
            lines.append(
                (
                    True,
                    f"{label}: {company.cvm_code} is not in the cached registry (left "
                    "alone; a yearly package only holds companies that filed that year)",
                )
            )
            continue
        expected = company_prefix(record, overrides=config.prefix_overrides)
        if expected.value == company.prefix and record.legal_name == company.legal_name:
            continue
        derived = company_prefix(record)
        finding = (
            f"{label}: {company.cvm_code} stored as {company.prefix}/{company.legal_name}, "
            f"registry says {derived.value}/{record.legal_name}"
        )
        if expected.source is PrefixSource.OVERRIDE:
            finding += f"; [prefix_overrides] names the prefix {expected.value}"
        if expected.value != company.prefix:
            finding += (
                f" (the next run moves the prefix; {company.prefix}/ keeps the days "
                "already written)"
            )
        else:
            finding += f" (the next run updates the entry; the folder stays {company.prefix}/)"
        lines.append((True, finding))
    if not lines:
        return [(True, f"{label}: {len(watched)} stored entry(ies) agree with the cached registry")]
    return lines


def _process_zone_finding(config: Config) -> tuple[bool, str]:
    """Compare ``$TZ`` against ``source.timezone``, which is what the schedule fires in.

    Reported here because that is where the derivation becomes verifiable: the container
    exports ``TZ`` from ``source.timezone``, and a run that disagrees with it would fire the
    schedule in one zone while writing the archive in another. Nothing in the package reads
    ``TZ``, so an absent one costs nothing and says so.
    """
    process_zone = os.environ.get("TZ", "").strip()
    if not process_zone:
        return True, "process TZ: unset (nothing here reads it; a scheduler would use UTC)"
    if process_zone == config.timezone_name:
        return True, f"process TZ: {process_zone} (matches source.timezone)"
    return False, (
        f"process TZ: {process_zone} contradicts source.timezone={config.timezone_name}: "
        f"a schedule read from TZ fires in {process_zone} while the archive is written in "
        f"{config.timezone_name}"
    )


def _root_finding(label: str, root: Path) -> tuple[bool, str]:
    """A root exists (or is created now) and accepts a write. Probing is the only real test."""
    probe = root / ".doctor-probe"
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as error:
        return False, f"{label}: {root} is not writable: {error}"
    return True, f"{label}: {root} (writable)"


def _cmd_add(config: Config, args: argparse.Namespace) -> ExitCode:
    try:
        entry = _resolve_query(config, args, choose=_ask_which_company)
    except _Cancelled:
        print("cancelled: nothing was added")
        return ExitCode.CLEAN
    watch_list = WatchList.load(config.watch_list_path)
    if not watch_list.add(entry):
        print(f"already watched: {entry.cvm_code}  {entry.legal_name}")
        return ExitCode.CLEAN
    watch_list.save()
    print(f"added: {entry.cvm_code}  {entry.legal_name}  -> {entry.prefix}/")
    return ExitCode.CLEAN


def _cmd_resolve(config: Config, args: argparse.Namespace) -> ExitCode:
    entry = _resolve_query(config, args)
    for key, value in entry.to_mapping().items():
        print(f"{key}: {value}")
    return ExitCode.CLEAN


def _cmd_list(config: Config, args: argparse.Namespace) -> ExitCode:
    companies = WatchList.load(config.watch_list_path).companies
    if args.query:
        companies = tuple(_watch_list_matches(companies, args.query))
    if not companies:
        print("the watch list is empty" if not args.query else "nothing matches")
        return ExitCode.CLEAN
    for company in companies:
        print(f"{company.cvm_code}  {company.prefix:<12}  {company.legal_name}")
    return ExitCode.CLEAN


def _cmd_rm(config: Config, args: argparse.Namespace) -> ExitCode:
    watch_list = WatchList.load(config.watch_list_path)
    matches = _watch_list_matches(watch_list.companies, args.query)
    if not matches:
        raise CompanyError(f"nothing in the watch list matches {args.query!r}")
    if len(matches) > 1:
        raise AmbiguousQueryError(
            f"{args.query!r} matches {len(matches)} watched companies; "
            "narrow it down with the CVM code",
            candidates=[
                f"{company.cvm_code}  {company.prefix}  {company.legal_name}"
                for company in matches
            ],
        )
    removed = watch_list.remove(matches[0].cvm_code)
    watch_list.save()
    assert removed is not None
    print(f"removed: {removed.cvm_code}  {removed.legal_name}")
    return ExitCode.CLEAN


def _cmd_run(config: Config, args: argparse.Namespace) -> ExitCode:
    """One pass, and then the run consolidated into a table on stdout.

    The summary is printed for every run, scheduled ones included: it is the answer to "what
    did this run do?", and a recap only a terminal gets is one the container's log would have
    to be reconstructed from.
    """
    report = execute_run(config, monitor=args.monitor)
    for line in summary_lines(report):
        print(line)
    return report.exit_code


def _cmd_reconcile(config: Config, args: argparse.Namespace) -> ExitCode:
    with RunLock(config.lock_path):
        connection = open_manifest(config.manifest_path)
        try:
            manifest = Manifest.over(connection, Clock.installed())
            outcome = reconcile(
                manifest,
                documents_root=config.documents_root,
                staging_root=config.staging_root,
                max_attempts=config.max_document_attempts,
            )
            window = Clock.installed().window(config.retention_days)
            regenerate(
                manifest,
                inbox_root=config.inbox_root,
                window=window,
                modes=config.archive_modes,
            )
        finally:
            connection.close()
    return ExitCode.PARTIAL_FAILURE if outcome.failed else ExitCode.CLEAN


def _cmd_purge(config: Config, args: argparse.Namespace) -> ExitCode:
    with RunLock(config.lock_path):
        connection = open_manifest(config.manifest_path)
        try:
            manifest = Manifest.over(connection, Clock.installed())
            window = Clock.installed().window(config.retention_days)
            purge(
                manifest,
                documents_root=config.documents_root,
                inbox_root=config.inbox_root,
                window=window,
            )
            regenerate(
                manifest,
                inbox_root=config.inbox_root,
                window=window,
                modes=config.archive_modes,
            )
        finally:
            connection.close()
    return ExitCode.CLEAN


def _cmd_status(config: Config, args: argparse.Namespace) -> ExitCode:
    """What the archive holds — and, for whatever it still owes, why it owes it.

    A count of documents left in a non-terminal state answers "is anything missing?" and
    nothing else; the reason is one join away in the manifest, and requiring an operator to
    open SQLite to read it is what makes a stuck document look like a quiet market.
    """
    print(f"config: {config.origin if config.origin is not None else 'built-in defaults'}")
    print(f"data root: {config.data_root}")
    print(f"documents root: {config.documents_root}")
    print(f"log file: {config.log_path}")
    print(f"timezone: {config.timezone_name}")
    window = Clock.installed().window(config.retention_days)
    print(f"retention window: {window.first} .. {window.last} ({window.days} dates)")
    for line in _window_findings(config):
        print(line)
    print(f"watched companies: {len(WatchList.load(config.watch_list_path).companies)}")

    if not config.manifest_path.exists():
        print("manifest: none yet — no run has completed")
        return ExitCode.CLEAN
    connection = open_manifest(config.manifest_path)
    try:
        manifest = Manifest.over(connection, Clock.installed())
        counts = manifest.documents.count_by_state()
        total = sum(counts.values())
        states = ", ".join(
            f"{count} {state}" for state, count in counts.items() if count
        )
        print(f"documents: {total}" + (f" ({states})" if states else ""))
        _print_pending(manifest)
        watermark = manifest.state.watermark()
        print(f"last completed sweep: {watermark if watermark is not None else 'never'}")
    finally:
        connection.close()
    return ExitCode.CLEAN


#: What the archive still owes: queued or waiting to be retried, interrupted mid-download, or
#: given up on. Every other state is settled, and settled states have nothing to explain.
_PENDING_STATES = (LocalState.DISCOVERED, LocalState.DOWNLOADING, LocalState.FAILED)


def _print_pending(manifest: Manifest) -> None:
    """List what is not on disk yet, each with the last failure recorded against it."""
    pending = manifest.documents.in_state(*_PENDING_STATES)
    if not pending:
        return
    print(f"pending ({len(pending)}):")
    for record in pending:
        document = record.document
        print(
            f"  ({document.document_id}, {document.version}) {record.local_state} "
            f"{document.cvm_code} {document.category} delivered {document.delivery_date}"
        )
        print(f"    {_last_failure(manifest, record.identity)}")


def _last_failure(manifest: Manifest, identity: tuple[int, int]) -> str:
    """The reason, in the words of the exception that produced it.

    Kept verbatim and untruncated: a message that has been shortened to fit is one an
    operator has to go and read in full anyway, which is the trip this line exists to save.
    """
    failure = manifest.attempts.last_failure(identity)
    if failure is None:
        return "not attempted yet"
    attempts = manifest.attempts.lifetime_failures(identity)
    when = failure.at.isoformat(sep=" ", timespec="seconds")
    return (
        f"{attempts} failed attempt(s), last {when}: "
        f"{failure.detail or 'no detail was recorded'}"
    )


# --- Query plumbing shared by add, resolve and rm. ---


def _resolve_query(
    config: Config, args: argparse.Namespace, *, choose: Chooser | None = None
) -> WatchedCompany:
    """Resolve the query against the registry, honoring the flag the human typed it under."""
    query, kinds = _typed_query(args)
    entry = resolve(
        _registry(config),
        query,
        overrides=config.prefix_overrides,
        choose=choose if _has_a_human() else None,
    )
    if kinds is not None and entry.matched_by not in kinds:
        raise CompanyError(
            f"{query!r} did not match as typed: it was found by {entry.matched_by}, "
            f"not by {' or '.join(str(kind) for kind in kinds)} "
            f"({entry.cvm_code}  {entry.legal_name})"
        )
    return entry


def _ask_which_company(query: str, result: SearchResult) -> RegistryRecord:
    """Number the candidates and let the human settle it. Cancelling is one of the answers.

    Only reachable with a terminal on both ends, so it may read from stdin and loop until the
    answer is one of the offered numbers. A typo is re-asked rather than resolved generously:
    the whole point of asking is that guessing here registers the wrong company.
    """
    candidates = result.matches
    print(f"{query!r} matches {len(candidates)} companies by {result.kind}:\n")
    width = len(str(len(candidates)))
    for number, record in enumerate(candidates, start=1):
        print(f"  {number:>{width}}  {describe(record)}")
    print()

    while True:
        try:
            answer = input(f"choose 1-{len(candidates)}, or Enter to cancel: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise _Cancelled from None
        if not answer:
            raise _Cancelled
        if answer.isdigit() and 1 <= int(answer) <= len(candidates):
            return candidates[int(answer) - 1]
        print(f"  {answer!r} is not one of the choices")


def _has_a_human() -> bool:
    """Whether there is somebody at the other end to answer a question.

    Both streams, because the prompt is only useful if it can be both read and answered: a
    ``co-docs-watcher add ... | tee log`` still has a terminal on stdin, and a prompt nobody
    sees is a command that hangs. Cron and pipelines get the refusal instead, which is the
    behaviour every non-interactive caller already relies on.
    """
    try:
        return sys.stdin is not None and sys.stdin.isatty() and sys.stdout.isatty()
    except ValueError:  # a closed stream — no terminal, by any definition
        return False


def _typed_query(args: argparse.Namespace) -> tuple[str, tuple[MatchKind, ...] | None]:
    for attribute, kinds in _QUERY_FLAGS:
        value = getattr(args, attribute)
        if value:
            return value, kinds
    return args.query, None


def _registry(config: Config) -> Registry:
    """The merged registry, refreshed if stale. Failure here blocks `add` and only `add`."""
    cache = RegistryCache(config.registry_cache_root, max_age_days=config.registry_max_age_days)
    return cache.load(now=Clock.installed().now())


def _watch_list_matches(
    companies: Sequence[WatchedCompany], query: str
) -> list[WatchedCompany]:
    """Match a query against what is *watched* — prefix, CVM code, or legal-name substring.

    ``rm`` and ``list`` resolve against the watch list rather than the registry: what is being
    named is an entry this archive already has, and the registry may not even be cached.
    """
    code = normalize_cvm_code(query)
    key = normalize_key(query)
    prefix = query.strip().upper()
    return [
        company
        for company in companies
        if company.prefix.upper() == prefix
        or (code and company.cvm_code == code)
        or (key and key in normalize_key(company.legal_name))
    ]
