"""The ticker root, and the fallback chain that names a company's folder.

A folder is named after the root of the company's ticker — ``PETR4`` and ``PETR3`` are one
company, and ``PETR`` is what a human recognizes in a directory listing. The rule that extracts
it is deliberately strict, because the field it reads is free text: ``Codigo_Negociacao`` is
filled with lower case (``tgma3``), with junk (``B3``, ``NÃO HÁ``, bare numbers), and with
nothing at all for debentures and commercial notes. Anything that does not look like a ticker
is not treated as one.

The chain has three steps, and every step is worse than the one before it and better than
having no folder: the validated root, the reduced legal name, the zero-padded CVM code. An
operator who disagrees with the outcome overrides it in the configuration file — a company that
trades under two equally short roots is a matter of taste, not of correctness.

Everything here is pure. The folder name is a snapshot taken when a company is registered in
the watch list; nothing in this module renames a folder that already exists.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from co_docs_watcher.cvm.registry import RegistryRecord
from co_docs_watcher.text import PREFIX_RULE, normalize_cvm_code, reduce_legal_name

__all__ = [
    "BARE_ROOT_RULE",
    "TICKER_ROOT_RULE",
    "CompanyPrefix",
    "PrefixSource",
    "choose_root",
    "company_prefix",
    "ticker_root",
]

logger = logging.getLogger(__name__)

#: Group 1 is the root, group 2 the class digits: ``PETR4`` → ``PETR``, ``EQMA3B`` → ``EQMA``,
#: ``ENGI11`` → ``ENGI``, ``B3SA3`` → ``B3SA``.
TICKER_ROOT_RULE = re.compile(r"^([A-Z][A-Z0-9]{3,})(\d{1,2}[A-Z]?)$")

#: Codes with no class digit — ``LMED``, ``TEGA`` — already *are* the root.
BARE_ROOT_RULE = re.compile(r"^[A-Z]{4,5}$")


class PrefixSource(StrEnum):
    """Which step of the chain produced the folder name.

    Recorded in the watch list, because "why is this folder called ``009512``?" is a question
    with a good answer, and the answer stops being reconstructible once the registry moves on.
    """

    OVERRIDE = "override"
    TICKER = "ticker"
    LEGAL_NAME = "legal_name"
    CVM_CODE = "cvm_code"


@dataclass(frozen=True, slots=True)
class CompanyPrefix:
    """A folder name and the step of the chain that produced it."""

    value: str
    source: PrefixSource


def ticker_root(code: str) -> str | None:
    """The root of one trading code, or ``None`` when the text is not a trading code.

    Case is normalized because the registry is inconsistent about it and a ticker is upper
    case by convention; nothing else about the value is repaired. A code this rule rejects is
    a code the field should not have contained.
    """
    candidate = code.strip().upper()
    match = TICKER_ROOT_RULE.match(candidate)
    if match:
        return match.group(1)
    if BARE_ROOT_RULE.match(candidate):
        return candidate
    return None


def choose_root(codes: Iterable[str]) -> str | None:
    """Pick one root out of a company's trading codes: **the shorter root wins**.

    A company with more than one root is almost always a subscription-receipt pair —
    ``ENGI``/``ENGI1``, ``SAPR``/``SAPR1`` — where the longer root is the receipt and the
    shorter one is the company (12 of the 346 companies with a valid root, measured
    2026-08-24). Remaining ties break alphabetically so the choice is at least stable across
    runs; a company where that produces the wrong answer is what the override exists for.
    """
    roots = {root for root in map(ticker_root, codes) if root}
    if not roots:
        return None
    return min(roots, key=lambda root: (len(root), root))


def company_prefix(
    record: RegistryRecord, *, overrides: Mapping[str, str] | None = None
) -> CompanyPrefix:
    """The folder name for a company: override, then ticker root, then name, then code.

    An override is taken **verbatim**. It is validated when the configuration loads, against
    the same ``PREFIX_RULE`` the rest of the chain satisfies, and repairing one here instead
    would name a folder after something nobody wrote — silently shortening a long name, or
    reducing one written in punctuation to the empty string. A value that reaches this
    function without having been validated is refused rather than repaired, and the company
    falls back to the step it would have used had no override existed.
    """
    override = (overrides or {}).get(normalize_cvm_code(record.cvm_code))
    if override and PREFIX_RULE.match(override):
        return CompanyPrefix(override, PrefixSource.OVERRIDE)
    if override:
        logger.error(
            "prefix: the override %r for CVM code %s is not a usable folder name and is "
            "ignored; the configuration is what validates it",
            override,
            record.cvm_code,
        )

    root = choose_root(record.trading_codes)
    if root:
        return CompanyPrefix(root, PrefixSource.TICKER)

    reduced = reduce_legal_name(record.legal_name)
    if reduced:
        return CompanyPrefix(reduced, PrefixSource.LEGAL_NAME)

    return CompanyPrefix(normalize_cvm_code(record.cvm_code), PrefixSource.CVM_CODE)
