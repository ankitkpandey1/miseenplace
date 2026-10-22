"""Template for a custom storage backend. Copy, rename, fill in the TODOs.

Use it with   [storage] backend = "my_package.my_module:MyStorage"   in config.toml, or publish
it under the `miseenplace.storage` entry-point group. Guide: docs/writing-a-storage-backend.md.
Prove it with tests/contract/test_storage_contract.py (add it to `backends()`).

The simplest way to be correct: keep the log as one object you append to, and everything else
as small named objects you replace whole.
"""

from pathlib import Path

from miseenplace.model import Entry, Item
from miseenplace.storage.base import Snapshot, check_name


class TemplateStorage:
    def __init__(self, root: Path, settings: dict[str, object]) -> None:
        # `root` is the local data root (config, cursors, drafts live there regardless).
        # `settings` is your [storage] table: bucket names, URLs. Never put secrets in it.
        self.root = root
        self.settings = settings

    def append(self, entry: Entry) -> None:
        raise NotImplementedError  # TODO 1: durable append of json.dumps(entry) + "\n"

    def read_entries(self, after: int = 0) -> list[Entry]:
        raise NotImplementedError  # TODO 2: entries with seq > after, in order

    def load_snapshot(self) -> Snapshot | None:
        raise NotImplementedError  # TODO 3: (seq, items) or None

    def save_snapshot(self, seq: int, items: list[Item]) -> None:
        raise NotImplementedError  # TODO 3: replace atomically

    def read_file(self, name: str) -> str | None:
        check_name(name)
        raise NotImplementedError  # TODO 4: text or None

    def write_file(self, name: str, text: str) -> None:
        check_name(name)
        raise NotImplementedError  # TODO 4: replace atomically

    def append_file(self, name: str, text: str) -> None:
        check_name(name)
        raise NotImplementedError  # TODO 4: durable append

    def refresh(self) -> None:
        return None  # TODO 5 (optional): fetch changes made by another machine

    def sync(self, message: str) -> None:
        return None  # TODO 5 (optional): publish. Raise StorageError on failure; never hide it
