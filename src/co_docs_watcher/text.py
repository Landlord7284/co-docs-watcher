"""Text normalization shared by identity, search and naming.

Two different jobs live here, and they must not be confused. *Normalizing an identifier*
(``normalize_cvm_code``, ``normalize_cnpj``) turns the many spellings the regulator
uses for the same number into the one spelling this system stores. *Sanitizing a name* turns
free text into something a filesystem and a human can both read.
"""

from __future__ import annotations

import re

__all__ = [
    "CVM_CODE_DIGITS",
    "normalize_cnpj",
    "normalize_cvm_code",
]

#: The CVM code is six digits, zero-padded. The listing sends ``00951-2``, the payload of a
#: search expects ``009512``, and the registry publishes ``009512``: one spelling is stored.
CVM_CODE_DIGITS = 6

_NON_DIGITS = re.compile(r"\D+")


def normalize_cvm_code(value: str | int) -> str:
    """Return the CVM code as six zero-padded digits, or ``""`` when there is none.

    Punctuation is dropped rather than interpreted: ``00951-2``, ``9512`` and ``009512`` are
    the same company, and a code longer than six digits is returned as-is so that a source
    that grows a digit shows up as a visible oddity instead of a silent truncation.
    """
    digits = _NON_DIGITS.sub("", str(value))
    if not digits:
        return ""
    return digits.zfill(CVM_CODE_DIGITS)


def normalize_cnpj(value: str) -> str:
    """Return the fourteen digits of a CNPJ, or ``""`` when the text holds no usable number.

    The registry formats it (``00.000.000/0001-91``) and humans type it either way. Anything
    that is not fourteen digits is rejected as free text: half-typed numbers must fail the
    match rather than find the wrong company.
    """
    digits = _NON_DIGITS.sub("", value)
    return digits if len(digits) == 14 else ""
