"""Questions the assistant queues instead of interrupting, and what answering them teaches.

asks.jsonl   one ask per line: decision, recommend, default, by (local "YYYY-MM-DD HH:MM" or ""),
             confidence, flag ("" | blocks | destructive), kind (a draft type, or ""),
             status, answered
asks.where   the one-line note shown last in the digest: where the user was

`render` is the digest: blocking or destructive asks first, then the earliest deadline, then
the oldest. It stops at MAX_ITEMS asks or MAX_CHARS characters, whichever comes first (about
two minutes of reading), and says how many it left out. Answering an ask as accepted, edited
or rejected is what lets `acceptance` say how often a kind of draft went out unchanged.
"""

import json
import re
import time
from dataclasses import asdict, dataclass, replace

from miseenplace.jsonl import parsed, rows
from miseenplace.model import Entry, MiseError
from miseenplace.storage.base import Storage

NAME = "asks.jsonl"
WHERE = "asks.where"
CONFIDENCE = ("verified", "read", "guessed")
OUTCOMES = ("accepted", "edited", "rejected")
BY = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
MAX_ITEMS = 7
MAX_FIELD = 200
MIN_DECISION = 20
MAX_CHARS = 1800  # 2 minutes at about 900 characters a minute
CHARS_PER_MINUTE = 900
ROUTINE_TAG = "routine:"
TOP_ROUTINES = 3
MIN_DRAFTS = 5
NA = "n/a"


@dataclass(frozen=True)
class Ask:
    id: int
    decision: str
    recommend: str
    default: str
    by: str
    confidence: str
    flag: str
    kind: str
    created: str
    status: str = "open"
    answered: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, line: str) -> "Ask":
        return cls(**json.loads(line))

    def deadline(self, now: str) -> str:
        if not self.by:
            return ""
        shown = self.by[11:] if self.by[:10] == now[:10] else self.by[5:]
        return f" if no answer by {shown}{' (overdue)' if self.by < now else ''}"

    def line(self, width: int, now: str) -> str:
        """One line of at most `width` characters; the decision gives way first."""
        tag = {"blocks": "BLOCKS ", "destructive": "DESTRUCTIVE "}.get(self.flag, "")
        until = self.deadline(now)
        tail = (
            f"  Recommended: {self.recommend}.  Default{until}: {self.default}."
            f"  [{self.confidence}]"
        )
        head = f"A{self.id}. {tag}"
        room = width - len(head) - len(tail)
        if room >= MIN_DECISION:
            text = self.decision if len(self.decision) <= room else self.decision[: room - 1] + "…"
            return head + text + tail
        whole = head + self.decision + tail
        return whole if len(whole) <= width else whole[: width - 1] + "…"


def one_line(label: str, text: str) -> str:
    clean = " ".join(text.split())
    if not clean:
        raise MiseError(f"an ask needs a non-empty {label}")
    if len(clean) > MAX_FIELD:
        raise MiseError(f"the {label} is {len(clean)} characters; keep it under {MAX_FIELD}")
    return clean


def read_asks(storage: Storage) -> list[Ask]:
    text = storage.read_file(NAME) or ""
    return [parsed(NAME, n, Ask.from_json, line) for n, line in rows(text)]


def write_asks(storage: Storage, asks: list[Ask]) -> None:
    storage.write_file(NAME, "".join(a.to_json() + "\n" for a in asks))


def add_ask(
    storage: Storage,
    decision: str,
    recommend: str,
    default: str,
    confidence: str,
    by: str = "",
    flag: str = "",
    kind: str = "",
) -> Ask:
    if confidence not in CONFIDENCE:
        raise MiseError(f"confidence is one of {', '.join(CONFIDENCE)}")
    if by and not BY.fullmatch(by):
        raise MiseError(f"--by is a time of day as HH:MM, got {by!r}")
    asks = read_asks(storage)
    now = time.strftime("%Y-%m-%d %H:%M")
    new = Ask(
        max((a.id for a in asks), default=0) + 1,
        one_line("decision", decision),
        one_line("recommendation", recommend),
        one_line("default", default),
        f"{now[:10]} {by}" if by else "",
        confidence,
        flag,
        " ".join(kind.split()),
        now,
    )
    write_asks(storage, [*asks, new])
    return new


