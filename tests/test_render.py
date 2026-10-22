"""The page, brief and standup, built from a real store."""

import datetime as dt
import json

import pytest

from miseenplace.model import MiseError, now
from miseenplace.render import brief, render, standup
from miseenplace.store import Store
from tests.test_model import seed


def today(store: Store) -> dt.date:
    return dt.date.fromisoformat(now()[:10])  # same clock as the store


def test_brief_shows_every_field_of_one_item(store: Store) -> None:
    store.add(
        "ship parser", eta="2026-10-22", who="Sam", refs=["https://example.com/1"], note="n1",
        topic="Parser", context="asked by Sam", next="write tests", blocker="review",
        decisions=["use PEG::fast"], think="parser-rewrite", priority="p1", spent="1h30m",
        tags=["code", "q4"],
    )  # fmt: skip
    text = brief(store.load()[1], "1")
    for want in (
        "#1 ship parser", "[Parser]", "state: todo", "priority: p1", "eta: 2026-10-22",
        "who: Sam", "context: asked by Sam", "next: write tests", "blocker: review",
        "use PEG (why: fast)", "think: parser-rewrite", "refs: https://example.com/1",
        "notes: n1", "spent: 1h30m", "tags: code, q4",
    ):  # fmt: skip
        assert want in text


def test_brief_topic_lists_open_items_by_priority(store: Store) -> None:
    seed(store)
    text = brief(store.load()[1], "alpha")
    assert text.index("p0-sooner") < text.index("p2-soon")
    assert "p0-later" not in text


def test_brief_unknown_key_fails_loudly(store: Store) -> None:
    with pytest.raises(MiseError, match="0 items match"):
        brief(store.load()[1], "nope")


def test_render_puts_overdue_first(store: Store) -> None:
    store.add("late", eta="2026-10-20")
    store.add("later", eta="2026-10-30")
    page = render(store.load()[1], store.read_log(), dt.date(2026, 10, 21))
    assert page.index("late") < page.index("## Mine") < page.index("later")


def test_render_line_shows_priority_and_topic(store: Store) -> None:
    store.add("x", priority="p0", topic="Parser", eta="2026-10-30")
    page = render(store.load()[1], store.read_log(), dt.date(2026, 10, 21))
    assert "**#1** x · todo · P0 · `Parser` · eta 10-30" in page


def test_diary_appears_in_page_for_last_seven_days_only(store: Store) -> None:
    store.log("fresh entry", topic="T", spent="1h30m", tags=["a"])
    old: dict[str, object] = {
        "text": "ancient entry",
        "topic": None,
        "tags": [],
        "spent": 0,
        "refs": [],
    }
    entry = {
        "seq": 2,
        "ts": "2020-01-01 10:00",
        "op": "log",
        "id": 0,
        "by": "agent",
        "changes": old,
    }
    with (store.root / "data" / "audit_log.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")
    page = render(store.load()[1], store.read_log())
    assert "fresh entry · `T` · 1h30m · #a" in page.split("## Diary (last 7 days)")[1]
    assert "ancient entry" not in page


def test_standup_lists_done_next_and_waiting(store: Store) -> None:
    store.add("review 7042", eta="2026-10-22", refs=["https://x/7042"])
    store.add("ship journal")
    store.add("nudge V on 7046", kind="waiting", who="Kim")
    store.update("ship journal", "done", note="pushed")
    text = standup(store.load()[1], store.read_log(), "today", today(store))
    did, nxt = text.split("**Next:**")
    assert "ship journal — done; pushed" in did
    assert "review 7042" not in did
    assert "review 7042 — eta 10-22 (https://x/7042)" in nxt
    assert "nudge V on 7046 — Kim" in text.split("**Waiting on:**")[1]


def test_diary_appears_in_standup_did_with_item_time(store: Store) -> None:
    store.add("ship it", spent="45m")
    store.update("ship it", "set", state="doing", spent="15m")
    store.log("paired on parser", topic="Parser", spent="30m", refs=["https://example.com/3"])
    text = standup(store.load()[1], store.read_log(), "today", today(store))
    did, nxt = text.split("**Next:**")
    assert "- paired on parser [Parser] — 30m (https://example.com/3)" in did
    assert "ship it — doing; +15m [1h spent]" in did
    assert "ship it [1h spent]" in nxt
