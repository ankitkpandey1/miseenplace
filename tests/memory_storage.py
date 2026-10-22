"""A complete custom storage backend (everything in a dict), loaded by name in tests.

It exists to prove the plug-in path: `backend = "tests.memory_storage:MemoryStorage"` works and
a backend that is not the built-in two passes the same contract suite.
"""

from pathlib import Path

from miseenplace.model import Entry, Item
from miseenplace.storage.base import Snapshot, check_name

SHARED: dict[str, "MemoryStorage"] = {}


class MemoryStorage:
    def __init__(self, root: Path, settings: dict[str, object]) -> None:
        # one process-wide instance per root, so "reopening" a root sees the same data
        twin = SHARED.get(str(root))
        self.entries: list[Entry] = twin.entries if twin else []
        self.files: dict[str, str] = twin.files if twin else {}
        self.snapshot: Snapshot | None = twin.snapshot if twin else None
        SHARED.setdefault(str(root), self)

    def append(self, entry: Entry) -> None:
        self.entries.append(entry)

    def read_entries(self, after: int = 0) -> list[Entry]:
        return [e for e in self.entries if e["seq"] > after]

    def load_snapshot(self) -> Snapshot | None:
        return self.snapshot

    def save_snapshot(self, seq: int, items: list[Item], fingerprint: str) -> None:
        self.snapshot = (seq, items, fingerprint)

    def read_file(self, name: str) -> str | None:
        return self.files.get(check_name(name))

    def write_file(self, name: str, text: str) -> None:
        self.files[check_name(name)] = text

    def append_file(self, name: str, text: str) -> None:
        self.files[check_name(name)] = self.files.get(check_name(name), "") + text

    def refresh(self) -> None:
        return None

    def sync(self, message: str) -> None:
        return None


class NotAStorage:
    """Constructs fine but lacks the methods: the loader must refuse it."""

    def __init__(self, root: Path, settings: dict[str, object]) -> None:
        self.root = root
