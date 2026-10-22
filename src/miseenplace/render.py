"""Plain-text and Markdown views: the page, brief, standup, and chat-message lines. No I/O."""

import datetime as dt
import time
from typing import cast

from miseenplace.chat import Message
from miseenplace.model import (
    CLOSED,
    Entry,
    Item,
    eta_key,
    find,
    fmt_spent,
    now,
    parse_eta,
    priority_key,
    select,
)

# ---- items ----


def line(i: Item) -> str:
    parts = [f"**#{i['id']}** {i['title']}", i["state"]]
    if i["priority"]:
        parts.append(i["priority"].upper())
    if i["topic"]:
        parts.append(f"`{i['topic']}`")
    if i["eta"]:
        parts.append(f"eta {i['eta'][5:]}")
    if i["who"]:
        parts.append(i["who"])
    parts += [f"[ref]({r})" for r in i["refs"]]
    if i["notes"]:
        parts.append(f"_{i['notes'][-1]}_")
    return "- " + " · ".join(parts)


def diary_entries(entries: list[Entry], start: str) -> list[Entry]:
    return [e for e in entries if e["op"] == "log" and e["ts"][:10] >= start]


def diary_line(e: Entry) -> str:
    c = e["changes"]
    parts = [f"{e['ts'][5:10]} {c['text']}"]
    if c["topic"]:
        parts.append(f"`{c['topic']}`")
    if c["spent"]:
        parts.append(fmt_spent(cast(int, c["spent"])))
    parts += [f"#{t}" for t in cast(list[str], c["tags"])]
    parts += [f"[ref]({r})" for r in cast(list[str], c["refs"])]
    return "- " + " · ".join(parts)


def render(items: list[Item], entries: list[Entry], today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    week_ago = (today - dt.timedelta(days=7)).isoformat()
    live = sorted((i for i in items if i["state"] not in CLOSED), key=eta_key)
    due = [i for i in live if i["eta"] and i["eta"] <= today.isoformat()]
    rest = [i for i in live if i not in due]
    closed = sorted(
        (i for i in items if i["state"] in CLOSED and i["updated"][:10] >= week_ago),
        key=lambda i: i["updated"],
        reverse=True,
    )
    sections = [
        ("⏰ Overdue / due today", due),
        ("Mine", [i for i in rest if i["kind"] == "mine"]),
        ("Waiting on others", [i for i in rest if i["kind"] == "waiting"]),
        ("Reminders", [i for i in rest if i["kind"] == "reminder"]),
        ("Done (last 7 days)", closed),
    ]
    out = [
        f"# Action Items\n\n_Rendered {now()} from data/items.json · "
        "history in data/audit_log.jsonl_\n"
    ]
    for title, rows in sections:
        out.append(f"## {title}\n" + ("\n".join(map(line, rows)) or "_none_") + "\n")
    diary = sorted(diary_entries(entries, week_ago), key=lambda e: e["ts"], reverse=True)
    out.append("## Diary (last 7 days)\n" + ("\n".join(map(diary_line, diary)) or "_none_") + "\n")
    return "\n".join(out)


def brief_item(i: Item) -> str:
    spent = fmt_spent(i["spent"]) if i["spent"] else None
    rows = [
        ("state", i["state"]),
        ("priority", i["priority"]),
        ("eta", i["eta"]),
        ("who", i["who"]),
        ("context", i["context"]),
        ("next", i["next"]),
        ("blocker", i["blocker"]),
        ("think", i["think"]),
        ("refs", ", ".join(i["refs"])),
        ("notes", "; ".join(i["notes"])),
        ("spent", spent),
        ("tags", ", ".join(i["tags"])),
    ]
    out = [f"#{i['id']} {i['title']}" + (f"  [{i['topic']}]" if i["topic"] else "")]
    out += [f"  {label}: {value}" for label, value in rows if value]
    if i["decisions"]:
        out += [
            "  decisions:",
            *(f"    - {d['what']} (why: {d['why']})" for d in i["decisions"]),
        ]
    return "\n".join(out)


def brief(items: list[Item], key: str) -> str:
    """One item by id, else every open item of a topic (by priority), else a title match."""
    by_id = [i for i in items if str(i["id"]) == key.lstrip("#")]
    topic = select(items, topic=key)
    found = by_id or sorted(topic, key=priority_key) or [find(items, key)]
    return "\n\n".join(map(brief_item, found))


# ---- standup ----


def item_facts(e: Entry) -> list[str]:
    facts: list[str] = []
    state = cast(list[str], e["changes"].get("state", [None, None]))[1]
    if state in ("done", "dropped", "doing"):
        facts.append(state)
    notes = cast(list[list[str]], e["changes"].get("notes", [[], []]))[1]
    if notes:
        facts.append(notes[-1])
    old, new = cast(list[int], e["changes"].get("spent", [0, 0]))
    if new > old:
        facts.append(f"+{fmt_spent(new - old)}")
    return facts


def did_by_item(entries: list[Entry], start: str) -> dict[int, list[str]]:
    """What changed per item since `start`: state moves, latest note, time added."""
    did: dict[int, list[str]] = {}
    for e in entries:
        if e["ts"][:10] < start or e["op"] in ("add", "log"):
            continue
        facts = item_facts(e)
        if facts:
            did.setdefault(e["id"], []).extend(facts)
    return did


def standup_row(i: Item, extra: str = "") -> str:
    spent = f" [{fmt_spent(i['spent'])} spent]" if i["spent"] else ""
    link = f" ({i['refs'][0]})" if i["refs"] else ""
    return f"- {i['title']}{extra}{spent}{link}"


def diary_row(e: Entry) -> str:
    c = e["changes"]
    topic = f" [{c['topic']}]" if c["topic"] else ""
    spent = f" — {fmt_spent(cast(int, c['spent']))}" if c["spent"] else ""
    refs = cast(list[str], c["refs"])
    return f"- {c['text']}{topic}{spent}" + (f" ({refs[0]})" if refs else "")


def standup(
    items: list[Item], entries: list[Entry], since: str, today: dt.date | None = None
) -> str:
    """Did (item changes and diary since `since`), next (open mine by eta), waiting on."""
    start = parse_eta(since, today) or "0000-00-00"
    by_id = {i["id"]: i for i in items}
    open_ = sorted((i for i in items if i["state"] not in CLOSED), key=eta_key)
    did = [
        standup_row(by_id[k], f" — {'; '.join(dict.fromkeys(v))}")
        for k, v in did_by_item(entries, start).items()
    ] + [diary_row(e) for e in diary_entries(entries, start)]
    out = [f"**Did** (since {start}):", *(did or ["- nothing logged"])]
    out += ["", "**Next:**"]
    out += [
        standup_row(i, f" — eta {i['eta'][5:]}" if i["eta"] else "")
        for i in open_
        if i["kind"] == "mine"
    ] or ["- none"]
    waiting = [
        standup_row(i, f" — {i['who']}" if i["who"] else "")
        for i in open_
        if i["kind"] == "waiting"
    ]
    if waiting:
        out += ["", "**Waiting on:**", *waiting]
    return "\n".join(out)


# ---- chat messages ----


def format_message(m: Message, width: int = 1200) -> str:
    """`[MM-DD HH:MM] (id N) Sender [@ME]: body`, local time, body cut at `width`."""
    t = time.strftime("%m-%d %H:%M", time.localtime(m.ts))
    tag = " [@ME]" if m.mentioned else ""
    body = m.text.strip()
    if len(body) > width:
        body = body[:width] + " …[trunc]"
    return f"[{t}] (id {m.id}) {m.sender}{tag}: {body}"
