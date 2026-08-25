"""The exception hierarchy is a contract: subclassing, severity, and exit codes are asserted."""

from __future__ import annotations

import logging

import pytest

from co_docs_watcher.errors import (
    CaptchaRequiredError,
    CompanyError,
    ConfigError,
    DocumentError,
    ExitCode,
    ItemError,
    LockHeldError,
    ManifestError,
    RegistryError,
    RegistryNotPublishedError,
    SchemaTooNewError,
    SourceContractError,
    SourceError,
    TransientSourceError,
    WatcherError,
    exit_code_for,
)

ALL_ERRORS = [
    WatcherError,
    ConfigError,
    LockHeldError,
    SourceError,
    TransientSourceError,
    CaptchaRequiredError,
    SourceContractError,
    ItemError,
    DocumentError,
    CompanyError,
    ManifestError,
    RegistryError,
    RegistryNotPublishedError,
    SchemaTooNewError,
]


@pytest.mark.parametrize("error_type", ALL_ERRORS)
def test_every_error_descends_from_the_base(error_type: type[Exception]) -> None:
    assert issubclass(error_type, WatcherError)


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        (WatcherError, ExitCode.PARTIAL_FAILURE),
        (ConfigError, ExitCode.INVALID_CONFIG),
        (LockHeldError, ExitCode.LOCK_HELD),
        (CaptchaRequiredError, ExitCode.CAPTCHA_REQUIRED),
        (TransientSourceError, ExitCode.PARTIAL_FAILURE),
        (SourceContractError, ExitCode.PARTIAL_FAILURE),
        (DocumentError, ExitCode.PARTIAL_FAILURE),
        (CompanyError, ExitCode.PARTIAL_FAILURE),
        (ManifestError, ExitCode.PARTIAL_FAILURE),
        (RegistryError, ExitCode.PARTIAL_FAILURE),
        (SchemaTooNewError, ExitCode.INVALID_CONFIG),
    ],
)
def test_exit_code_mapping(error_type: type[WatcherError], expected: ExitCode) -> None:
    assert error_type.exit_code is expected
    assert exit_code_for(error_type("boom")) is expected


def test_unforeseen_exceptions_map_to_partial_failure() -> None:
    assert exit_code_for(RuntimeError("boom")) is ExitCode.PARTIAL_FAILURE


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        (TransientSourceError, logging.WARNING),
        (LockHeldError, logging.WARNING),
        (ConfigError, logging.ERROR),
        (CaptchaRequiredError, logging.ERROR),
        (DocumentError, logging.ERROR),
        (CompanyError, logging.ERROR),
        (SourceContractError, logging.CRITICAL),
        (RegistryError, logging.ERROR),
        (RegistryNotPublishedError, logging.WARNING),
    ],
)
def test_severity_ladder(error_type: type[WatcherError], expected: int) -> None:
    assert error_type.severity == expected


def test_only_transient_source_errors_are_retryable() -> None:
    retryable = [error for error in ALL_ERRORS if error.retryable]
    assert retryable == [TransientSourceError]


def test_captcha_is_terminal() -> None:
    assert not CaptchaRequiredError.retryable
    assert CaptchaRequiredError.batch_fatal


@pytest.mark.parametrize("error_type", [ItemError, DocumentError, CompanyError])
def test_item_errors_never_kill_the_batch(error_type: type[WatcherError]) -> None:
    assert not error_type.batch_fatal


def test_a_registry_failure_blocks_registration_and_not_monitoring() -> None:
    # The watch list persists the resolved prefix, so a run needs no registry at all.
    assert not RegistryError.batch_fatal
    assert not RegistryNotPublishedError.batch_fatal


@pytest.mark.parametrize(
    "error_type",
    [WatcherError, ConfigError, LockHeldError, TransientSourceError, SourceContractError],
)
def test_non_item_errors_are_batch_fatal(error_type: type[WatcherError]) -> None:
    assert error_type.batch_fatal
