"""Exception hierarchy.

Every module raises from here, and the hierarchy encodes three orthogonal distinctions the
rest of the design depends on:

*Fatal to the batch or isolated to one item.* An isolated failure — a company that cannot be
resolved, a document that cannot be downloaded — is recorded and skipped; it never kills the
run. ``ItemError`` and its subclasses are the only ones with ``batch_fatal = False``.

*Retryable or terminal.* ``TransientSourceError`` is the source failing under load and is the
only error worth backing off on. ``CaptchaRequiredError`` is the opposite: the source demanded
a captcha, there is no legitimate workaround, and insisting aggravates the trigger.

*Severity.* The ladder is fixed: ``WARNING`` transient and retryable, ``ERROR`` needs human
action, ``CRITICAL`` the source contract probably changed.

The error to exit-code mapping lives here as data (``ExitCode`` and ``exit_code_for``) and is
consumed by ``cli.py``, so that the codes documented for operators have exactly one definition.
"""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import ClassVar

__all__ = [
    "CaptchaRequiredError",
    "CompanyError",
    "ConfigError",
    "DocumentError",
    "ExitCode",
    "IllegalTransitionError",
    "ItemError",
    "LockHeldError",
    "ManifestError",
    "SchemaTooNewError",
    "SourceContractError",
    "SourceError",
    "TransientSourceError",
    "WatcherError",
    "exit_code_for",
]


class ExitCode(IntEnum):
    """Process exit codes. The operator-facing contract of the CLI."""

    CLEAN = 0
    PARTIAL_FAILURE = 1
    INVALID_CONFIG = 2
    LOCK_HELD = 3
    CAPTCHA_REQUIRED = 4


class WatcherError(Exception):
    """Base of every error this package raises deliberately.

    The class attributes are the contract; subclasses narrow them. A bare ``WatcherError``
    reaching the CLI exits ``PARTIAL_FAILURE``: the run did not complete cleanly, but it is
    neither a configuration problem nor one of the two conditions that own a code of their own.
    """

    exit_code: ClassVar[ExitCode] = ExitCode.PARTIAL_FAILURE
    severity: ClassVar[int] = logging.ERROR
    retryable: ClassVar[bool] = False
    batch_fatal: ClassVar[bool] = True


class ConfigError(WatcherError):
    """Configuration is absent, unreadable, or invalid.

    Raised for malformed TOML, roots that are not absolute, and invalid timezone names. The
    watcher refuses to start rather than falling back to something the operator did not ask
    for: a silent fallback means operating on a different archive than intended.
    """

    exit_code: ClassVar[ExitCode] = ExitCode.INVALID_CONFIG
    severity: ClassVar[int] = logging.ERROR


class LockHeldError(WatcherError):
    """Another instance holds the lock on ``data_root``.

    Not a defect: overlapping invocations are expected when a run takes longer than the
    interval between them. The second instance exits immediately, and the next invocation
    will find the lock free — the kernel releases it when the owner dies.
    """

    exit_code: ClassVar[ExitCode] = ExitCode.LOCK_HELD
    severity: ClassVar[int] = logging.WARNING


class SourceError(WatcherError):
    """Base for everything the source does to us."""


class TransientSourceError(SourceError):
    """The source failed in a way that a later attempt may survive.

    Covers ``temErro: true`` responses, connection failures, and timeouts. It is **never**
    interpretable as an empty result: HTTP is always 200 on this source, so a robot that reads
    a backend failure as "nothing new" records silence as good news. Backoff applies here and
    only here.
    """

    severity: ClassVar[int] = logging.WARNING
    retryable: ClassVar[bool] = True


class CaptchaRequiredError(SourceError):
    """The source answered ``SolicitarCaptcha: "S"``.

    Terminal: never retried, never backed off. There is no legitimate workaround, and
    insisting aggravates the trigger. The run ends with exit code ``4`` and the operator
    reduces frequency.
    """

    exit_code: ClassVar[ExitCode] = ExitCode.CAPTCHA_REQUIRED
    severity: ClassVar[int] = logging.ERROR


class SourceContractError(SourceError):
    """The wire format is not what this build knows how to read.

    A row without exactly 12 fields, an envelope that is no longer JSON, a download whose
    content signature matches nothing. The system is recently migrated: divergence is loud and
    the collection is aborted, because a partially understood payload is worse than none.
    """

    severity: ClassVar[int] = logging.CRITICAL


class ManifestError(WatcherError):
    """The local manifest cannot be used as it stands."""


class SchemaTooNewError(ManifestError):
    """The manifest on disk was written by a newer build.

    It refuses to open rather than degrading: an older build silently reading a newer schema
    would write rows the newer one cannot interpret, and the archive would be the only place
    the damage shows. The operator upgrades the build or points at another ``data_root``.
    """

    exit_code: ClassVar[ExitCode] = ExitCode.INVALID_CONFIG


class IllegalTransitionError(ManifestError):
    """A local state transition the state machine does not allow.

    The machine is closed on purpose: an ``available`` document must never walk back to
    ``discovered``, because that is the shape of a re-download loop that grows the archive by
    the same document, every run, forever.
    """


class ItemError(WatcherError):
    """A failure scoped to a single item, recorded and skipped.

    An isolated failure never kills the batch. Raise these deep, catch them at the loop that
    owns the batch, record them, and carry on; the run ends ``PARTIAL_FAILURE``.
    """

    batch_fatal: ClassVar[bool] = False


class DocumentError(ItemError):
    """One document could not be fetched, validated, or written."""


class CompanyError(ItemError):
    """One company could not be resolved against the registry."""


def exit_code_for(error: BaseException) -> ExitCode:
    """Map an exception to the process exit code.

    Anything that is not a ``WatcherError`` is an unforeseen failure and exits
    ``PARTIAL_FAILURE``: the run did not complete, and no operator-facing code claims it.
    """
    if isinstance(error, WatcherError):
        return error.exit_code
    return ExitCode.PARTIAL_FAILURE
