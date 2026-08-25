"""A source that answers from memory, and the deliveries it hands back.

The pipeline depends on the ``Source`` protocol, so testing it needs no server and no wire
format: ``FakeSource`` is a list of ``SourceDocument`` plus a recipe per identity for what the
download writes into the staging directory. What the RAD adapter does to produce those objects
is pinned by the contract tests instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path

from co_docs_watcher.models import (
    DeliveredFile,
    Delivery,
    DeliveryKind,
    FileRole,
    SourceDocument,
)

Identity = tuple[int, int]
Recipe = Callable[[SourceDocument, Path], Delivery]

PDF_BYTES = b"%PDF-1.7\nnot a real filing, but it starts like one\n"


def pdf_delivery(document: SourceDocument, into: Path, *, content: bytes = PDF_BYTES) -> Delivery:
    """A standalone PDF: one stable file, under the neutral staging name."""
    into.mkdir(parents=True, exist_ok=True)
    path = into / "document.pdf"
    path.write_bytes(content)
    return Delivery(
        document=document,
        kind=DeliveryKind.PDF,
        files=(DeliveredFile(path=path, role=FileRole.DOCUMENT, stable=True),),
    )


def zip_delivery(
    document: SourceDocument,
    into: Path,
    *,
    members: Mapping[str, bytes] | None = None,
    generated: bool = True,
) -> Delivery:
    """A structured delivery, already extracted: stable members plus the generated copy.

    The generated PDF carries the generation instant in its name, which is exactly why the
    pipeline has to impose one of its own.
    """
    into.mkdir(parents=True, exist_ok=True)
    if members is None:
        members = {f"{document.cvm_code}ITR30-06-2026v1.xml": b"<itr><conta/></itr>"}
    files = []
    for name, content in members.items():
        path = into / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files.append(DeliveredFile(path=path, role=FileRole.MEMBER, stable=True))
    if generated:
        path = into / f"{document.document_id}_{document.cvm_code}_20260824153701.pdf"
        path.write_bytes(PDF_BYTES)
        files.append(DeliveredFile(path=path, role=FileRole.GENERATED_PDF, stable=False))
    return Delivery(document=document, kind=DeliveryKind.ZIP, files=tuple(files))


class FakeSource:
    """A ``Source`` that lists from a list and downloads from a recipe.

    ``failures`` maps an identity to the exceptions its downloads raise, one per attempt and in
    order; once they run out the download succeeds, which is what makes a retry budget testable.
    """

    def __init__(
        self,
        documents: Iterable[SourceDocument] = (),
        *,
        stray: Iterable[SourceDocument] = (),
        recipes: Mapping[Identity, Recipe] | None = None,
        failures: Mapping[Identity, Sequence[Exception]] | None = None,
    ) -> None:
        self.documents = list(documents)
        # Rows the source answers with whatever days were asked for — a source that lies about
        # its own date filter, which is the only way the window guard can ever fire.
        self.stray = list(stray)
        self.recipes = dict(recipes or {})
        self.failures = {identity: list(errors) for identity, errors in (failures or {}).items()}
        self.requested: list[list[date]] = []
        self.downloaded: list[Identity] = []

    def list_window(self, days: Sequence[date]) -> list[SourceDocument]:
        self.requested.append(list(days))
        wanted = set(days)
        listed = [document for document in self.documents if document.delivery_date in wanted]
        return listed + self.stray

    def download(self, document: SourceDocument, into: Path) -> Delivery:
        self.downloaded.append(document.identity)
        pending = self.failures.get(document.identity)
        if pending:
            raise pending.pop(0)
        recipe = self.recipes.get(document.identity, pdf_delivery)
        return recipe(document, into)
