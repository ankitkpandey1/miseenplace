"""The inbox: new messages in, a short list of actionable items out.

Everything deterministic happens here so the agent never reads raw chat: ingest from the
source (own cursor), drop what the user already answered, classify, compact, and keep a queue
the agent works through with `triage`. One JSON file per message under
<root>/state/inbox/<source>/, so a watcher and an agent never write the same bytes.

The suggested action is a plain heuristic over the text, not a verdict: the agent may override it.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from miseenplace import private
from miseenplace.chat import Message, Target, load
from miseenplace.model import MiseError
from miseenplace.sources.base import Source

ACTIONS = ("reply", "todo", "waiting", "ignore")
ASK_WIDTH = 120
WHERE_WIDTH = 28
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

DEADLINE = r"by (?:mon|tue|wed|thu|fri|sat|sun|today|tomorrow|eod|end of)\w*"
ASK_RE = re.compile(
    rf"\?|\b(?:can|could|would|will) you\b|\bplease\b|\bpls\b|\bneed(?:s)?\b|\beta\b"
    rf"|\blet me know\b|\b{DEADLINE}",
    re.IGNORECASE,
)
WORK_RE = re.compile(
    rf"\b(?:review|approve|merge|write|send|fix|update|prepare|draft|share|check)\b|{DEADLINE}",
    re.IGNORECASE,
)
PROMISE_RE = re.compile(
    r"\b(?:i(?:'ll| will) (?:send|share|check|get back|look|update|do)|working on it)\b",
    re.IGNORECASE,
)
MENTION_RE = re.compile(r"@_?\*\*([^*|]+)(?:\|\d+)?\*\*")
QUOTE_RE = re.compile(r"(`{3,}) ?quote.*?\1", re.DOTALL)
SAID_RE = re.compile(r"^\s*@?_?\*\*[^*]+\*\*\s*\[said\]\([^)]*\):?")  # a quote-reply's header
URL_RE = re.compile(r"https?://[^\s)>\]]+")


@dataclass
class InboxItem:
    id: str
    who: str
    where: str
    text: str
    ts: float
    mentioned: bool
    url: str | None
    target: Target | None
    status: str = "new"  # new | open | auto-ignored | answered | todo | waiting | ignored | drafted
    action: str = ""  # suggested: reply | todo | waiting | ignore
    ref: str = ""  # journal item id or draft id once handled
    shown: list[str] = field(default_factory=list)  # consumers that have been shown this item

    def ask(self) -> str:
        return compact(self.text, ASK_WIDTH)

    def line(self) -> str:
        where = compact(self.where, WHERE_WIDTH)
        return f"{self.id} · {self.who} · {where} · {self.ask()} · {self.action}"

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "who": self.who,
            "where": self.where,
            "ask": self.ask(),
            "action": self.action,
            "url": self.url,
        }


def compact(text: str, width: int) -> str:
    """One line: quotes and their reply header dropped, mentions unwrapped, cut at `width`."""
    text = MENTION_RE.sub(r"@\1", SAID_RE.sub("", QUOTE_RE.sub("", text)))
    one = " ".join(text.split())
    return one if len(one) <= width else one[: width - 1].rstrip() + "…"


def suggest(item: InboxItem) -> str:
    """reply | todo | waiting | ignore, from the words alone."""
    text = QUOTE_RE.sub("", item.text)
    if ASK_RE.search(text):
        return "todo" if WORK_RE.search(text) else "reply"
    if PROMISE_RE.search(text):
        return "waiting"
    return "ignore"


class Anchor(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def ts(self) -> float: ...


def is_after(m: Message, item: Anchor) -> bool:
    """Message order, not clock order: ids rise with arrival, timestamps can tie or skew."""
    if m.id.isdigit() and item.id.isdigit():
        return int(m.id) > int(item.id)
    return m.ts > item.ts


def answered(item: InboxItem, thread: list[Message]) -> bool:
    """Did I write in this thread after the message? Then there is nothing to do."""
    return any(m.mine and is_after(m, item) for m in thread)


class Inbox:
    def __init__(self, root: Path, source: Source) -> None:
        self.source = source
        self.dir = root / "state" / "inbox" / source.name

    def _path(self, item_id: str) -> Path:
        if not ID_RE.match(item_id):
            raise MiseError(f"bad inbox id {item_id!r}")
        return self.dir / f"{item_id}.json"

    def _save(self, item: InboxItem) -> None:
        private.write_text(self._path(item.id), json.dumps(asdict(item)))

    def get(self, item_id: str) -> InboxItem:
        path = self._path(item_id)
        if not path.exists():
            raise MiseError(f"no inbox item {item_id}; list them with `inbox`")
        return self._load(path)

    def _load(self, path: Path) -> InboxItem:
        data = json.loads(path.read_text(encoding="utf-8"))
        target = load(Target, data["target"]) if data["target"] else None
        return load(InboxItem, {**data, "target": target})

    def items(self) -> list[InboxItem]:
        found = [self._load(p) for p in self.dir.glob("*.json")] if self.dir.exists() else []
        return sorted(found, key=lambda i: (i.ts, i.id))

    def set_status(self, item: InboxItem, status: str, ref: str = "") -> None:
        item.status, item.ref = status, ref
        self._save(item)

    # ---- ingest ----

    def ingest(self, cursor: str) -> list[InboxItem]:
        """Queue this consumer's new messages, classify them, return what it has not been shown.

        Order matters for a crash or a network error at any point: messages are queued to disk
        BEFORE the cursor moves, and classification comes after both, so nothing is lost; items
        opened before an error are still returned (unseen) by the next call.
        """
        batch = self.source.fetch(cursor)
        for m in batch.messages:
            if not self._path(m.id).exists():
                self._save(from_message(m))
        self.source.advance(cursor, batch)
        self._classify([i for i in self.items() if i.status == "new"])
        return self.unseen(cursor)

    def unseen(self, cursor: str) -> list[InboxItem]:
        return [i for i in self.open_items() if cursor not in i.shown]

    def mark_shown(self, items: list[InboxItem], cursor: str) -> None:
        """Call after the items were written to the consumer: only then are they 'shown'."""
        for item in items:
            if cursor not in item.shown:
                item.shown.append(cursor)
                self._save(item)

    def recheck(self) -> None:
        """Drop open items the user answered since (for instance in the chat app itself)."""
        threads: dict[str, list[Message]] = {}
        for item in self.open_items():
            if item.target is None:
                continue
            if item.where not in threads:
                threads[item.where] = self.source.thread(item.target, 40)
            if answered(item, threads[item.where]):
                self.set_status(item, "answered")

    def _classify(self, fresh: list[InboxItem]) -> list[InboxItem]:
        opened: list[InboxItem] = []
        threads: dict[str, list[Message]] = {}
        for item in fresh:
            if item.target is not None and item.where not in threads:
                threads[item.where] = self.source.thread(item.target, 40)
            if answered(item, threads.get(item.where, [])):
                self.set_status(item, "answered")
                continue
            item.action = suggest(item)
            self.set_status(item, "auto-ignored" if item.action == "ignore" else "open")
            if item.status == "open":
                opened.append(item)
        return opened

    def open_items(self) -> list[InboxItem]:
        return [i for i in self.items() if i.status == "open"]


def from_message(m: Message) -> InboxItem:
    link = URL_RE.search(m.text)
    url = m.url or (link.group(0) if link else None)
    return InboxItem(m.id, m.sender, m.where, m.text, m.ts, m.mentioned, url, m.target)


def render_lines(items: list[InboxItem]) -> str:
    return "\n".join(i.line() for i in items)


def render_json(items: list[InboxItem]) -> str:
    return json.dumps([i.public() for i in items], separators=(",", ":"))


def title_of(item: InboxItem) -> str:
    return compact(item.text, 80)


def topic_of(item: InboxItem) -> str:
    """The thread/topic part of `#channel > topic`, else the whole label."""
    return item.where.split(" > ", 1)[-1]
