"""Reading and rewriting ``companies.yaml`` without ever losing what a human wrote.

The file belongs to the operator. They add comments to it, order it the way they think about
their portfolio, and edit it while the watcher is running. Two mechanisms keep that true:

*Round-trip YAML.* ``ruamel.yaml`` preserves comments, quoting and key order across a load and
dump; the plain parser would silently return a file stripped of every comment in it.

*A hash guard, not a timestamp.* Before replacing the file, the bytes on disk are hashed and
compared against the bytes that were loaded. If they differ, someone edited the file in the
meantime and the watcher abandons its own write. ``mtime`` is not the mechanism: it has a
coarse resolution on some filesystems, it can move backwards, and a same-second edit is
exactly the case that must not be missed.

The write itself is a temporary file plus ``rename``, so a crash mid-write leaves the previous
list intact rather than half a list.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import SingleQuotedScalarString

from co_docs_watcher.errors import WatchListConflictError, WatchListError
from co_docs_watcher.scope.models import WatchedCompany
from co_docs_watcher.text import normalize_cvm_code

__all__ = ["COMPANIES_KEY", "WatchList"]

logger = logging.getLogger(__name__)

#: The single top-level key. Everything else in the file is the human's.
COMPANIES_KEY = "companies"

_HEADER = """\
The companies this archive is about, one entry per company.

Comments, ordering and quoting in this file survive every rewrite the watcher makes, and
an edit made while the watcher is running is never overwritten. Entries are added by
`add` and removed by `rm`; editing them by hand is fine.

`prefix` names the company's folder in the archive and is a snapshot: changing it here
changes where future documents are filed, and never renames a folder already on disk.
"""


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 100
    return yaml


class WatchList:
    """The watch list as loaded from disk, plus what is needed to write it back safely."""

    __slots__ = ("_digest", "_document", "_path")

    def __init__(self, path: Path, document: CommentedMap, digest: str) -> None:
        self._path = path
        self._document = document
        self._digest = digest

    @classmethod
    def load(cls, path: Path) -> WatchList:
        """Load the list, or start an empty one when the file does not exist yet."""
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            return cls(path, _empty_document(), "")
        except OSError as exc:
            raise WatchListError(f"{path}: cannot be read: {exc}") from exc

        try:
            document = _yaml().load(io.BytesIO(payload))
        except YAMLError as exc:
            raise WatchListError(f"{path}: is not valid YAML: {exc}") from exc

        if document is None:
            document = _empty_document()
        if not isinstance(document, CommentedMap):
            raise WatchListError(
                f"{path}: the top level must be a mapping with a {COMPANIES_KEY} key"
            )
        if document.get(COMPANIES_KEY) is None:
            document[COMPANIES_KEY] = CommentedSeq()
        if not isinstance(document[COMPANIES_KEY], CommentedSeq):
            raise WatchListError(f"{path}: {COMPANIES_KEY} must be a list")

        watch_list = cls(path, document, _digest(payload))
        watch_list.companies  # noqa: B018 — validate every entry at load, not at first use
        return watch_list

    @property
    def path(self) -> Path:
        return self._path

    @property
    def companies(self) -> tuple[WatchedCompany, ...]:
        entries = self._document[COMPANIES_KEY]
        return tuple(
            WatchedCompany.from_mapping(entry, where=f"{self._path}: entry {index + 1}")
            for index, entry in enumerate(entries)
        )

    @property
    def cvm_codes(self) -> frozenset[str]:
        """What discovery filters the global sweep against."""
        return frozenset(company.cvm_code for company in self.companies)

    def get(self, cvm_code: str) -> WatchedCompany | None:
        code = normalize_cvm_code(cvm_code)
        return next((company for company in self.companies if company.cvm_code == code), None)

    def add(self, company: WatchedCompany) -> bool:
        """Append a company. Returns ``False`` when it is already there, changing nothing.

        Appending rather than sorting is deliberate: the order of this file is the human's,
        and re-sorting it on every ``add`` would produce a diff nobody asked for.
        """
        if self.get(company.cvm_code) is not None:
            return False
        entry = CommentedMap(company.to_mapping())
        entry["cvm_code"] = SingleQuotedScalarString(company.cvm_code)
        self._document[COMPANIES_KEY].append(entry)
        return True

    def remove(self, cvm_code: str) -> WatchedCompany | None:
        """Remove a company by CVM code, returning what was removed."""
        code = normalize_cvm_code(cvm_code)
        entries = self._document[COMPANIES_KEY]
        for index, company in enumerate(self.companies):
            if company.cvm_code == code:
                del entries[index]
                return company
        return None

    def render(self) -> bytes:
        buffer = io.BytesIO()
        _yaml().dump(self._document, buffer)
        return buffer.getvalue()

    def save(self) -> None:
        """Write the list back, unless the file changed underneath.

        The conflict is not resolved and not merged: the file on disk is left exactly as the
        human left it, and the caller is told what happened.
        """
        current = _digest_of(self._path)
        if current != self._digest:
            logger.error(
                "watch list: %s changed on disk since it was loaded; the edit on disk is kept "
                "and this write is abandoned",
                self._path,
            )
            raise WatchListConflictError(
                f"{self._path} changed on disk since it was loaded; nothing was written"
            )
        payload = self.render()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        staging = self._path.with_name(self._path.name + ".part")
        try:
            staging.write_bytes(payload)
            os.replace(staging, self._path)
        except OSError as exc:
            staging.unlink(missing_ok=True)
            raise WatchListError(f"{self._path}: cannot be written: {exc}") from exc
        self._digest = _digest(payload)


def _empty_document() -> CommentedMap:
    document = CommentedMap()
    document[COMPANIES_KEY] = CommentedSeq()
    document.yaml_set_start_comment(_HEADER)
    return document


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _digest_of(path: Path) -> str:
    """The digest of the file as it is right now; ``""`` when it does not exist."""
    try:
        return _digest(path.read_bytes())
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise WatchListError(f"{path}: cannot be read: {exc}") from exc
