"""The queue reaching disk: imposed names, atomic placement, per-file hashes, retry budget."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from co_docs_watcher.archive_modes import ArchiveModes
from co_docs_watcher.config import DEFAULT_MAX_DOCUMENT_ATTEMPTS as ATTEMPTS
from co_docs_watcher.errors import (
    CaptchaRequiredError,
    DocumentError,
    SourceContractError,
    TransientSourceError,
)
from co_docs_watcher.manifest.repo import FileRecord, Manifest
from co_docs_watcher.models import (
    DeliveredFile,
    Delivery,
    DeliveryKind,
    FileRole,
    LocalState,
    SourceDocument,
)
from co_docs_watcher.pipeline.fetch import (
    _MAX_CONTAINER_NAMES,
    archive_path_of,
    category_component,
    document_file_name,
    fetch_pending,
)
from tests.conftest import TODAY, Roots
from tests.pipeline import (
    PDF_BYTES,
    FakeSource,
    pdf_delivery,
    unwrapped_ipe_delivery,
    zip_delivery,
)
from tests.test_models import make_document
from tests.test_pipeline_discover import PETR

DAY = TODAY.isoformat()
ITR = "ITR - Informações Trimestrais"


def queue(manifest: Manifest, *documents: SourceDocument) -> None:
    for document in documents:
        manifest.documents.upsert_observed(document)


def run(
    manifest: Manifest, roots: Roots, source: FakeSource, **kwargs: object
) -> object:
    kwargs.setdefault("max_attempts", ATTEMPTS)
    return fetch_pending(
        source,
        manifest,
        documents_root=roots.documents_root,
        staging_root=roots.staging_root,
        watched=(PETR,),
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_standalone_pdf_lands_under_the_imposed_name(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    queue(manifest, document)

    outcome = run(manifest, roots, FakeSource())

    placed = roots.day(TODAY) / "PETR" / "Fato-Relevante_160310_V01.pdf"
    record = manifest.documents.require(document.identity)
    assert outcome.available == (document.identity,)
    assert placed.read_bytes() == PDF_BYTES
    assert record.local_state is LocalState.AVAILABLE
    assert record.archive_path == Path(DAY) / "PETR" / "Fato-Relevante_160310_V01.pdf"


def test_the_pdf_is_hashed_once_and_marked_stable(manifest: Manifest, roots: Roots) -> None:
    document = make_document()
    queue(manifest, document)

    run(manifest, roots, FakeSource())

    files = manifest.files.files_for(document.identity)
    assert [entry.role for entry in files] == [FileRole.DOCUMENT]
    assert files[0].sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert files[0].size_bytes == len(PDF_BYTES)
    assert files[0].stable is True


def test_what_the_run_reports_weighing_is_what_the_archive_holds(
    manifest: Manifest, roots: Roots
) -> None:
    """A container is archived uncompressed, so the figure is read off the placed files."""
    document = make_document(category=ITR)
    queue(manifest, document)
    source = FakeSource(recipes={document.identity: zip_delivery})

    outcome = run(manifest, roots, source)

    placed = [path for path in (roots.day(TODAY) / "PETR" / "ITR").iterdir() if path.is_file()]
    assert outcome.archived_bytes == sum(path.stat().st_size for path in placed)
    assert outcome.archived_bytes > 0


def test_a_download_that_never_landed_weighs_nothing(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    queue(manifest, document)
    source = FakeSource(failures={document.identity: [DocumentError("truncated")]})

    outcome = run(manifest, roots, source)

    assert outcome.available == ()
    assert outcome.archived_bytes == 0


def test_an_unwrapped_ipe_delivery_lands_flat_like_any_other_filing(
    manifest: Manifest, roots: Roots
) -> None:
    """The response was a container; the delivery is one filing, and the layout follows it."""
    document = make_document()
    queue(manifest, document)
    source = FakeSource(recipes={document.identity: unwrapped_ipe_delivery})

    run(manifest, roots, source)

    placed = Path(DAY) / "PETR" / "Fato-Relevante_160310_V01.pdf"
    assert (roots.documents_root / placed).read_bytes() == PDF_BYTES
    assert manifest.documents.require(document.identity).archive_path == placed
    # No category subfolder, and nothing left under the opaque name the source used.
    assert sorted(path.name for path in (roots.day(TODAY) / "PETR").iterdir()) == [
        "Fato-Relevante_160310_V01.pdf"
    ]


def test_a_container_is_extracted_in_full_into_a_category_subfolder(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document(category=ITR)
    queue(manifest, document)
    source = FakeSource(recipes={document.identity: zip_delivery})

    run(manifest, roots, source)

    folder = roots.day(TODAY) / "PETR" / "ITR"
    assert sorted(path.name for path in folder.iterdir()) == [
        "009512ITR30-06-2026v1.xml",  # a stable member keeps the name the source gave it
        "ITR_160310_V01.pdf",  # the generated copy is renamed; its own name carries an instant
    ]
    assert manifest.documents.require(document.identity).archive_path == Path(DAY) / "PETR" / "ITR"
    # The container itself never reaches the archive.
    assert not any(path.suffix == ".zip" for path in folder.rglob("*"))


def test_the_generated_copy_is_the_only_file_marked_unstable(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document(category=ITR)
    queue(manifest, document)

    run(manifest, roots, FakeSource(recipes={document.identity: zip_delivery}))

    files = {
        entry.relative_path.name: entry
        for entry in manifest.files.files_for(document.identity)
    }
    assert files["ITR_160310_V01.pdf"].stable is False
    assert files["009512ITR30-06-2026v1.xml"].stable is True


def test_two_deliveries_of_one_category_on_one_day_do_not_collide(
    manifest: Manifest, roots: Roots
) -> None:
    first = make_document(category=ITR)
    second = make_document(document_id=160477, category=ITR)
    queue(manifest, first, second)
    source = FakeSource(
        recipes={first.identity: zip_delivery, second.identity: zip_delivery}
    )

    run(manifest, roots, source)

    company = roots.day(TODAY) / "PETR"
    assert sorted(path.name for path in company.iterdir()) == ["ITR", "ITR_V01"]
    # Identity is in the PDF name, not in the directory: both folders say which document is in
    # them, and the suffix only keeps them apart.
    assert (company / "ITR" / "ITR_160310_V01.pdf").exists()
    assert (company / "ITR_V01" / "ITR_160477_V01.pdf").exists()


def test_a_second_version_takes_the_version_suffix(manifest: Manifest, roots: Roots) -> None:
    first = make_document(category=ITR)
    resubmission = make_document(document_id=160477, version=2, category=ITR)
    queue(manifest, first, resubmission)
    source = FakeSource(
        recipes={first.identity: zip_delivery, resubmission.identity: zip_delivery}
    )

    run(manifest, roots, source)

    company = roots.day(TODAY) / "PETR"
    assert (company / "ITR_V02" / "ITR_160477_V02.pdf").exists()


def test_an_available_document_is_not_fetched_again(manifest: Manifest, roots: Roots) -> None:
    document = make_document()
    queue(manifest, document)
    source = FakeSource()

    run(manifest, roots, source)
    run(manifest, roots, source)

    assert source.downloaded == [document.identity]


def test_a_failed_download_leaves_nothing_in_the_archive_and_stays_in_the_queue(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    queue(manifest, document)
    source = FakeSource(failures={document.identity: [TransientSourceError("the source is down")]})

    outcome = run(manifest, roots, source)

    assert outcome.retrying == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.DISCOVERED
    assert list(roots.day(TODAY).glob("*")) == []
    assert list(roots.staging_root.iterdir()) == []
    assert manifest.attempts.lifetime_failures(document.identity) == 1


def test_debris_from_a_failed_attempt_never_reaches_the_archive(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    queue(manifest, document)

    def half_written(candidate: SourceDocument, into: Path):
        pdf_delivery(candidate, into)
        raise DocumentError("the container is empty")

    run(manifest, roots, FakeSource(recipes={document.identity: half_written}))

    assert list(roots.staging_root.iterdir()) == []
    assert not (roots.day(TODAY)).exists()


def test_the_batch_survives_a_document_that_cannot_be_fetched(
    manifest: Manifest, roots: Roots
) -> None:
    broken = make_document()
    fine = make_document(document_id=160477)
    queue(manifest, broken, fine)
    source = FakeSource(failures={broken.identity: [DocumentError("the container is empty")]})

    outcome = run(manifest, roots, source)

    assert outcome.available == (fine.identity,)
    assert manifest.documents.require(fine.identity).local_state is LocalState.AVAILABLE


def test_the_retry_budget_is_counted_across_runs(manifest: Manifest, roots: Roots) -> None:
    document = make_document()
    queue(manifest, document)
    errors = [TransientSourceError("down") for _ in range(ATTEMPTS)]
    source = FakeSource(failures={document.identity: errors})

    for _ in range(ATTEMPTS - 1):
        run(manifest, roots, source)
        assert manifest.documents.require(document.identity).local_state is LocalState.DISCOVERED

    outcome = run(manifest, roots, source)

    assert outcome.failed == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.FAILED


def test_a_contract_divergence_is_not_retried(manifest: Manifest, roots: Roots) -> None:
    document = make_document()
    queue(manifest, document)
    source = FakeSource(
        failures={document.identity: [SourceContractError("the signature matches nothing")]}
    )

    outcome = run(manifest, roots, source)

    assert outcome.failed == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.FAILED


def test_every_attempt_is_recorded_against_the_budget(manifest: Manifest, roots: Roots) -> None:
    document = make_document()
    queue(manifest, document)

    run(manifest, roots, FakeSource())

    assert manifest.attempts.lifetime_attempts(document.identity) == 1
    assert manifest.attempts.lifetime_failures(document.identity) == 0


def test_a_company_missing_from_the_watch_list_is_filed_under_its_cvm_code(
    manifest: Manifest, roots: Roots, caplog: pytest.LogCaptureFixture
) -> None:
    document = make_document(cvm_code="002437")
    queue(manifest, document)

    fetch_pending(
        FakeSource(),
        manifest,
        documents_root=roots.documents_root,
        staging_root=roots.staging_root,
        watched=(),
        max_attempts=ATTEMPTS,
    )

    assert (roots.day(TODAY) / "002437" / "Fato-Relevante_160310_V01.pdf").exists()


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("Fato Relevante", "Fato-Relevante"),
        ("Aviso aos Acionistas", "Aviso-aos-Acionistas"),
        ("ITR - Informações Trimestrais", "ITR"),
        ("DFP - Demonstrações Financeiras Padronizadas", "DFP"),
        ("Comunicado ao Mercado", "Comunicado-ao-Mercado"),
        ("Documentos de Oferta de Distribuição Pública", "Documentos-de-Oferta-de"),
        ("", "Document"),
        ("   ", "Document"),
        ("Assembleia/Reunião", "Assembleia-Reuniao"),
    ],
)
def test_the_category_becomes_one_readable_path_component(category: str, expected: str) -> None:
    assert category_component(category) == expected


def test_the_file_name_carries_the_identity() -> None:
    document = make_document(document_id=160310, version=12)
    assert document_file_name(document, ".pdf") == "Fato-Relevante_160310_V12.pdf"


def test_the_archive_path_of_a_container_is_the_directory_that_holds_it() -> None:
    folder = Path(DAY) / "PETR" / "ITR"
    files = [
        FileRecord(folder / "ITR_1_V01.pdf", FileRole.GENERATED_PDF, "", 0, False),
        FileRecord(folder / "sub" / "a.xml", FileRole.MEMBER, "", 0, True),
    ]
    assert archive_path_of(files) == Path(DAY) / "PETR" / "ITR"


def test_the_archive_path_of_a_standalone_pdf_is_the_file() -> None:
    files = [FileRecord(Path(DAY) / "PETR" / "F_1_V01.pdf", FileRole.DOCUMENT, "", 0, True)]
    assert archive_path_of(files) == Path(DAY) / "PETR" / "F_1_V01.pdf"


def test_a_container_left_by_an_interrupted_run_is_replaced_not_duplicated(
    manifest: Manifest, roots: Roots
) -> None:
    # A run that placed the delivery and died before recording it: the folder on disk carries
    # this document's imposed PDF name, which is what says it is ours to replace.
    document = make_document(category=ITR)
    debris = roots.day(TODAY) / "PETR" / "ITR"
    debris.mkdir(parents=True)
    (debris / "ITR_160310_V01.pdf").write_bytes(b"%PDF-1.7\nhalf a run\n")
    (debris / "stale.xml").write_bytes(b"<itr/>")
    queue(manifest, document)

    run(manifest, roots, FakeSource(recipes={document.identity: zip_delivery}))

    company = roots.day(TODAY) / "PETR"
    assert [path.name for path in company.iterdir()] == ["ITR"]
    assert sorted(path.name for path in debris.iterdir()) == [
        "009512ITR30-06-2026v1.xml",
        "ITR_160310_V01.pdf",
    ]
    assert (debris / "ITR_160310_V01.pdf").read_bytes() == PDF_BYTES


def test_a_source_that_refuses_the_run_costs_the_queue_nothing(
    manifest: Manifest, roots: Roots
) -> None:
    # A captcha ends the run; the document in flight was never attempted, and the next run
    # must find it in the queue with its retry budget intact.
    document = make_document()
    queue(manifest, document)
    source = FakeSource(failures={document.identity: [CaptchaRequiredError("SolicitarCaptcha")]})

    with pytest.raises(CaptchaRequiredError):
        run(manifest, roots, source)

    assert manifest.documents.require(document.identity).local_state is LocalState.DISCOVERED
    assert manifest.attempts.lifetime_attempts(document.identity) == 0
    assert list(roots.staging_root.iterdir()) == []


MODES = ArchiveModes(directory_mode=0o750, file_mode=0o640)


@pytest.fixture
def restrictive_umask() -> Iterator[None]:
    """Run under a umask that would strip every group and other bit from a creation.

    ``0o077`` is not exotic — it is what a hardened image or a login shell may well set — and
    it is the value that makes the difference visible: without an explicit ``chmod``, a
    directory born from ``mkdir(0o755)`` lands ``0o700`` and a written file lands ``0o600``.
    """
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.usefixtures("restrictive_umask")
def test_a_standalone_document_and_its_two_directory_levels_carry_the_configured_modes(
    manifest: Manifest, roots: Roots
) -> None:
    """The umask has no vote: the archive is handed to people who never run this program."""
    document = make_document()
    queue(manifest, document)

    run(manifest, roots, FakeSource(), modes=MODES)

    placed = roots.day(TODAY) / "PETR" / "Fato-Relevante_160310_V01.pdf"
    assert mode_of(placed) == 0o640
    assert mode_of(placed.parent) == 0o750
    # The date directory is a *parent* of the company directory, and parents created by
    # ``parents=True`` are born from 0o777 — which is why the two levels are created apart.
    assert mode_of(roots.day(TODAY)) == 0o750


@pytest.mark.usefixtures("restrictive_umask")
def test_a_container_carries_the_modes_down_to_every_member(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document(category=ITR)
    queue(manifest, document)
    members = {
        "009512ITR30-06-2026v1.xml": b"<itr><conta/></itr>",
        "anexos/nota.xml": b"<nota/>",
    }
    source = FakeSource(
        recipes={
            document.identity: lambda doc, into: zip_delivery(doc, into, members=members)
        }
    )

    run(manifest, roots, source, modes=MODES)

    folder = roots.day(TODAY) / "PETR" / "ITR"
    assert mode_of(folder) == 0o750
    assert mode_of(folder / "anexos") == 0o750
    assert mode_of(folder / "ITR_160310_V01.pdf") == 0o640
    assert mode_of(folder / "009512ITR30-06-2026v1.xml") == 0o640
    assert mode_of(folder / "anexos" / "nota.xml") == 0o640


@pytest.mark.usefixtures("restrictive_umask")
def test_a_date_directory_left_by_an_earlier_run_is_re_stamped(
    manifest: Manifest, roots: Roots
) -> None:
    """``exist_ok=True`` reapplies nothing, so an archive built before this rule is repaired."""
    stale = roots.day(TODAY)
    stale.mkdir()
    os.chmod(stale, 0o700)
    document = make_document()
    queue(manifest, document)

    run(manifest, roots, FakeSource(), modes=MODES)

    assert mode_of(stale) == 0o750


@pytest.mark.usefixtures("restrictive_umask")
def test_a_caller_that_names_no_modes_gets_the_declared_defaults(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    queue(manifest, document)

    run(manifest, roots, FakeSource())

    placed = roots.day(TODAY) / "PETR" / "Fato-Relevante_160310_V01.pdf"
    assert mode_of(placed) == 0o644
    assert mode_of(placed.parent) == 0o755


FOREIGN = b"%PDF-1.7\nsomebody else's delivery\n"


def test_an_archive_that_cannot_be_written_does_not_cost_the_document_its_budget(
    manifest: Manifest, roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A full disk fails every document in the queue the same way. Charged to the retry budget
    # it would take three runs to turn the whole queue permanently ``failed``, and nothing
    # brings a failed document back.
    document = make_document()
    queue(manifest, document)

    def unwritable(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", unwritable)

    outcome = run(manifest, roots, FakeSource())

    assert outcome.retrying == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.DISCOVERED
    assert manifest.attempts.lifetime_failures(document.identity) == 0


def test_a_category_folder_that_is_not_this_documents_is_never_overwritten(
    manifest: Manifest, roots: Roots
) -> None:
    # Every name this document would take is occupied by something that does not carry its
    # imposed PDF, so none of them is recognizably ours and none of them may be deleted.
    document = make_document(category=ITR)
    company = roots.day(TODAY) / "PETR"
    occupied = ("ITR", "ITR_V01", f"ITR_V01_{document.document_id}")
    for name in occupied:
        (company / name).mkdir(parents=True)
        (company / name / "somebody-elses.pdf").write_bytes(FOREIGN)
    queue(manifest, document)

    run(manifest, roots, FakeSource(recipes={document.identity: zip_delivery}))

    for name in occupied:
        assert (company / name / "somebody-elses.pdf").read_bytes() == FOREIGN
    landed = company / f"ITR_V01_{document.document_id}_2"
    assert (landed / f"ITR_{document.document_id}_V01.pdf").read_bytes() == PDF_BYTES


def test_a_delivery_is_refused_rather_than_placed_over_an_unrecognizable_folder(
    manifest: Manifest, roots: Roots
) -> None:
    # The fuse: a day holding this many unrecognizable containers of one category for one
    # company is a defect to look at, not a folder to add.
    document = make_document(category=ITR)
    company = roots.day(TODAY) / "PETR"
    stem = f"ITR_V01_{document.document_id}"
    names = ["ITR", "ITR_V01", stem]
    names += [f"{stem}_{ordinal}" for ordinal in range(2, _MAX_CONTAINER_NAMES + 1)]
    for name in names:
        (company / name).mkdir(parents=True)
        (company / name / "somebody-elses.pdf").write_bytes(FOREIGN)
    queue(manifest, document)

    outcome = run(manifest, roots, FakeSource(recipes={document.identity: zip_delivery}))

    assert outcome.retrying == (document.identity,)
    assert sorted(path.name for path in company.iterdir()) == sorted(names)
    for name in names:
        assert (company / name / "somebody-elses.pdf").read_bytes() == FOREIGN


def test_a_staged_file_with_no_extension_is_refused_instead_of_being_called_a_pdf(
    manifest: Manifest, roots: Roots
) -> None:
    # The extension is decided by the content, at the boundary. Inventing one here would be
    # the single place in the archive where a name was guessed.
    document = make_document()
    queue(manifest, document)

    def nameless(candidate: SourceDocument, into: Path) -> Delivery:
        into.mkdir(parents=True, exist_ok=True)
        path = into / "document"
        path.write_bytes(PDF_BYTES)
        return Delivery(
            document=candidate,
            kind=DeliveryKind.PDF,
            files=(DeliveredFile(path=path, role=FileRole.DOCUMENT, stable=True),),
        )

    outcome = run(manifest, roots, FakeSource(recipes={document.identity: nameless}))

    assert outcome.retrying == (document.identity,)
    assert list((roots.day(TODAY) / "PETR").iterdir()) == []
    assert manifest.attempts.lifetime_failures(document.identity) == 1
