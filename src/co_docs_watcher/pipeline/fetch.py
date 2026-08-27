"""Fetching: take the queue to disk — download, name, place atomically, hash.

Everything here exists because the filesystem and SQLite do not form one transaction. The
order is fixed and it is the order that survives a crash at any point in it: stage under
``documents_root/.tmp/``, place with a single ``rename``, record the files, and only then
call the document ``available``. A run killed before the rename leaves debris in ``.tmp/``
and nothing in the archive; killed after it, the file rows are what lets the next start
recognize a finished download instead of fetching it again.

The names in the archive are imposed, never the source's. ``Content-Disposition`` carries no
id and no readable date, and the reading PDF generated inside a structured package carries the
*generation instant*, so two downloads of the same document would land under two names. What
goes on disk is ``{Category}_{document_id}_V{version}.{ext}``: identity, in the file name,
where a human reading a directory listing can see it.

Only the container is named after the category, and only for structured deliveries — which is
why two deliveries of the same category, by the same company, on the same day, do not collide:
the directory takes a suffix, and the identity stays in the PDF inside it.

What decides between the two layouts is the shape of the *delivery*, never the shape of the
response. An eventual filing reaches this module as one ``DOCUMENT`` whether it arrived as a
bare PDF or wrapped in an IPE container the adapter unwrapped, and both land as one named PDF
in the company's folder — which is what a reading queue is for: the day's directory listing
should read as the day's publications, not as a row of folders to open one by one.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from co_docs_watcher.archive_modes import (
    DEFAULT_MODES,
    ArchiveModes,
    ensure_directory,
    stamp_file,
    stamp_tree,
)
from co_docs_watcher.clock import directory_name
from co_docs_watcher.errors import (
    CaptchaRequiredError,
    DocumentError,
    RequestBudgetExceededError,
    SourceContractError,
    TransientSourceError,
)
from co_docs_watcher.manifest.repo import AttemptOutcome, FileRecord, Identity, Manifest
from co_docs_watcher.models import (
    DeliveredFile,
    Delivery,
    FileRole,
    LocalState,
    SourceDocument,
)
from co_docs_watcher.scope.models import WatchedCompany
from co_docs_watcher.source import Source
from co_docs_watcher.text import MAX_NAME_COMPONENT, strip_accents

__all__ = [
    "MAX_ATTEMPTS",
    "FetchOutcome",
    "archive_path_of",
    "category_component",
    "document_file_name",
    "fetch_pending",
    "sha256_of",
]

logger = logging.getLogger(__name__)

#: Failed downloads a document is allowed before it stops being retried. Attempts are counted
#: across runs, in the manifest: one try per run, so a source that is down for an afternoon
#: costs a document one attempt, not its whole budget.
MAX_ATTEMPTS = 3

#: What a category with no usable text is called. Deliberately not invented in Portuguese: the
#: source's own label is data, its absence is not.
_UNNAMED_CATEGORY = "Document"

_CHUNK = 1024 * 1024

_NON_ALPHANUMERIC = re.compile(r"[^0-9A-Za-z]+")

#: How far the category subfolder may be disambiguated before a delivery is refused. Reaching
#: it means a day holds this many unrecognizable containers of one category for one company,
#: which is a defect to be looked at rather than a folder to add.
_MAX_CONTAINER_NAMES = 50


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """What one pass over the queue did."""

    available: tuple[Identity, ...]
    retrying: tuple[Identity, ...]
    failed: tuple[Identity, ...]

    @property
    def attempted(self) -> int:
        return len(self.available) + len(self.retrying) + len(self.failed)


def fetch_pending(
    source: Source,
    manifest: Manifest,
    *,
    documents_root: Path,
    staging_root: Path,
    watched: Iterable[WatchedCompany],
    modes: ArchiveModes = DEFAULT_MODES,
    max_attempts: int = MAX_ATTEMPTS,
) -> FetchOutcome:
    """Download everything still ``discovered``, one document at a time.

    An isolated failure never kills the batch: it is recorded against the retry budget, the
    document goes back to the queue or to ``failed``, and the next document is fetched.

    ``modes`` is what the archive is created with, all the way down: the staging tree is
    stamped before the rename that places it, so nothing is ever visible in the archive under
    a mode that is about to be corrected.
    """
    prefixes = {company.cvm_code: company.prefix for company in watched}
    # Under ``documents_root`` on purpose: placement is a ``rename``, and a rename is only
    # atomic within one filesystem.
    ensure_directory(staging_root, modes)
    pending = manifest.documents.in_state(LocalState.DISCOVERED)
    available: list[Identity] = []
    retrying: list[Identity] = []
    failed: list[Identity] = []

    for record in pending:
        document = record.document
        identity = record.identity
        staging = staging_root / f"{document.document_id}-v{document.version}"
        _discard(staging)
        manifest.documents.transition(identity, LocalState.DOWNLOADING)
        try:
            delivery = source.download(document, staging)
            files = _place(
                delivery,
                staging=staging,
                documents_root=documents_root,
                prefix=_prefix_for(document, prefixes),
                modes=modes,
            )
            manifest.files.record_files(identity, files)
            manifest.documents.transition(
                identity, LocalState.AVAILABLE, archive_path=archive_path_of(files)
            )
            manifest.attempts.record(identity, AttemptOutcome.SUCCESS)
            available.append(identity)
        except (CaptchaRequiredError, RequestBudgetExceededError):
            # The source refused the run, not this document. Put it back in the queue before
            # the run ends: charging its retry budget for an attempt that never reached it is
            # how a document that was only ever unlucky ends up permanently failed.
            manifest.documents.transition(identity, LocalState.DISCOVERED)
            raise
        except OSError as error:
            # The archive, not the document: mkdir, chmod, replace, stat and the hash all fail
            # for a full disk or a root nobody can write to, and every document in the queue
            # fails the same way. Charging that to the retry budget is how one bad afternoon
            # turns the whole queue permanently ``failed`` — and nothing brings a failed
            # document back to the queue.
            logger.error("document %s could not be written to the archive: %s", identity, error)
            manifest.documents.transition(identity, LocalState.DISCOVERED)
            retrying.append(identity)
        except SourceContractError as error:
            # Not retryable and not the document's fault: a signature this build cannot store
            # will not become storable on the next attempt. Loud, and the batch continues.
            logger.critical("document %s: %s", identity, error)
            manifest.attempts.record(identity, AttemptOutcome.FAILURE, str(error))
            manifest.documents.transition(identity, LocalState.FAILED)
            failed.append(identity)
        except (DocumentError, TransientSourceError) as error:
            manifest.attempts.record(identity, AttemptOutcome.FAILURE, str(error))
            exhausted = manifest.attempts.failures(identity) >= max_attempts
            manifest.documents.transition(
                identity, LocalState.FAILED if exhausted else LocalState.DISCOVERED
            )
            if exhausted:
                logger.error(
                    "document %s failed %d times, giving up: %s", identity, max_attempts, error
                )
                failed.append(identity)
            else:
                logger.warning(
                    "document %s could not be fetched, will retry: %s", identity, error
                )
                retrying.append(identity)
        finally:
            _discard(staging)

    logger.info(
        "fetch: %d available, %d to retry, %d failed", len(available), len(retrying), len(failed)
    )
    return FetchOutcome(tuple(available), tuple(retrying), tuple(failed))


def category_component(category: str) -> str:
    """The category as one path component, in the source's own words minus its punctuation.

    Structured categories arrive qualified — ``ITR - Informações Trimestrais`` — and the
    qualification is the same for every company on every day, so only the acronym is kept.
    Case is the source's: this is a label a human reads, not an identifier to compare.
    """
    head = category.split(" - ", 1)[0]
    words = _split_words(strip_accents(head))
    if not words:
        return _UNNAMED_CATEGORY
    kept: list[str] = []
    length = 0
    for word in words:
        addition = len(word) + (1 if kept else 0)
        if kept and length + addition > MAX_NAME_COMPONENT:
            break
        kept.append(word)
        length += addition
    return "-".join(kept)[:MAX_NAME_COMPONENT]


def document_file_name(document: SourceDocument, extension: str) -> str:
    """``{Category}_{document_id}_V{version}.{ext}`` — the identity, spelled out."""
    return (
        f"{category_component(document.category)}_{document.document_id}"
        f"_{version_component(document.version)}{extension}"
    )


def version_component(version: int) -> str:
    """``V01``. Zero-padded so that a directory listing sorts the way a human expects."""
    return f"V{version:02d}"


def archive_path_of(files: Sequence[FileRecord]) -> Path:
    """Where the archive holds a delivery: the file itself, or the directory that holds it.

    Derived from the recorded files rather than remembered, so that startup reconciliation can
    reach the same answer from the manifest alone.
    """
    if not files:
        raise DocumentError("a delivery with no files has no place in the archive")
    if len(files) == 1 and files[0].role == FileRole.DOCUMENT:
        return files[0].relative_path
    return Path(os.path.commonpath([str(entry.relative_path.parent) for entry in files]))


def _place(
    delivery: Delivery,
    *,
    staging: Path,
    documents_root: Path,
    prefix: str,
    modes: ArchiveModes,
) -> list[FileRecord]:
    """Move a staged delivery into the archive with one rename, and hash what landed.

    The two directory levels are created one at a time rather than in a single
    ``parents=True`` call: parents are born from ``0o777`` regardless of the mode asked for,
    which would give the date directory and the company directory two different modes. Each
    level is stamped even when it already existed, which is what brings a date directory built
    by an earlier run up to the mode now declared.
    """
    document = delivery.document
    date_root = ensure_directory(documents_root / directory_name(document.delivery_date), modes)
    company_root = ensure_directory(date_root / prefix, modes)
    if _is_standalone(delivery):
        placed = _place_document(delivery, company_root=company_root, modes=modes)
    else:
        placed = _place_container(
            delivery, staging=staging, company_root=company_root, modes=modes
        )
    return [
        FileRecord.of(
            delivered,
            relative_path=path.relative_to(documents_root),
            sha256=sha256_of(path),
            size_bytes=path.stat().st_size,
        )
        for delivered, path in placed
    ]


def _is_standalone(delivery: Delivery) -> bool:
    """One file, and that file is the filing itself — however it reached the staging tree.

    ``archive_path_of`` reads the same discriminator back off the manifest, which is what
    keeps placement and startup reconciliation from disagreeing about where a delivery lives.
    """
    return len(delivery.files) == 1 and delivery.files[0].role is FileRole.DOCUMENT


def _place_document(
    delivery: Delivery, *, company_root: Path, modes: ArchiveModes
) -> list[tuple[DeliveredFile, Path]]:
    """A standalone filing: one file, one ``rename``, the imposed name."""
    delivered = delivery.files[0]
    extension = delivered.path.suffix
    if not extension:
        # The extension is decided by the content, at the boundary, and a name invented here
        # would be the one place in the archive where it was guessed instead.
        raise DocumentError(
            f"document {delivery.document.identity}: the staged file {delivered.path.name!r} "
            "carries no extension, and the archive name may not invent one"
        )
    target = company_root / document_file_name(delivery.document, extension)
    stamp_file(delivered.path, modes)
    os.replace(delivered.path, target)
    return [(delivered, target)]


def _place_container(
    delivery: Delivery, *, staging: Path, company_root: Path, modes: ArchiveModes
) -> list[tuple[DeliveredFile, Path]]:
    """A structured delivery: the whole staging tree becomes the category subfolder.

    The container itself never reaches the archive — it was extracted at the boundary — and the
    generated reading PDF is renamed *before* the move, so the directory that arrives is
    already the directory that stays — and stamped before it, for the same reason: the rename
    publishes the whole tree in one step, and there is no moment afterwards in which fixing
    the modes would still be invisible.
    """
    staged = _impose_generated_names(delivery, staging=staging)
    stamp_tree(staging, modes)
    target_dir = _free_directory(company_root, delivery)
    if target_dir.exists():
        # Ours, from a run that placed the delivery and died before recording it:
        # ``_free_directory`` never hands back a directory that is not recognizably this
        # document's, which is what makes deleting one here safe.
        shutil.rmtree(target_dir)
    os.replace(staging, target_dir)
    return [(delivered, target_dir / path.relative_to(staging)) for delivered, path in staged]


def _impose_generated_names(
    delivery: Delivery, *, staging: Path
) -> list[tuple[DeliveredFile, Path]]:
    """Rename the generated reading copies; every other member keeps its stable origin name."""
    document = delivery.document
    imposed = document_file_name(document, ".pdf")
    staged: list[tuple[DeliveredFile, Path]] = []
    generated = 0
    for delivered in delivery.files:
        if delivered.role is not FileRole.GENERATED_PDF:
            staged.append((delivered, delivered.path))
            continue
        generated += 1
        name = imposed if generated == 1 else f"{Path(imposed).stem}_{generated}.pdf"
        target = delivered.path.with_name(name)
        os.replace(delivered.path, target)
        staged.append((delivered, target))
    return staged


def _free_directory(company_root: Path, delivery: Delivery) -> Path:
    """The category subfolder, disambiguated when the day already holds one of that category.

    A directory that already exists is only ever handed back when it is *this* document's —
    recognized by the imposed PDF name inside it, which is the whole reason identity lives in
    the file name and not in the directory. That is the guarantee the caller deletes on: every
    other name is either free or skipped. A container with no generated copy cannot be
    recognized that way and always takes the next name, which is a duplicate folder rather than
    an overwrite.

    When every name is taken by something unrecognizable the delivery is refused. Placing it
    would mean deleting a directory nobody proved was ours, and a document that failed to land
    is recoverable in a way a directory that was overwritten is not.
    """
    document = delivery.document
    base = category_component(document.category)
    version = version_component(document.version)
    imposed = document_file_name(document, ".pdf")
    stem = f"{base}_{version}_{document.document_id}"
    candidates = [base, f"{base}_{version}", stem]
    candidates += [f"{stem}_{ordinal}" for ordinal in range(2, _MAX_CONTAINER_NAMES + 1)]
    for name in candidates:
        candidate = company_root / name
        if not candidate.exists() or (candidate / imposed).exists():
            return candidate
    raise DocumentError(
        f"document {document.identity}: {company_root} already holds {len(candidates)} "
        f"directories named after {base!r} and none of them is this document's"
    )


def _prefix_for(document: SourceDocument, prefixes: dict[str, str]) -> str:
    prefix = prefixes.get(document.cvm_code)
    if prefix:
        return prefix
    # The last step of the folder-name chain. Reaching it here means the queue holds a company
    # the watch list no longer does, which is worth saying out loud.
    logger.warning(
        "document %s belongs to %s, which is not in the watch list; filing it under its CVM code",
        document.identity,
        document.cvm_code,
    )
    return document.cvm_code


def _split_words(text: str) -> list[str]:
    return [word for word in _NON_ALPHANUMERIC.split(text) if word]


def sha256_of(path: Path) -> str:
    """The content hash of one file. Integrity and auditing — never deduplication."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _discard(staging: Path) -> None:
    """Remove a staging tree. What survives is reported: ``.tmp/`` that only grows is a defect."""
    try:
        shutil.rmtree(staging)
    except FileNotFoundError:
        pass
    except OSError as error:
        logger.warning("staging debris at %s could not be removed: %s", staging, error)
