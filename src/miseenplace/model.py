"""Item and log-entry model: constants, types, parsing, validation and queries. No I/O."""

import datetime as dt
import os
import re
import shlex
import uuid
from typing import NotRequired, TypedDict, cast

STATES = ("todo", "doing", "waiting", "done", "dropped")
KINDS = ("mine", "waiting", "reminder")
OPS = ("add", "set", "done", "drop", "log")
PRIORITIES = ("p0", "p1", "p2", "p3")
DEFAULT_ACTORS = ("me", "agent")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
CLOSED = ("done", "dropped")
TEXT_FIELDS = ("topic", "context", "next", "blocker", "think")
OPT_STR = (str, type(None))
ITEM_FIELDS: dict[str, type | tuple[type, ...]] = {
    "id": int,
    "title": str,
    "kind": str,
    "state": str,
    "eta": OPT_STR,
    "who": OPT_STR,
    "refs": list,
    "notes": list,
    "created": str,
    "updated": str,
    **dict.fromkeys(TEXT_FIELDS, OPT_STR),
    "decisions": list,
    "priority": OPT_STR,
    "spent": int,
    "tags": list,
    "sessions": list,
}
PRE_SESSION_FIELDS = set(ITEM_FIELDS) - {"sessions"}
LEGACY_FIELDS = PRE_SESSION_FIELDS - set(TEXT_FIELDS) - {"decisions", "priority", "spent", "tags"}
DIARY_FIELDS = {"text", "topic", "tags", "spent", "refs"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
SPENT_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?$")


class Decision(TypedDict):
    what: str
    why: str


class Item(TypedDict):
    id: int
    title: str
    kind: str
    state: str
    eta: str | None
    who: str | None
    refs: list[str]
    notes: list[str]
    created: str
    updated: str
    topic: str | None
    context: str | None
    next: str | None
    blocker: str | None
    decisions: list[Decision]
    think: str | None
    priority: str | None
    spent: int
    tags: list[str]
    sessions: list[str]


class Entry(TypedDict):
    seq: int
    ts: str
    op: str
    id: int
    by: str
    changes: dict[str, object]


class Extras(TypedDict):
    """Work-context options shared by `add` and `set`; absent means unchanged."""

    topic: NotRequired[str]
    context: NotRequired[str]
    next: NotRequired[str]
    blocker: NotRequired[str]
    think: NotRequired[str]
    priority: NotRequired[str]
    spent: NotRequired[str]
    decisions: NotRequired[list[str]]
    tags: NotRequired[list[str]]
    untags: NotRequired[list[str]]
    sessions: NotRequired[list[str]]


class MiseError(Exception):
    """A user-facing failure: the message says what to do about it."""


def raw(x: Item | Entry) -> dict[str, object]:
    """Dynamic-key view of a typed record."""
    return cast(dict[str, object], x)


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# ---- parsing -------------------------------------------------------------------


def parse_eta(s: str | None, today: dt.date | None = None) -> str | None:
    if s is None:
        return None
    s = s.strip().lower()
    if s in ("none", "-", ""):
        return None
    try:
        return eta_date(s, today or dt.date.today()).isoformat()
    except ValueError:
        raise MiseError(
            f"bad eta {s!r}; use YYYY-MM-DD, MM-DD, today, tomorrow, +Nd, mon..sun or none"
        ) from None


def eta_date(s: str, today: dt.date) -> dt.date:
    if s == "today":
        return today
    if s == "tomorrow":
        return today + dt.timedelta(days=1)
    if s == "yesterday":
        return today - dt.timedelta(days=1)
    if re.fullmatch(r"[+-]\d+d", s):
        return today + dt.timedelta(days=int(s[:-1]))
    if s[:3] in DAYS:
        return today + dt.timedelta(days=(DAYS.index(s[:3]) - today.weekday() - 1) % 7 + 1)
    if re.fullmatch(r"\d{2}-\d{2}", s):
        return dt.date.fromisoformat(f"{today.year}-{s}")
    return dt.date.fromisoformat(s)


def parse_spent(s: str) -> int:
    """30m | 1h | 1h30m -> minutes (> 0)."""
    m = SPENT_RE.match(s.strip().lower())
    minutes = int(m[1] or 0) * 60 + int(m[2] or 0) if m else 0
    if minutes <= 0:
        raise MiseError(f"bad time spent {s!r}; use 30m, 1h or 1h30m")
    return minutes


def fmt_spent(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return (f"{h}h" if h else "") + (f"{m}m" if m or not h else "")


def parse_decision(s: str) -> Decision:
    what, sep, why = s.partition("::")
    if not sep or not what.strip() or not why.strip():
        raise MiseError(f"decision must be 'what::why' (both non-empty), got {s!r}")
    return {"what": what.strip(), "why": why.strip()}


def parse_session(s: str) -> str:
    """`UUID[@CWD]` -> `UUID@CWD` (lowercase uuid, absolute cwd, default the current dir)."""
    sid, _, cwd = s.strip().partition("@")
    try:
        sid = str(uuid.UUID(sid))
    except ValueError:
        raise MiseError(
            f"bad session {s!r}; use UUID or UUID@CWD (a Claude Code session id)"
        ) from None
    path = os.path.abspath(os.path.expanduser(cwd)) if cwd else os.getcwd()
    if not os.path.isdir(path):
        raise MiseError(f"bad session {s!r}: {path} is not a directory")
    return f"{sid}@{path}"


def merge_sessions(old: list[str], given: list[str]) -> list[str]:
    """Add sessions (a known uuid moves to the end with its new cwd); '' clears them all."""
    out = list(old)
    for g in given:
        if not g.strip():
            out = []
            continue
        new = parse_session(g)
        out = [x for x in out if x.partition("@")[0] != new.partition("@")[0]] + [new]
    return out


def resume_commands(item: Item) -> list[str]:
    """`cd CWD && claude --resume UUID`, newest session first."""
    return [
        f"cd {shlex.quote(cwd)} && claude --resume {sid}"
        for sid, _, cwd in (x.partition("@") for x in reversed(item["sessions"]))
    ]


def with_defaults(item: dict[str, object]) -> dict[str, object]:
    """Fill the work fields an item written before they existed lacks."""
    defaults: dict[str, object] = dict.fromkeys(TEXT_FIELDS)
    defaults |= {"decisions": [], "priority": None, "spent": 0, "tags": [], "sessions": []}
    return defaults | item


def added_item(changes: dict[str, object]) -> dict[str, object]:
    """The item an `add` entry creates; legacy adds (10 fields) get defaults."""
    return (
        with_defaults(changes) if set(changes) in (LEGACY_FIELDS, PRE_SESSION_FIELDS) else changes
    )


# ---- validation (mirrors schema/*.schema.json; stdlib only) ---------------------


def check_urls(urls: object, where: str) -> None:
    if not isinstance(urls, list) or not all(
        isinstance(r, str) and r.startswith("http") for r in urls
    ):
        raise MiseError(f"{where}: refs must be a list of http(s) urls")


def check_tags(tags: object, where: str) -> None:
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        raise MiseError(f"{where}: tags must be a list of non-empty strings")
    if len(set(tags)) != len(tags):
        raise MiseError(f"{where}: duplicate tags in {tags}")


def check_sessions(sessions: object, where: str) -> None:
    ok = isinstance(sessions, list) and all(
        isinstance(x, str) and SESSION_RE.match(x) and os.path.isabs(x[37:]) for x in sessions
    )
    if not ok:
        raise MiseError(f"{where}: sessions must be a list of 'uuid@/absolute/cwd' strings")


def check_minutes(minutes: object, where: str) -> None:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
        raise MiseError(f"{where}: spent must be whole minutes >= 0, got {minutes!r}")


def validate_item(i: dict[str, object]) -> None:
    if set(i) != set(ITEM_FIELDS):
        raise MiseError(f"item fields {sorted(i)} != {sorted(ITEM_FIELDS)}")
    where = f"item {i.get('id')}"
    for k, t in ITEM_FIELDS.items():
        if not isinstance(i[k], t):
            raise MiseError(f"{where}: {k} has wrong type {type(i[k]).__name__}")
    item = cast(Item, i)
    if item["id"] < 1 or not item["title"].strip():
        raise MiseError(f"{where}: id must be >= 1 and title non-empty")
    if item["kind"] not in KINDS or item["state"] not in STATES:
        raise MiseError(f"{where}: bad kind/state {item['kind']}/{item['state']}")
    if item["eta"] is not None and not DATE_RE.match(item["eta"]):
        raise MiseError(f"{where}: bad eta {item['eta']!r}, want YYYY-MM-DD")
    if not (TS_RE.match(item["created"]) and TS_RE.match(item["updated"])):
        raise MiseError(f"{where}: bad created/updated, want YYYY-MM-DD HH:MM")
    check_urls(item["refs"], where)
    if not all(isinstance(n, str) for n in item["notes"]):
        raise MiseError(f"{where}: notes must be strings")
    validate_work_fields(item)


def validate_work_fields(item: Item) -> None:
    where = f"item {item['id']}"
    if any(not str(v).strip() for k, v in raw(item).items() if k in TEXT_FIELDS and v is not None):
        raise MiseError(f"{where}: empty text field; use null (pass '' to clear)")
    if item["priority"] is not None and item["priority"] not in PRIORITIES:
        raise MiseError(f"{where}: bad priority {item['priority']!r}; use {'|'.join(PRIORITIES)}")
    check_minutes(item["spent"], where)
    check_tags(item["tags"], where)
    check_sessions(item["sessions"], where)
    for d in item["decisions"]:
        ok = isinstance(d, dict) and set(d) == {"what", "why"}
        if not ok or not all(isinstance(v, str) and v.strip() for v in d.values()):
            raise MiseError(f"{where}: each decision must be {{what, why}} non-empty strings")


def validate_diary(e: dict[str, object]) -> None:
    where = f"log entry {e['seq']}"
    c = e["changes"]
    if e["id"] != 0 or not isinstance(c, dict) or set(c) != DIARY_FIELDS:
        raise MiseError(f"{where}: a diary entry needs id 0 and changes {sorted(DIARY_FIELDS)}")
    if not isinstance(c["text"], str) or not c["text"].strip():
        raise MiseError(f"{where}: diary text must be non-empty")
    if c["topic"] is not None and not (isinstance(c["topic"], str) and c["topic"].strip()):
        raise MiseError(f"{where}: topic must be null or non-empty text")
    check_minutes(c["spent"], where)
    check_urls(c["refs"], where)
    check_tags(c["tags"], where)


def validate_entry(e: dict[str, object], actors: tuple[str, ...], replay: bool = False) -> None:
    """Strict for new writes; `replay` also accepts legacy adds (no work fields)."""
    if set(e) != {"seq", "ts", "op", "id", "by", "changes"}:
        raise MiseError(f"log entry fields {sorted(e)}")
    where = f"log entry {e['seq']}"
    if e["by"] not in actors:
        raise MiseError(f"{where}: unknown actor {e['by']!r}; allowed {list(actors)} (config.toml)")
    if e["op"] not in OPS or not (isinstance(e["ts"], str) and TS_RE.match(e["ts"])):
        raise MiseError(f"{where}: bad op/ts, ops are {list(OPS)}")
    changes = e["changes"]
    if not isinstance(changes, dict):
        raise MiseError(f"{where}: changes must be an object")
    if e["op"] == "log":
        validate_diary(e)
    elif not isinstance(e["id"], int) or e["id"] < 1:
        raise MiseError(f"{where}: bad item id {e['id']!r}")
    elif e["op"] == "add":
        validate_item(added_item(changes) if replay else changes)
    elif not all(isinstance(v, list) and len(v) == 2 for v in changes.values()):
        raise MiseError(f"{where}: changes must be {{field: [old, new]}}")


# ---- queries --------------------------------------------------------------------


def eta_key(i: Item) -> tuple[str, int]:
    return (i["eta"] or "9999", i["id"])


def priority_key(i: Item) -> tuple[int, str, int]:
    """p0 first, no priority last, then soonest eta (overdue first)."""
    rank = PRIORITIES.index(i["priority"]) if i["priority"] else len(PRIORITIES)
    return (rank, *eta_key(i))


def find(items: list[Item], key: str) -> Item:
    key = key.lstrip("#")
    hits = [i for i in items if str(i["id"]) == key]
    if not hits:
        hits = [i for i in items if i["state"] not in CLOSED and key.lower() in i["title"].lower()]
    if len(hits) != 1:
        raise MiseError(f"{len(hits)} items match {key!r}; pass the id (see `list --all`)")
    return hits[0]


SESSION_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}@")
REPO_REF_RE = re.compile(r"^(?:([\w.-]+)/)?([\w.-]+)#(\d+)$")
PR_URL_RE = re.compile(r"^https?://[^/]+/([^/]+)/([^/]+)/(?:pull|issues)/(\d+)(?:[/?#]|$)", re.I)


def find_by_ref(items: list[Item], key: str) -> Item | None:
    """The item whose ref is the url `key` (any PR/issue page url), or the PR/issue `repo#N` /
    `owner/repo#N`; case-insensitive."""
    if m := REPO_REF_RE.match(key):
        owner, repo, num = (m[1] or "").lower(), m[2].lower(), m[3]
    elif m := PR_URL_RE.match(key):
        owner, repo, num = m[1].lower(), m[2].lower(), m[3]
    elif key.startswith("http"):
        hits = [
            i
            for i in items
            if key.rstrip("/").lower() in (r.rstrip("/").lower() for r in i["refs"])
        ]
        return unique_ref_hit(hits, key)
    else:
        return None
    hits = [i for i in items if any(ref_is(r, owner, repo, num) for r in i["refs"])]
    return unique_ref_hit(hits, key)


def ref_is(ref: str, owner: str, repo: str, num: str) -> bool:
    m = PR_URL_RE.match(ref)
    if not m:
        return False
    return owner in ("", m[1].lower()) and (m[2].lower(), m[3]) == (repo, num)


def unique_ref_hit(hits: list[Item], key: str) -> Item:
    if len(hits) != 1:
        raise MiseError(f"{len(hits)} items have a ref matching {key!r}; pass the id")
    return hits[0]


def select(
    items: list[Item],
    show_all: bool = False,
    topic: str | None = None,
    tag: str | None = None,
    priority: str | None = None,
    kind: str | None = None,
    who: str | None = None,
) -> list[Item]:
    """Items matching every given filter; topic and who match case-insensitively."""
    if priority is not None and priority not in PRIORITIES:
        raise MiseError(f"bad priority {priority!r}; use {'|'.join(PRIORITIES)}")
    return [
        i
        for i in items
        if (show_all or i["state"] not in CLOSED)
        and (topic is None or (i["topic"] or "").lower() == topic.lower())
        and (tag is None or tag in i["tags"])
        and (priority is None or i["priority"] == priority)
        and (kind is None or i["kind"] == kind)
        and (who is None or who.lower() in (i["who"] or "").lower())
    ]


def top(items: list[Item], n: int = 5) -> list[Item]:
    """Open items of kind mine by priority, then eta."""
    return sorted(select(items, kind="mine"), key=priority_key)[:n]
