"""The source seam: no module outside ``rad/`` imports ``rad/``.

The rule has one exception, ``run.py``, the composition root: something has to instantiate the
adapter and hand it to the pipeline as a ``Source``. Test modules are exempt by construction —
contract tests exist precisely to import ``rad/`` and pin the wire format.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "co_docs_watcher"

# The composition root is the only module allowed to import the source adapter.
SEAM_ALLOWLIST = frozenset({"run.py"})


def imports_rad(source: str, filename: str = "<test>") -> bool:
    """Whether a module's source imports ``rad/``, in any of the spellings that reach it."""
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "co_docs_watcher.rad" or alias.name.startswith("co_docs_watcher.rad.")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level and module.split(".")[0] == "rad":
                return True
            if module == "co_docs_watcher.rad" or module.startswith("co_docs_watcher.rad."):
                return True
            if module == "co_docs_watcher" and any(alias.name == "rad" for alias in node.names):
                return True
    return False


def modules_violating_the_seam() -> list[str]:
    violations = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC)
        if relative.parts[0] == "rad" or relative.as_posix() in SEAM_ALLOWLIST:
            continue
        if imports_rad(path.read_text(encoding="utf-8"), filename=str(path)):
            violations.append(relative.as_posix())
    return violations


def test_nothing_outside_rad_imports_rad() -> None:
    assert modules_violating_the_seam() == []


def test_the_detector_catches_every_spelling_of_the_violation() -> None:
    # A guard that cannot fail guards nothing: these are the imports the rule forbids.
    forbidden = [
        "import co_docs_watcher.rad",
        "import co_docs_watcher.rad.client",
        "from co_docs_watcher.rad import client",
        "from co_docs_watcher.rad.listing import sweep",
        "from co_docs_watcher import rad",
        "from .rad import client",
        "from ..rad.download import fetch",
    ]
    assert [source for source in forbidden if not imports_rad(source)] == []


def test_the_detector_does_not_cry_wolf() -> None:
    allowed = [
        "from co_docs_watcher.models import SourceDocument",
        "from co_docs_watcher.source import Source",
        "import co_docs_watcher.radar",  # not the package: prefix matching must not fire
        "from co_docs_watcher import radiology",
        "from . import radar",
    ]
    assert [source for source in allowed if imports_rad(source)] == []
