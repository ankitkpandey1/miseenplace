"""`today`: the threads of the last days that involve me, and which of them still wait on me.

Pure over the messages a source's `digest` returns; no fetching of its own.
"""

import re
import time
from dataclasses import dataclass

from miseenplace.chat import Message
from miseenplace.inbox import compact

LAST = {"awaiting you": 3, "you replied last": 2, "fyi": 2}  # answered threads need less
AWAITING, REPLIED, FYI = "awaiting you", "you replied last", "fyi"
STATES = (AWAITING, REPLIED, FYI)
LABEL_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
BARE_LINK_RE = re.compile(r"https?://\S+")
MARKUP_RE = re.compile(r"\*\*|__|`|^#+\s|^>\s", re.MULTILINE)


@dataclass(frozen=True)
class Thread:
    where: str
    messages: list[Message]

    @property
    def state(self) -> str:
        last = self.messages[-1]
        if last.mine:
            return REPLIED
        return AWAITING if last.mentioned or self.where.startswith("DM:") else FYI


def involving_me(messages: list[Message]) -> list[Thread]:
    """Threads with a message of mine, an @-mention of me, or a DM; awaiting-you first."""
    groups: dict[str, list[Message]] = {}
    for m in sorted(messages, key=lambda m: m.ts):
        groups.setdefault(m.where, []).append(m)
    threads = [
        Thread(where, rows)
        for where, rows in groups.items()
        if where.startswith("DM:") or any(m.mine or m.mentioned for m in rows)
    ]
    newest_first = sorted(threads, key=lambda t: t.messages[-1].ts, reverse=True)
    return sorted(newest_first, key=lambda t: t.state != AWAITING)


def plain(text: str) -> str:
    """Links and markdown out: `[label](url)` -> label, a bare url -> (link), emphasis dropped."""
    text = LABEL_LINK_RE.sub(r"\1", text)
    text = BARE_LINK_RE.sub("(link)", text)
    return " ".join(MARKUP_RE.sub("", text).split())


def message_row(m: Message, width: int) -> str:
    t = time.strftime("%H:%M", time.localtime(m.ts))
    who = "you" if m.mine else (m.sender.split() or ["?"])[0].lower()
    body = compact(plain(compact(m.text, len(m.text))), width) or "(quote)"
    return f"  {t} {who} {body}"


def render(threads: list[Thread], days: float, width: int) -> str:
    if not threads:
        return "0 threads today" if days <= 1 else f"0 threads in {days:g}d"
    counts = [sum(t.state == s for t in threads) for s in STATES]
    noun = "thread" if len(threads) == 1 else "threads"
    head = f"{len(threads)} {noun}: {counts[0]} awaiting you, {counts[1]} you replied last"
    lines = [f"{head}, {counts[2]} fyi"]
    for t in threads:
        n = len(t.messages)
        lines.append(f"{t.state}  {t.where}  {n} {'msg' if n == 1 else 'msgs'}")
        lines.extend(message_row(m, width) for m in t.messages[-LAST[t.state] :])
    return "\n".join(lines)
