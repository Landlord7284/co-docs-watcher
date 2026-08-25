"""Single-instance guarantee: ``flock`` on a file under ``data_root``.

``flock`` and not a pidfile. A pidfile has to answer "is the process that wrote this still
alive?", and every answer is wrong somewhere: PIDs are reused, they are namespace-local (a
containerized run is typically PID 1), and a crash leaves a file claiming a lock nobody holds.
The kernel releases an ``flock`` when the owning file description goes away — process exit,
crash, kill -9 alike — so there is no stale lock to detect and no staleness code to get wrong.

The lock is advisory and local. ``data_root`` must live on a filesystem local to the process
for the same reason SQLite must: locking over SMB/NFS is unreliable.
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from types import TracebackType

from co_docs_watcher.errors import LockHeldError

__all__ = ["RunLock"]

logger = logging.getLogger(__name__)


class RunLock:
    """Context manager holding the exclusive run lock.

    Acquisition is non-blocking: a second instance does not queue behind the first, it exits
    with ``LockHeldError`` (exit code 3). Overlapping invocations are expected — a run that
    takes longer than the interval between runs is normal — and waiting would only pile up
    processes that will all sweep the same window anyway.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            raise RuntimeError(f"lock already held by this instance: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise LockHeldError(
                f"another instance is running: the lock at {self.path} is held"
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        logger.debug("acquired the run lock at %s", self.path)

    def release(self) -> None:
        """Release the lock. Closing is enough; the kernel would do it for us anyway."""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        logger.debug("released the run lock at %s", self.path)

    @property
    def held(self) -> bool:
        return self._fd is not None

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