def answer(storage: Storage, ask_id: int, outcome: str) -> str:
    if outcome not in OUTCOMES:
        raise MiseError(f"answer is one of {', '.join(OUTCOMES)}")
    asks = read_asks(storage)
    found = next((a for a in asks if a.id == ask_id), None)
    if found is None:
        raise MiseError(f"no ask A{ask_id}")
    if found.status != "open":
        raise MiseError(f"A{ask_id} is already {found.status}")
    done = replace(found, status=outcome, answered=time.strftime("%Y-%m-%d"))
    write_asks(storage, [done if a.id == ask_id else a for a in asks])
    return f"A{ask_id} {outcome}"


def set_where(storage: Storage, text: str) -> None:
    storage.write_file(WHERE, one_line("note", text) + "\n")


def rank(a: Ask) -> tuple[bool, str, int]:
    return (a.flag == "", a.by or "9999", a.id)


def render(storage: Storage, width: int) -> str:
    open_ = sorted((a for a in read_asks(storage) if a.status == "open"), key=rank)
    now = time.strftime("%Y-%m-%d %H:%M")
    shown: list[str] = []
    for a in open_[:MAX_ITEMS]:
        line = a.line(width, now)
        if shown and sum(map(len, shown)) + len(line) > MAX_CHARS:
            break
        shown.append(line)
    minutes = max(1, -(-sum(map(len, shown)) // CHARS_PER_MINUTE))
    noun = "decision" if len(open_) == 1 else "decisions"
    out = [f"{len(open_)} {noun} (~{minutes} min)" if open_ else "0 decisions", *shown]
    if len(open_) > len(shown):
        out.append(f"+{len(open_) - len(shown)} more")
    where = (storage.read_file(WHERE) or "").strip()
    if where:
        out.append(f"Where you were: {where}"[:width])
    return "\n".join(out)


def since(days: int) -> str:
    """The first calendar day of a window of `days` days that ends today."""
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))


def acceptance(asks: list[Ask], start: str) -> dict[str, tuple[int, int]]:
    """Per draft kind: (accepted unchanged, answered), for asks answered on or after `start`."""
    out: dict[str, tuple[int, int]] = {}
    for a in asks:
        if a.kind and a.status != "open" and a.answered >= start:
            same, total = out.get(a.kind, (0, 0))
            out[a.kind] = (same + (a.status == "accepted"), total + 1)
    return out


def routine_minutes(entries: list[Entry], start: str) -> dict[str, int]:
    """Minutes per routine type from diary entries tagged `routine:<type>` since `start`."""
    out: dict[str, int] = {}
    for e in entries:
        if e["op"] != "log" or e["ts"][:10] < start:
            continue
        spent, tags = e["changes"].get("spent"), e["changes"].get("tags")
        if isinstance(spent, int) and isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and tag.startswith(ROUTINE_TAG):
                    kind = tag[len(ROUTINE_TAG) :]
                    out[kind] = out.get(kind, 0) + spent
    return out


def report(storage: Storage, entries: list[Entry], days: int) -> str:
    start = since(days)
    minutes = sorted(routine_minutes(entries, start).items(), key=lambda kv: (-kv[1], kv[0]))
    rates = acceptance(read_asks(storage), start)
    top = [f"  {kind}  {m}m" for kind, m in minutes[:TOP_ROUTINES] if m > 0]
    drafts = [
        f"  {k}  {s}/{n} unchanged  {s / n:.0%}{' (n<5)' if n < MIN_DRAFTS else ''}"
        for k, (s, n) in sorted(rates.items())
    ]
    return "\n".join(
        [
            f"routine types by minutes, last {days}d",
            *(top or [f"  {NA} (log with --tag {ROUTINE_TAG}<type> --spent)"]),
            f"draft acceptance, last {days}d",
            *(drafts or [f"  {NA} (no answered asks with a --kind)"]),
        ]
    )
