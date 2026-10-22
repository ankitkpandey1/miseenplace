"""An exclusive lock on the data root, so two processes never interleave a read-modify-write.

POSIX uses flock, Windows uses msvcrt.locking. The lock is advisory and released when the
process exits, so a crash never leaves it held.
"""

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from miseenplace import private
from miseenplace.model import MiseError

LOCK_NAME = ".lock"
WAIT_SECONDS = 30.0


@contextmanager
def exclusive(root: Path, wait: float = WAIT_SECONDS) -> Iterator[None]:
    private.make_dirs(root)
    path = root / LOCK_NAME
    with path.open("a+b") as f:
        deadline = time.monotonic() + wait
        while not _try_lock(f.fileno()):
            if time.monotonic() > deadline:
                raise MiseError(
                    f"another miseenplace process holds {path} for over {wait:g}s; "
                    "wait for it, or delete the file if no process is running"
                )
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(f.fileno())


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
