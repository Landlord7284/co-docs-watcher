"""Creation modes for the shared archive, and the three ways they are applied.

``documents_root`` is handed to other people — mounted over SMB, synced, read by someone who
never runs this program — so what it may be read by is a decision this watcher declares, never
one it inherits from whatever umask started the process. In a container that umask comes from
the base image and the init; on a workstation, from the login shell. Neither is an answer to
"who is allowed to read the archive?".

Declaring it takes an explicit ``chmod`` after every creation, for three reasons that are
properties of the platform rather than of this code:

1. the kernel writes ``mode & ~umask``, so the mode passed to ``mkdir`` is a ceiling and not a
   value — under ``0o077``, ``mkdir(0o755)`` lands ``0o700``;
2. ``parents=True`` does not carry ``mode`` to the parents it creates: they are born from
   ``0o777``, which is why creating ``<date>/<prefix>`` in one call would otherwise produce two
   different modes for the two levels;
3. ``exist_ok=True`` reapplies nothing, so a directory created by an earlier run keeps the mode
   it was born with — re-stamping is what repairs an archive that predates this rule, and it
   costs nothing to redo.

``os.chmod`` is not masked, so the stamp lands the mode that was written down.

Files are stamped in **staging**, before the ``os.replace`` that places them: a placement is a
single rename, and a file must never be visible in the archive under a mode that is about to
be corrected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_DIRECTORY_MODE",
    "DEFAULT_FILE_MODE",
    "DEFAULT_MODES",
    "ArchiveModes",
    "ensure_directory",
    "stamp_file",
    "stamp_tree",
]

#: Readable by everyone, writable by the owner: the archive is published, not shared for
#: editing. A stricter pair is a configuration decision, not a different code path.
DEFAULT_DIRECTORY_MODE = 0o755
DEFAULT_FILE_MODE = 0o644

#: The highest mode there is: four octal digits, the first carrying setuid/setgid/sticky.
MAX_MODE = 0o7777


@dataclass(frozen=True, slots=True)
class ArchiveModes:
    """The two modes everything under ``documents_root`` is created with."""

    directory_mode: int = DEFAULT_DIRECTORY_MODE
    file_mode: int = DEFAULT_FILE_MODE


#: What a caller that names no modes gets: the declared defaults, never the process umask.
DEFAULT_MODES = ArchiveModes()


def ensure_directory(path: Path, modes: ArchiveModes) -> Path:
    """Create ``path`` and every missing parent, and stamp each level with the archive mode.

    The parents are stamped because ``parents=True`` creates them from ``0o777``, and ``path``
    itself is stamped even when it already existed, because that is what brings a date
    directory built by an earlier run up to the mode now declared.
    """
    missing = [ancestor for ancestor in path.parents if not ancestor.exists()]
    path.mkdir(parents=True, exist_ok=True)
    for created in missing:
        os.chmod(created, modes.directory_mode)
    os.chmod(path, modes.directory_mode)
    return path


def stamp_file(path: Path, modes: ArchiveModes) -> None:
    """Give one file the archive's file mode. Called on the staging copy, before placement."""
    os.chmod(path, modes.file_mode)


def stamp_tree(root: Path, modes: ArchiveModes) -> None:
    """Stamp a whole staging tree — the directory itself, its subdirectories, and every file.

    A structured delivery is placed by renaming its staging directory into the archive in one
    call, so the tree has to be right before the rename rather than after it.
    """
    os.chmod(root, modes.directory_mode)
    for parent, directories, files in os.walk(root):
        for name in directories:
            os.chmod(Path(parent) / name, modes.directory_mode)
        for name in files:
            os.chmod(Path(parent) / name, modes.file_mode)
