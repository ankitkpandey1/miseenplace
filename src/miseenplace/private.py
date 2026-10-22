"""Files and directories for personal data: owner-only (0600 files, 0700 directories)."""

import os
from pathlib import Path

FILE_MODE = 0o600
DIR_MODE = 0o700


def make_dirs(path: Path) -> None:
    """Create `path` and any missing parents, each owner-only. Existing directories are kept."""
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=DIR_MODE, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    """Replace `path` atomically with `text`, created owner-only and fsynced before the swap."""
    make_dirs(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_text(path: Path, text: str) -> None:
    """Append `text` durably. A file that does not end in a newline gets one first."""
    make_dirs(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, FILE_MODE)
    with os.fdopen(fd, "a+b") as f:
        if f.seek(0, os.SEEK_END) and (f.seek(-1, os.SEEK_END), f.read(1))[1] != b"\n":
            f.write(b"\n")
        f.write(text.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def create_exclusive(path: Path, text: str) -> None:
    """Create `path` owner-only, failing with FileExistsError if it exists."""
    make_dirs(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
