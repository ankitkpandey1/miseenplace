"""Demo source: a chat service in two JSON files, so everything works without an account.

<root>/demo/inbox.json   {"me": name, "messages": [{id, where, sender, ts, text, mentioned, mine}]}
<root>/demo/sent.jsonl   one line per posted draft

It is also the smallest complete implementation of the contract: read it before writing a
new source (docs/writing-a-source.md).
"""

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import TypedDict, cast

from miseenplace.chat import Message, Target
from miseenplace.consent import Draft, Outbox
from miseenplace.model import MiseError
from miseenplace.sources.base import (
    Batch,
    Cursors,
    EditRefused,
    Person,
    SendRefused,
    SourceError,
    advance_cursor,
    fetch_batch,
)


class Row(TypedDict):
    id: int
    where: str
    sender: str
    ts: float
    text: str
    mentioned: bool
    mine: bool


class Inbox(TypedDict):
    me: str
    people: list[str]
    messages: list[Row]


def target_of(where: str) -> Target:
    if where.startswith("#"):
        channel, _, topic = where[1:].partition(" > ")
        return Target(channel, topic)
    return Target(where.removeprefix("DM: "))


def to_message(r: Row) -> Message:
    return Message(
        str(r["id"]), r["where"], r["sender"], r["ts"], r["text"], r["mentioned"], r["mine"],
        target=target_of(r["where"]),
    )  # fmt: skip


def label_of(target: Target) -> str:
    return f"#{target.to} > {target.thread}" if target.thread else f"DM: {target.to}"


class DemoSource:
    name = "demo"

    def __init__(self, root: Path) -> None:
        self.dir = root / "demo"
        self.cursors = Cursors(root, self.name)
        self.outbox = Outbox(root, self.name, self._deliver)

    @classmethod
    def from_config(cls, root: Path, settings: dict[str, object]) -> "DemoSource":
        return cls(root)

    # ---- storage ----

    def _read(self) -> Inbox:
        path = self.dir / "inbox.json"
        if not path.exists():
            raise SourceError(f"no demo data at {path}; run `miseenplace demo` first")
        return cast(Inbox, json.loads(path.read_text()))

    def _write(self, inbox: Inbox) -> None:
        (self.dir / "inbox.json").write_text(json.dumps(inbox, indent=1))

    def append(self, where: str, sender: str, text: str, mine: bool = False) -> str:
        """Add a message to the demo service (how tests and the tour simulate new chat)."""
        inbox = self._read()
        next_id = max((m["id"] for m in inbox["messages"]), default=0) + 1
        inbox["messages"].append(
            {
                "id": next_id,
                "where": where,
                "sender": sender,
                "ts": time.time(),
                "text": text,
                "mentioned": False,
                "mine": mine,
            }
        )
        self._write(inbox)
        return str(next_id)

    # ---- contract ----

    def _newest(self) -> str:
        return str(max((m["id"] for m in self._read()["messages"]), default=0))

    def _after(self, position: str) -> Batch:
        fresh = [m for m in self._read()["messages"] if m["id"] > int(position)]
        if not fresh:
            return Batch([], position)
        return Batch([to_message(m) for m in fresh if not m["mine"]], str(fresh[-1]["id"]))

    def fetch(self, cursor: str, init: bool = False) -> Batch:
        return fetch_batch(self.cursors, cursor, init, self._newest, self._after)

    def advance(self, cursor: str, batch: Batch) -> None:
        advance_cursor(self.cursors, cursor, batch)

    def new(self, cursor: str, init: bool = False) -> list[Message]:
        batch = self.fetch(cursor, init)
        self.advance(cursor, batch)
        return batch.messages

    def digest(
        self, days: float, include_noise: bool = False, only_mine: bool = False
    ) -> list[Message]:
        since = time.time() - days * 86400
        rows = [m for m in self._read()["messages"] if m["ts"] >= since]
        return [to_message(m) for m in rows if m["mine"] or not only_mine]

    def message(self, message_id: str) -> Message:
        row = next((m for m in self._read()["messages"] if str(m["id"]) == message_id), None)
        if row is None:
            raise SourceError(f"no message {message_id}")
        return to_message(row)

    def thread(self, target: Target, limit: int = 40) -> list[Message]:
        rows = [m for m in self._read()["messages"] if m["where"] == label_of(target)]
        return [to_message(m) for m in rows[-limit:]]

    def draft(self, target: Target, text: str) -> Draft:
        inbox = self._read()
        if target.reply_to:
            row = next((m for m in inbox["messages"] if str(m["id"]) == target.reply_to), None)
            if row is None:
                raise SourceError(f"no message {target.reply_to} to reply to")
            quoted = "> " + row["text"].replace("\n", "\n> ")
            where = row["where"]
            text = f"{quoted}\n\n{text}"
            target = Target(where, None, target.reply_to, where)
        return self.outbox.draft(target, text)

    def send(self, draft_id: str) -> str:
        return self.outbox.send(draft_id)

    def _own_row(self, message_id: str) -> tuple[Inbox, Row]:
        inbox = self._read()
        row = next((m for m in inbox["messages"] if str(m["id"]) == message_id), None)
        if row is None:
            raise MiseError(f"no message {message_id}")
        if not row["mine"]:
            raise MiseError(f"message {message_id} is not yours")
        return inbox, row

    def edit(self, message_id: str, text: str) -> Draft:
        _, row = self._own_row(message_id)
        return self.outbox.draft(replace(target_of(row["where"]), edit_of=message_id), text)

    def users(self, query: str) -> list[Person]:
        people = self._read()["people"]
        return [
            Person(p.lower(), p, p.lower() + "@example.com")
            for p in people
            if query.lower() in p.lower()
        ]

    def _deliver(self, target: Target, text: str) -> str:
        if target.edit_of:
            try:
                inbox, row = self._own_row(target.edit_of)
            except MiseError as e:
                raise EditRefused(str(e)) from None
            row["text"] = text
            self._write(inbox)
            return target.edit_of
        try:
            me = self._read()["me"]
        except SourceError as e:  # the service is not there: nothing can have been posted
            raise SendRefused(str(e)) from None
        where = target.label or label_of(target)
        posted = self.append(where, me, text, mine=True)
        with (self.dir / "sent.jsonl").open("a") as f:
            f.write(json.dumps({"id": posted, "where": where, "text": text}) + "\n")
        return posted
