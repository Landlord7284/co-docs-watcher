"""Text normalization shared by identity, search and naming.

Two different jobs live here, and they must not be confused. *Normalizing an identifier*
(``normalize_cvm_code``, ``normalize_cnpj``) turns the many spellings the regulator
uses for the same number into the one spelling this system stores. *Sanitizing a name* turns
free text into something a filesystem and a human can both read.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "CVM_CODE_DIGITS",
    "CVM_CODE_RULE",
    "MAX_NAME_COMPONENT",
    "PREFIX_RULE",
    "normalize_cnpj",
    "normalize_cvm_code",
    "normalize_key",
    "reduce_legal_name",
    "safe_component",
    "strip_accents",
]

#: The CVM code is six digits, zero-padded. The listing sends ``00951-2``, the payload of a
#: search expects ``009512``, and the registry publishes ``009512``: one spelling is stored.
CVM_CODE_DIGITS = 6

#: What a CVM code looks like when someone writes one down: the digits, or the spelling with
#: the check digit split off that the listing uses. It is checked *before* the value reaches
#: ``normalize_cvm_code``, because that function drops whatever is not a digit and therefore
#: answers free text with a code rather than with nothing — ``PETR4`` reduces to company
#: ``000004``. That generosity is what makes a typed query forgiving; reading an identifier
#: out of a file is where it has to stop.
CVM_CODE_RULE = re.compile(rf"^\d{{1,{CVM_CODE_DIGITS}}}$|^\d{{1,{CVM_CODE_DIGITS - 1}}}-\d$")

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


#: How long a name derived from free text may be. Long enough to stay recognizable, short
#: enough that a path built from several of them stays readable in a terminal.
MAX_NAME_COMPONENT = 24

#: Words that say what a company *is* rather than which company it is. Dropping them is what
#: turns ``PLASCAR PARTICIPACOES INDUSTRIAIS S.A.`` into a name a human scans in a directory
#: listing. Single letters go with them: ``S.A.`` and ``S/A`` survive tokenization as ``S`` and
#: ``A``.
_LEGAL_FORMS = frozenset({"SA", "SAS", "LTDA", "CIA", "COMPANHIA", "EIRELI", "ME", "MEI", "EPP"})

#: Judicial and administrative states a company's registered name carries around. They belong
#: to the situation, not to the identity, and they change while the company does not.
_SITUATIONS = re.compile(r"\bEM (RECUPERACAO|LIQUIDACAO|FALENCIA)\b.*$")

_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]+")

#: What a finished folder name looks like, derived from the one length limit above. It is the
#: shape ``safe_component`` produces *and* the shape an operator's override is checked against,
#: and it is defined once because those two must not be able to disagree: a rule looser than
#: the producer would let a configured name through that the archive then quietly shortens.
PREFIX_RULE = re.compile(rf"^[A-Z0-9][A-Z0-9-]{{0,{MAX_NAME_COMPONENT - 1}}}$")


def strip_accents(text: str) -> str:
    """Drop diacritics, keeping the letters underneath: ``PARTICIPAÇÕES`` → ``PARTICIPACOES``."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def normalize_key(text: str) -> str:
    """The comparison form used for searching: accent-free, upper case, single-spaced.

    Search is the one place where the registry's own spelling must not matter. A human types
    ``sao martinho``; the registry says ``SÃO MARTINHO``; both reduce to the same key.
    """
    folded = strip_accents(text).upper()
    return " ".join(_NON_ALPHANUMERIC.sub(" ", folded).split())


def safe_component(text: str, *, max_length: int = MAX_NAME_COMPONENT) -> str:
    """Turn free text into one path component: upper case, ``A-Z0-9`` and hyphens only.

    Truncation happens at a word boundary whenever one fits, because a name cut mid-word reads
    like a bug in the archive rather than a deliberate limit.
    """
    words = normalize_key(text).split()
    if not words:
        return ""
    kept: list[str] = []
    length = 0
    for word in words:
        addition = len(word) + (1 if kept else 0)
        if kept and length + addition > max_length:
            break
        kept.append(word)
        length += addition
    if not kept:
        return words[0][:max_length]
    return "-".join(kept)[:max_length]


def reduce_legal_name(name: str, *, max_length: int = MAX_NAME_COMPONENT) -> str:
    """Reduce a registered legal name to the part that identifies the company.

    This is the second step of the folder-name fallback chain, reached when the registry's
    trading code is junk or absent. It is not a rename: the result is a label for a directory,
    and the company's identity stays in the manifest.
    """
    folded = _SITUATIONS.sub("", normalize_key(name))
    words = [
        word for word in folded.split() if len(word) > 1 and word not in _LEGAL_FORMS
    ]
    return safe_component(" ".join(words), max_length=max_length)
