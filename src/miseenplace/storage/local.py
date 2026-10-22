"""Local files, the default backend.

<root>/data/audit_log.jsonl   the log (fsync on every append)
<root>/data/items.json        the snapshot, replaced atomically
<root>/<name>                 every other named file

Everything is created owner-only (files 0600, directories 0700) and read and written as UTF-8.
"""

import json
from pathlib import Path
from typing import cast

from miseenplace import private
from miseenplace.model import Entry, Item
from miseenplace.storage.base import Snapshot, StorageError, check_name

LOG_NAME = "data/audit_log.jsonl"
SNAP_NAME = "data/items.json"


def parse_log(text: str, where: str) -> list[Entry]:
    """Entries of a log file. A damaged line is refused with the exact line to fix."""
    lines = text.split("\n")
    torn = bool(lines[-1].strip())  # a complete log ends with a newline
    entries: list[Entry] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict) or not isinstance(entry.get("seq"), int):
                raise ValueError("not a log entry")
        except ValueError:
            kind = (
                "ends with a torn line (a write was cut off)"
                if torn and number == len(lines)
                else "has a damaged line"
            )
            raise StorageError(
                f"{where} {kind}: line {number} is not a valid entry. Nothing was changed. "
                f"Open the file, check line {number} against your backups or git history, "
                "then fix or delete that one line and retry."
            ) from None
        entries.append(cast(Entry, entry))
    return entries


class LocalStorage:
    def __init__(self, root: Path, settings: dict[str, object] | None = None) -> None:
        self.root = root

    def path(self, name: str) -> Path:
        return self.root / check_name(name)

    # ---- log ----

    def append(self, entry: Entry) -> None:
        self.append_file(LOG_NAME, json.dumps(entry) + "\n")

    def read_entries(self, after: int = 0) -> list[Entry]:
        text = self.read_file(LOG_NAME) or ""
        return [e for e in parse_log(text, str(self.path(LOG_NAME))) if e["seq"] > after]

    # ---- snapshot ----

    def load_snapshot(self) -> Snapshot | None:
        """The cache, or None when missing or unreadable: the store rebuilds it from the log."""
        try:
            text = self.read_file(SNAP_NAME)
            if text is None:
                return None
            snap = json.loads(text)
            return (
                cast(int, snap["seq"]),
                cast(list[Item], snap["items"]),
                cast(str, snap["fingerprint"]),
            )
        except (ValueError, KeyError, TypeError, StorageError):
            return None

    def save_snapshot(self, seq: int, items: list[Item], fingerprint: str) -> None:
        body = {"seq": seq, "fingerprint": fingerprint, "items": items}
        self.write_file(SNAP_NAME, json.dumps(body, indent=1))

    # ---- named files ----

    def read_file(self, name: str) -> str | None:
        path = self.path(name)
        try:
            return path.read_text(encoding="utf-8") if path.exists() else None
        except (OSError, UnicodeDecodeError) as e:
            raise StorageError(f"cannot read {path}: {e}") from None

    def write_file(self, name: str, text: str) -> None:
        path = self.path(name)
        try:
            private.write_text(path, text)
        except OSError as e:
            raise StorageError(f"cannot write {path}: {e}") from None

    def append_file(self, name: str, text: str) -> None:
        path = self.path(name)
        try:
            private.append_text(path, text)
        except OSError as e:
            raise StorageError(f"cannot append to {path}: {e}") from None

    # ---- nowhere else to pull from or push to ----

    def refresh(self) -> None:
        return None

    def sync(self, message: str) -> None:
        return None
