"""What the watch list stores about a company, and how it survives a round trip through YAML.

An entry is a *decision*, not a copy of the registry. It records the CVM code the sweep is
filtered against, the folder prefix the archive is built with, and how both were arrived at —
which query stage found the company and which step of the fallback chain named it. The
registry moves; the archive on disk does not, and months later "why is this folder called
``009512``?" has to be answerable without re-running anything.

``legal_name`` is carried along for the human reading the file. Nothing depends on it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from co_docs_watcher.cvm.search import MatchKind
from co_docs_watcher.cvm.ticker import PrefixSource
from co_docs_watcher.errors import WatchListError
from co_docs_watcher.text import normalize_cvm_code

__all__ = ["WatchedCompany"]


@dataclass(frozen=True, slots=True)
class WatchedCompany:
    """One company being monitored."""

    cvm_code: str
    prefix: str
    prefix_source: PrefixSource
    legal_name: str
    matched_by: MatchKind

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, where: str) -> WatchedCompany:
        """Read one entry, refusing anything malformed.

        Refusing is the point: this file is hand-edited, and an entry quietly dropped for
        being unreadable is a company that stops being monitored without anyone noticing.
        """
        if not isinstance(raw, Mapping):
            raise WatchListError(
                f"{where}: every entry must be a mapping, got {type(raw).__name__}"
            )
        cvm_code = normalize_cvm_code(str(raw.get("cvm_code", "")))
        if not cvm_code or len(cvm_code) > 6:
            raise WatchListError(f"{where}: cvm_code is missing or not a CVM code")
        prefix = str(raw.get("prefix", "") or "").strip()
        if not prefix:
            raise WatchListError(f"{where}: prefix is required; it names the company's folder")
        return cls(
            cvm_code=cvm_code,
            prefix=prefix,
            prefix_source=_enum(PrefixSource, raw.get("prefix_source"), "prefix_source", where),
            legal_name=str(raw.get("legal_name", "") or "").strip(),
            matched_by=_enum(MatchKind, raw.get("matched_by"), "matched_by", where),
        )

    def to_mapping(self) -> dict[str, str]:
        """The entry as it is written, in the order a human reads it."""
        return {
            "cvm_code": self.cvm_code,
            "prefix": self.prefix,
            "prefix_source": str(self.prefix_source),
            "matched_by": str(self.matched_by),
            "legal_name": self.legal_name,
        }


def _enum[T: (MatchKind, PrefixSource)](
    enumeration: type[T], value: Any, field: str, where: str
) -> T:
    try:
        return enumeration(str(value))
    except ValueError as exc:
        allowed = ", ".join(str(member) for member in enumeration)
        raise WatchListError(f"{where}: {field} must be one of {allowed} (got {value!r})") from exc
