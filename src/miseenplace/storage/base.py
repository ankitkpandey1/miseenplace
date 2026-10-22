"""The storage contract: where the journal's durable bytes live.

The store (store.py) owns the rules: validation, replay, actors. A storage backend only keeps
bytes safely. It has three jobs:

* the audit log: append an entry durably, read entries after a sequence number;
* the snapshot: a cache of the current items that the store can always rebuild from the log;
* named text files: the rendered page, style files, the sent-message corpus, the request log.

`refresh()` brings in changes made elsewhere (git pull) and `sync()` publishes local changes
(git commit and push). Both are no-ops for plain local files. Failures raise StorageError and
must be loud: a backend never swallows a failed write or push.
"""

from typing import Protocol, runtime_checkable

from miseenplace.model import Entry, Item, MiseError

Snapshot = tuple[int, list[Item], str]  # (seq, items, fingerprint of the log entry at seq)


class StorageError(MiseError):
    """The backend could not read, write or publish. Never swallowed."""


class StorageUnreachable(StorageError):
    """The remote could not be reached or refused us. Local data is intact; try again later."""


@runtime_checkable
class Storage(Protocol):
    def append(self, entry: Entry) -> None:
        """Add one entry to the end of the log. Durable when it returns (fsync or equivalent)."""
        ...

    def read_entries(self, after: int = 0) -> list[Entry]:
        """Entries with seq > `after`, in log order."""
        ...

    def load_snapshot(self) -> Snapshot | None:
        """The cached (seq, items, fingerprint), or None when there is none or it is unreadable."""
        ...

    def save_snapshot(self, seq: int, items: list[Item], fingerprint: str) -> None:
        """Replace the snapshot atomically. `fingerprint` is opaque: store it, return it."""
        ...

    def read_file(self, name: str) -> str | None:
        """A named text file (e.g. `style/zulip.md`), or None when it does not exist."""
        ...

    def write_file(self, name: str, text: str) -> None:
        """Create or replace a named text file atomically."""
        ...

    def append_file(self, name: str, text: str) -> None:
        """Append `text` to a named file, creating it. Durable when it returns."""
        ...

    def refresh(self) -> None:
        """Pick up changes made elsewhere (pull). No-op when there is nowhere else."""
        ...

    def sync(self, message: str) -> None:
        """Publish local changes (commit and push). No-op when there is nowhere to publish."""
        ...


def check_name(name: str) -> str:
    """A relative file name inside the data root; refuses absolute paths and `..`."""
    parts = name.replace("\\", "/").split("/")
    if not name or name.startswith("/") or ".." in parts or "" in parts:
        raise StorageError(f"bad file name {name!r}: use a relative path without '..'")
    return name
