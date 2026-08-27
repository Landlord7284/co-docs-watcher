"""The watch list file: comments survive, and the human's edit always wins."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from co_docs_watcher.cvm.search import MatchKind
from co_docs_watcher.cvm.ticker import PrefixSource
from co_docs_watcher.errors import WatchListConflictError, WatchListError
from co_docs_watcher.scope.models import WatchedCompany
from co_docs_watcher.scope.store import WatchList

HAND_WRITTEN = """\
# The three I actually read every morning.
companies:
  # Bought in 2019, still the largest position.
  - cvm_code: '009512'
    prefix: PETR
    prefix_source: ticker
    matched_by: ticker
    legal_name: PETROLEO BRASILEIRO S.A. PETROBRAS

  - cvm_code: '004170'  # iron ore
    prefix: VALE
    prefix_source: ticker
    matched_by: ticker
    legal_name: VALE S.A.
"""


def company(cvm_code: str = "015253", prefix: str = "ENGI") -> WatchedCompany:
    return WatchedCompany(
        cvm_code=cvm_code,
        prefix=prefix,
        prefix_source=PrefixSource.TICKER,
        legal_name="ENERGISA S.A.",
        matched_by=MatchKind.TICKER,
    )


def written(tmp_path: Path, content: str = HAND_WRITTEN) -> Path:
    path = tmp_path / "companies.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_a_missing_file_loads_as_an_empty_list(tmp_path: Path) -> None:
    watch_list = WatchList.load(tmp_path / "companies.yaml")

    assert watch_list.companies == ()
    assert watch_list.cvm_codes == frozenset()


def test_a_new_file_is_written_with_a_header_a_human_can_read(tmp_path: Path) -> None:
    path = tmp_path / "companies.yaml"
    watch_list = WatchList.load(path)
    watch_list.add(company())
    watch_list.save()

    content = path.read_text(encoding="utf-8")
    assert content.startswith("# The companies this archive is about")
    # Leading zeros are the whole point of a CVM code: it must not be written as a number.
    assert "cvm_code: '015253'" in content


def test_entries_are_read_into_decisions_not_just_strings(tmp_path: Path) -> None:
    watch_list = WatchList.load(written(tmp_path))

    assert [entry.prefix for entry in watch_list.companies] == ["PETR", "VALE"]
    assert watch_list.cvm_codes == {"009512", "004170"}
    petrobras = watch_list.get("9512")
    assert petrobras is not None
    assert petrobras.prefix_source is PrefixSource.TICKER
    assert petrobras.matched_by is MatchKind.TICKER


def test_comments_and_ordering_survive_a_rewrite(tmp_path: Path) -> None:
    path = written(tmp_path)
    watch_list = WatchList.load(path)

    assert watch_list.add(company())
    watch_list.save()

    content = path.read_text(encoding="utf-8")
    assert "# The three I actually read every morning." in content
    assert "# Bought in 2019, still the largest position." in content
    assert "# iron ore" in content
    # Appended, not sorted: the order of this file belongs to the human.
    assert [entry.prefix for entry in WatchList.load(path).companies] == ["PETR", "VALE", "ENGI"]


def test_adding_a_company_that_is_already_watched_changes_nothing(tmp_path: Path) -> None:
    path = written(tmp_path)
    watch_list = WatchList.load(path)
    before = path.read_bytes()

    assert not watch_list.add(company(cvm_code="009512", prefix="PETROBRAS"))
    watch_list.save()

    assert path.read_bytes() == before


def test_removing_returns_what_was_removed(tmp_path: Path) -> None:
    path = written(tmp_path)
    watch_list = WatchList.load(path)

    removed = watch_list.remove("00417-0")
    watch_list.save()

    assert removed is not None and removed.prefix == "VALE"
    assert WatchList.load(path).cvm_codes == {"009512"}
    assert watch_list.remove("999999") is None


def test_an_edit_made_underneath_is_never_overwritten(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = written(tmp_path)
    watch_list = WatchList.load(path)
    watch_list.add(company())

    # The human edits the file while the watcher is working.
    edited = HAND_WRITTEN + "\n# and one I am thinking about\n"
    path.write_text(edited, encoding="utf-8")

    with caplog.at_level(logging.ERROR), pytest.raises(WatchListConflictError, match="changed"):
        watch_list.save()

    assert path.read_text(encoding="utf-8") == edited
    assert "changed on disk" in caplog.text
    assert list(tmp_path.glob("*.part")) == []


def test_a_file_created_underneath_is_a_conflict_too(tmp_path: Path) -> None:
    path = tmp_path / "companies.yaml"
    watch_list = WatchList.load(path)
    watch_list.add(company())
    path.write_text("companies: []\n", encoding="utf-8")

    with pytest.raises(WatchListConflictError):
        watch_list.save()


def test_saving_twice_in_a_row_is_allowed(tmp_path: Path) -> None:
    # The guard tracks what was written, not only what was read.
    path = written(tmp_path)
    watch_list = WatchList.load(path)
    watch_list.add(company())
    watch_list.save()

    watch_list.add(company(cvm_code="013471", prefix="PLASCAR-PARTICIPACOES"))
    watch_list.save()

    assert len(WatchList.load(path).companies) == 4


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("companies: [", "not valid YAML"),
        ("- PETR\n- VALE\n", "must be a mapping"),
        ("companies:\n  PETR: 009512\n", "must be a list"),
        ("companies:\n  - PETR\n", "every entry must be a mapping"),
        ("companies:\n  - prefix: PETR\n", "cvm_code is missing"),
        # A code is read, never distilled out of free text: every one of these holds digits,
        # and reducing them to what is left would monitor a company nobody asked for.
        ("companies:\n  - cvm_code: PETR4\n    prefix: PETR\n", "not a CVM code"),
        ("companies:\n  - cvm_code: 00951-2-3\n    prefix: PETR\n", "not a CVM code"),
        ("companies:\n  - cvm_code: '0095120'\n    prefix: PETR\n", "not a CVM code"),
        ("companies:\n  - cvm_code: '009512'\n", "prefix is required"),
        # The prefix is joined to the archive root: a folder name, and nothing that walks.
        ("companies:\n  - cvm_code: '009512'\n    prefix: ../../etc\n", "not a folder name"),
        ("companies:\n  - cvm_code: '009512'\n    prefix: /tmp/loose\n", "not a folder name"),
        ("companies:\n  - cvm_code: '009512'\n    prefix: PETR/SUB\n", "not a folder name"),
        (
            "companies:\n  - cvm_code: '009512'\n    prefix: PETR\n    prefix_source: guessed\n",
            "prefix_source must be one of",
        ),
    ],
)
def test_a_file_that_cannot_be_understood_is_refused(
    tmp_path: Path, content: str, message: str
) -> None:
    # Dropping an entry that fails to parse would stop monitoring a company in silence.
    with pytest.raises(WatchListError, match=message):
        WatchList.load(written(tmp_path, content))


def test_a_code_written_the_way_the_source_prints_it_is_the_same_code(tmp_path: Path) -> None:
    # ``00951-2`` is what the listing shows a human who copies it out of the page.
    content = (
        "companies:\n"
        "  - cvm_code: 00951-2\n"
        "    prefix: PETR\n"
        "    prefix_source: ticker\n"
        "    matched_by: ticker\n"
        "    legal_name: PETROLEO BRASILEIRO S.A. PETROBRAS\n"
    )

    assert WatchList.load(written(tmp_path, content)).cvm_codes == {"009512"}


def test_an_empty_file_is_an_empty_list(tmp_path: Path) -> None:
    assert WatchList.load(written(tmp_path, "")).companies == ()
    assert WatchList.load(written(tmp_path, "companies:\n")).companies == ()
