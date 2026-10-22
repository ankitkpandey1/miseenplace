"""`reconcile`: compare each open journal item with where it came from, propose a state change.

Chat refs (a message link, or the `[inbox:src:N]` mark `triage` writes) say whether I answered
and whether they replied since. GitHub PR refs say whether the PR is closed. The rules here are
pure over thread messages and `gh` JSON; the only I/O is `gh_pr_json` and the source lookups.
"""

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from miseenplace.chat import Message, Target
from miseenplace.inbox import is_after
from miseenplace.model import Item, MiseError, now
from miseenplace.sources.base import Lookup, Source
from miseenplace.store import Store

LINK_ID_RE = re.compile(r"#narrow/(?:\S*/)?(?:near|with|id)/(\d+)")
MARK_RE = re.compile(r"\[inbox:([\w-]+):(\d+)\]")  # what `triage` writes: source, message id
PR_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
GH_TIMEOUT = 30
NO_GH = "skipped PR checks: gh is not installed"


@dataclass(frozen=True)
class Change:
    item_id: int
    current: str
    proposed: str
    reason: str

    def line(self) -> str:
        if self.proposed == self.current:  # a signal, not a change
            return f"#{self.item_id} still {self.current}: {self.reason}"
        return f"#{self.item_id} {self.current} -> {self.proposed}: {self.reason}"

    @property
    def changes_state(self) -> bool:
        return self.proposed != self.current


def chat_refs(item: Item) -> list[tuple[str | None, str]]:
    """(source or None, message id) from the item's links and context, in order, once each."""
    links = [(None, m) for r in item["refs"] for m in LINK_ID_RE.findall(r)]
    marks = MARK_RE.findall(item["context"] or "")
    return list(dict.fromkeys([*links, *marks]))


def pr_urls(item: Item) -> list[str]:
    return list(dict.fromkeys(u for r in item["refs"] for u in PR_RE.findall(r)))


def chat_change(item: Item, ref: Message, thread: list[Message]) -> Change | None:
    """My last message at or after `ref`, and whether they answered since."""
    mine = [m for m in thread if m.mine and (m.id == ref.id or is_after(m, ref))]
    if not mine:
        return None
    last = mine[-1]
    replies = [m for m in thread if not m.mine and is_after(m, last)]
    if replies:
        current = item["state"]
        proposed = "todo" if current == "waiting" else current
        return Change(item["id"], current, proposed, f"new reply {replies[-1].id} after {last.id}")
    return Change(item["id"], item["state"], "waiting", f"you posted {last.id}; no reply since")


def pr_change(item: Item, url: str, raw: str) -> Change | None:
    """`gh pr view --json state,reviewDecision` output -> done when the PR is MERGED or CLOSED."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise MiseError(f"gh gave unreadable output for {url}") from None
    state = str(data.get("state", "")).upper() if isinstance(data, dict) else ""
    if state in ("MERGED", "CLOSED"):
        return Change(item["id"], item["state"], "done", f"{url} is {state}")
    return None


def first_line(text: str) -> str:
    return (text.strip().splitlines() or ["no output"])[0]


def gh_pr_json(url: str) -> str:
    done = subprocess.run(
        ["gh", "pr", "view", url, "--json", "state,reviewDecision"],
        capture_output=True,
        text=True,
        check=False,
        timeout=GH_TIMEOUT,
    )
    if done.returncode != 0:
        raise MiseError(f"gh could not read {url}: {first_line(done.stderr)}")
    return done.stdout


def pr_checker() -> Callable[[str], str] | None:
    return gh_pr_json if shutil.which("gh") else None


def rank(c: Change) -> int:
    return {"done": 0, "todo": 1}.get(c.proposed, 2)


def pick(found: list[Change]) -> Change | None:
    """The finding that matters most for one item: done, then a new reply, then waiting."""
    return min(found, key=rank) if found else None


class CachedLookup:
    """The source opened on first use; each message and thread read once per run."""

    def __init__(self, open_source: Callable[[], Lookup]) -> None:
        self._open = open_source
        self._source: Lookup | None = None
        self._messages: dict[str, Message] = {}
        self._threads: dict[Target, list[Message]] = {}

    def source(self) -> Lookup:
        if self._source is None:
            self._source = self._open()
        return self._source

    def message(self, message_id: str) -> Message:
        if message_id not in self._messages:
            self._messages[message_id] = self.source().message(message_id)
        return self._messages[message_id]

    def thread(self, target: Target) -> list[Message]:
        if target not in self._threads:
            self._threads[target] = self.source().thread(target)
        return self._threads[target]


def chat_changes(item: Item, chat: CachedLookup, notes: list[str]) -> list[Change]:
    found: list[Change] = []
    for origin, message_id in chat_refs(item):
        if origin and origin != chat.source().name:
            notes.append(
                f"skipped #{item['id']}: ref {origin}:{message_id} is not {chat.source().name}"
            )
            continue
        ref = chat.message(message_id)
        change = chat_change(item, ref, chat.thread(ref.target)) if ref.target else None
        if change:
            found.append(change)
    return found


def item_changes(
    item: Item, chat: CachedLookup, pr_json: Callable[[str], str] | None, notes: list[str]
) -> list[Change]:
    found = chat_changes(item, chat, notes) if chat_refs(item) else []
    if pr_json is None:
        return found
    prs = (pr_change(item, url, pr_json(url)) for url in pr_urls(item))
    return found + [c for c in prs if c]


def propose(
    items: list[Item], chat: Callable[[], Lookup], pr_json: Callable[[str], str] | None
) -> tuple[list[Change], list[str]]:
    """One finding per item, plus notes (skipped checks) for stderr."""
    lookup = CachedLookup(chat)
    notes = [NO_GH] if pr_json is None and any(pr_urls(i) for i in items) else []
    changes: list[Change] = []
    for item in items:
        try:
            best = pick(item_changes(item, lookup, pr_json, notes))
        except MiseError as e:  # one unreachable ref must not hide the other items
            notes.append(f"skipped #{item['id']}: {e}")
            continue
        if best:
            changes.append(best)
    return changes, notes


def apply(store: Store, changes: list[Change], by: str | None) -> int:
    """Through `Store.update`, as `set` and `done` do. Only items whose state differs change."""
    today = now().split()[0]
    todo = [c for c in changes if c.changes_state]
    for c in todo:
        store.update(
            str(c.item_id),
            "done" if c.proposed == "done" else "set",
            by,
            state=c.proposed,
            note=f"reconcile {today}: {c.reason}",
        )
    return len(todo)


def report(changes: list[Change], applying: bool) -> str:
    """Signals and changes, one line each; a dry run ends with how many changes --apply makes."""
    n = sum(c.changes_state for c in changes)
    lines = [c.line() for c in changes]
    if applying:
        return "\n".join(lines)
    if n:
        return "\n".join(
            [*lines, f"{n} {'change' if n == 1 else 'changes'} proposed. Run with --apply."]
        )
    return "\n".join([*lines, "0 changes"])


def lookup_of(source: Source) -> Lookup:
    if not isinstance(source, Lookup):
        raise MiseError(
            f"source {source.name!r} cannot look up a message; reconcile skips chat refs"
        )
    return source
