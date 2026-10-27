"""The journal store: config, write-ahead log, snapshot and the write operations.

Layout under the data root:
  data/audit_log.jsonl   append-only log, the source of truth and the audit trail
  data/items.json        snapshot {seq, items}; a cache, rebuilt from the log if missing
  Action Items.md        rendered view, never edit by hand
  config.toml            optional config: actors, [storage], [sources.<name>] tables
  state/                 per-machine cursors, drafts and inbox queue (never synced)

Where the bytes go (local files, git, your own) is the storage backend's business; see
storage/base.py. This module owns the rules: validation, replay, actors.
"""

import copy
import hashlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Unpack, cast

from miseenplace import lock
from miseenplace.config import CONFIG_NAME, Config, load_config
from miseenplace.model import (
    TEXT_FIELDS,
    Entry,
    Extras,
    Item,
    MiseError,
    added_item,
    find,
    merge_sessions,
    now,
    parse_decision,
    parse_eta,
    parse_spent,
    raw,
    validate_entry,
    validate_item,
    with_defaults,
)
from miseenplace.render import render
from miseenplace.storage import open_storage
from miseenplace.storage.base import Storage, StorageError, StorageUnreachable

PAGE_NAME = "Action Items.md"
__all__ = ["CONFIG_NAME", "PAGE_NAME", "Config", "Store", "fingerprint", "merge_extras"]


def fingerprint(entry: Entry) -> str:
    """Identifies one log entry; a snapshot made from a different log no longer matches."""
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode("utf-8")).hexdigest()


def warn(message: str) -> None:
    print(f"miseenplace: warning: {message}", file=sys.stderr)


def merge_extras(item: Item, extra: Extras) -> None:
    """Apply work-context options: text replaces ('' clears), spent/decisions/tags accumulate."""
    ex, rec = cast(dict[str, object], extra), raw(item)
    for k in TEXT_FIELDS:
        if k in ex:
            rec[k] = str(ex[k]).strip() or None
    if "priority" in extra:
        item["priority"] = None if extra["priority"] == "none" else extra["priority"]
    if "spent" in extra:
        item["spent"] += parse_spent(extra["spent"])
    item["decisions"] = item["decisions"] + [parse_decision(d) for d in extra.get("decisions", [])]
    tags = dict.fromkeys(item["tags"] + extra.get("tags", []))
    item["tags"] = [t for t in tags if t not in extra.get("untags", [])]
    item["sessions"] = merge_sessions(item["sessions"], extra.get("sessions", []))


