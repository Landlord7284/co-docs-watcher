"""The lock is the kernel's, not ours: nothing here detects staleness, because none exists."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from co_docs_watcher.errors import ExitCode, LockHeldError
from co_docs_watcher.lock import RunLock

HOLDER = textwrap.dedent(
    """
    import sys, time
    from co_docs_watcher.lock import RunLock
    lock = RunLock(sys.argv[1])
    lock.acquire()
    print("held", flush=True)
    time.sleep(60)
    """
)


def start_holder(path: Path) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_a_second_instance_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "data" / "watcher.lock"
    holder = start_holder(path)
    try:
        with pytest.raises(LockHeldError) as raised:
            RunLock(path).acquire()
        assert raised.value.exit_code is ExitCode.LOCK_HELD
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_killing_the_holder_frees_the_lock_immediately(tmp_path: Path) -> None:
    path = tmp_path / "data" / "watcher.lock"
    holder = start_holder(path)
    holder.send_signal(signal.SIGKILL)
    holder.wait(timeout=10)

    # No grace period, no stale-lock detection, no pid inspection: the kernel already let go.
    deadline = time.monotonic() + 5
    while True:
        try:
            with RunLock(path):
                break
        except LockHeldError:  # pragma: no cover - only on a slow reaper
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def test_the_lock_file_lives_under_data_root_and_is_created_on_demand(tmp_path: Path) -> None:
    path = tmp_path / "data" / "watcher.lock"
    assert not path.parent.exists()
    with RunLock(path) as lock:
        assert lock.held
        assert path.is_file()
        assert path.read_text().strip() == str(os.getpid())
    assert not RunLock(path).held


def test_releasing_is_idempotent_and_reacquirable(tmp_path: Path) -> None:
    lock = RunLock(tmp_path / "watcher.lock")
    lock.acquire()
    lock.release()
    lock.release()
    lock.acquire()
    assert lock.held
    lock.release()


def test_double_acquisition_by_the_same_instance_is_a_bug_not_a_wait(tmp_path: Path) -> None:
    # flock is per file description: re-locking would succeed silently and hide the mistake.
    with RunLock(tmp_path / "watcher.lock") as lock, pytest.raises(RuntimeError, match="already"):
        lock.acquire()


def test_the_lock_is_released_when_the_body_raises(tmp_path: Path) -> None:
    path = tmp_path / "watcher.lock"
    with pytest.raises(RuntimeError, match="the run failed"), RunLock(path):
        raise RuntimeError("the run failed")
    with RunLock(path) as lock:
        assert lock.held