class Store:
    """One data root. Config lives there; the bytes live in the configured storage backend."""

    def __init__(self, root: Path, storage: Storage | None = None) -> None:
        self.root = root
        self.config = load_config(root / CONFIG_NAME)
        self.storage = storage or open_storage(root, self.config.storage)

    # ---- write-ahead log ----

    def read_log(self) -> list[Entry]:
        return self.storage.read_entries(0)

    def apply(self, items: list[Item], e: Entry) -> None:
        """Apply one log entry; a mismatched old value means the log and state diverged."""
        if e["op"] == "log":
            return
        if e["op"] == "add":
            if any(i["id"] == e["id"] for i in items):
                raise MiseError(f"seq {e['seq']}: item {e['id']} added twice")
            items.append(cast(Item, copy.deepcopy(added_item(e["changes"]))))
            return
        item = next((i for i in items if i["id"] == e["id"]), None)
        if item is None:
            raise MiseError(f"seq {e['seq']}: item {e['id']} does not exist; the log is damaged")
        for k, pair in e["changes"].items():
            old, new = cast(list[object], pair)
            if raw(item).get(k) != old:
                raise MiseError(
                    f"seq {e['seq']}: item {e['id']}.{k} is {raw(item).get(k)!r}, "
                    f"log expects {old!r}; the log itself is inconsistent here, restore it from "
                    "your backup or git history"
                )
            raw(item)[k] = new

    def load(self) -> tuple[int, list[Item]]:
        """Snapshot plus replay of newer log entries. A snapshot that does not match the log
        (ahead of it, or made from other entries) is discarded and rebuilt from the log."""
        snap = self.storage.load_snapshot()
        seq, items, entries = 0, [], self.storage.read_entries(0)
        if snap and snap[0] > 0:
            known = {e["seq"]: e for e in entries}
            if snap[0] in known and fingerprint(known[snap[0]]) == snap[2]:
                seq = snap[0]
                items = [cast(Item, with_defaults(raw(i))) for i in snap[1]]
        for e in (e for e in entries if e["seq"] > seq):
            if e["seq"] != seq + 1:
                raise MiseError(
                    f"log gap: expected seq {seq + 1}, found {e['seq']}; "
                    "restore the missing lines from git history, never renumber"
                )
            validate_entry(raw(e), self.config.actors, replay=True)
            self.apply(items, e)
            seq = e["seq"]
        return seq, items

    def commit(
        self, seq: int, items: list[Item], op: str, item: Item, before: Item | None, by: str
    ) -> Entry:
        """Validate, append to the log (durably), then write the snapshot."""
        validate_item(raw(item))
        changes: dict[str, object] = (
            raw(item)
            if before is None
            else {k: [raw(before)[k], v] for k, v in raw(item).items() if raw(before)[k] != v}
        )
        return self.append_entry(seq, items, op, item["id"], changes, by)

    def append_entry(
        self, seq: int, items: list[Item], op: str, id_: int, changes: dict[str, object], by: str
    ) -> Entry:
        entry: Entry = {
            "seq": seq + 1,
            "ts": now(),
            "op": op,
            "id": id_,
            "by": by,
            "changes": changes,
        }
        validate_entry(raw(entry), self.config.actors)
        self.storage.append(entry)
        try:  # the entry is durable; the snapshot is only a cache the next load rebuilds
            self.storage.save_snapshot(entry["seq"], items, fingerprint(entry))
        except StorageError as e:
            warn(f"entry {entry['seq']} saved, snapshot not updated ({e}); it will be rebuilt")
        return entry

    @contextmanager
    def write_cycle(self) -> Iterator[tuple[int, list[Item]]]:
        """Exclusive lock, then pull, then load: no other process or machine slips in between."""
        with lock.exclusive(self.root):
            try:
                self.storage.refresh()
            except StorageUnreachable as e:
                warn(f"using local copy ({e})")
            yield self.load()

    # ---- writes ----

    def actor(self, by: str | None) -> str:
        return by or self.config.default_actor

    def add(
        self,
        title: str,
        kind: str = "mine",
        eta: str | None = None,
        who: str | None = None,
        refs: list[str] | None = None,
        note: str | None = None,
        by: str | None = None,
        **extra: Unpack[Extras],
    ) -> Item:
        with self.write_cycle() as (seq, items):
            ts = now()
            item = cast(
                Item,
                with_defaults(
                    {
                        "id": max((i["id"] for i in items), default=0) + 1,
                        "title": title,
                        "kind": kind,
                        "state": "waiting" if kind == "waiting" else "todo",
                        "eta": parse_eta(eta),
                        "who": who,
                        "refs": refs or [],
                        "notes": [note] if note else [],
                        "created": ts,
                        "updated": ts,
                    }
                ),
            )
            merge_extras(item, extra)
            items.append(item)
            self.commit(seq, items, "add", item, None, self.actor(by))
        return item

    def update(
        self,
        key: str,
        op: str,
        by: str | None = None,
        state: str | None = None,
        eta: str | None = None,
        who: str | None = None,
        refs: list[str] | None = None,
        note: str | None = None,
        **extra: Unpack[Extras],
    ) -> Item:
        with self.write_cycle() as (seq, items):
            item = find(items, key)
            before = copy.deepcopy(item)
            if op in ("done", "drop"):
                state = "done" if op == "done" else "dropped"
            if state:
                item["state"] = state
            if eta is not None:
                item["eta"] = parse_eta(eta)
            if who:
                item["who"] = who
            if refs:
                item["refs"] = item["refs"] + refs
            if note:
                item["notes"] = item["notes"] + [note]
            merge_extras(item, extra)
            item["updated"] = now()
            self.commit(seq, items, op, item, before, self.actor(by))
            return item

    def log(
        self,
        text: str,
        topic: str | None = None,
        tags: list[str] | None = None,
        spent: str | None = None,
        refs: list[str] | None = None,
        by: str | None = None,
    ) -> Entry:
        """Diary entry not tied to an item: op `log`, id 0, no item changes."""
        changes: dict[str, object] = {
            "text": text,
            "topic": topic,
            "tags": list(dict.fromkeys(tags or [])),
            "spent": parse_spent(spent) if spent else 0,
            "refs": refs or [],
        }
        with self.write_cycle() as (seq, items):
            return self.append_entry(seq, items, "log", 0, changes, self.actor(by))

    # ---- sync ----

    def refresh(self) -> None:
        """Pull changes made elsewhere (no-op for local files)."""
        with lock.exclusive(self.root):
            self.storage.refresh()

    def sync(self, msg: str = "sync") -> None:
        """Pull, rebuild the snapshot and page from the log, then publish."""
        with lock.exclusive(self.root):
            try:
                self.storage.refresh()
            except StorageUnreachable as e:  # commit locally anyway; the publish step fails loudly
                warn(f"could not pull ({e})")
            seq, items = self.load()
            log = self.read_log()
            self.storage.save_snapshot(seq, items, fingerprint(log[-1]) if log else "")
            self.storage.write_file(PAGE_NAME, render(items, log))
            self.storage.sync(msg)
