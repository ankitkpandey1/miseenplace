"""Pure functions: eta parsing, filters, ordering."""

import datetime as dt

import pytest

from miseenplace.model import Item, MiseError, fmt_spent, parse_eta, priority_key, select, top
from miseenplace.store import Store

WED = dt.date(2026, 10, 21)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("today", "2026-10-21"),
        ("tomorrow", "2026-10-22"),
        ("+3d", "2026-10-24"),
        ("fri", "2026-10-23"),
        ("wed", "2026-10-28"),
        ("yesterday", "2026-10-20"),
        ("-7d", "2026-10-14"),
        ("11-02", "2026-11-02"),
        ("none", None),
    ],
)
def test_eta_parsing(text: str, want: str | None) -> None:
    assert parse_eta(text, WED) == want


def test_bad_eta_has_a_fix_hint() -> None:
    with pytest.raises(MiseError, match="bad eta"):
        parse_eta("someday", WED)


def test_fmt_spent() -> None:
    assert (fmt_spent(180), fmt_spent(95), fmt_spent(5)) == ("3h", "1h35m", "5m")


def seed(store: Store) -> None:
    store.add("late-null", eta="2026-10-01")
    store.add(
        "p2-soon", eta="2026-10-05", priority="p2", topic="Alpha", tags=["web"], who="Sam Lee"
    )
    store.add("p0-later", eta="2026-10-30", priority="p0", topic="Beta")
    store.add("p0-sooner", eta="2026-10-10", priority="p0", topic="alpha", tags=["web", "api"])
    store.add("p1-noeta", priority="p1", kind="waiting", who="Kim")
    store.add("done-p0", priority="p0")
    store.update("done-p0", "done")


def titles(rows: list[Item]) -> list[str]:
    return [r["title"] for r in rows]


def test_list_filters(store: Store) -> None:
    seed(store)
    items = store.load()[1]
    assert titles(select(items, topic="alpha")) == ["p2-soon", "p0-sooner"]
    assert titles(select(items, tag="api")) == ["p0-sooner"]
    assert titles(select(items, priority="p0")) == ["p0-later", "p0-sooner"]
    assert titles(select(items, kind="waiting")) == ["p1-noeta"]
    assert titles(select(items, who="sam")) == ["p2-soon"]
    assert titles(select(items, topic="alpha", tag="web", priority="p0")) == ["p0-sooner"]
    assert "done-p0" in titles(select(items, priority="p0", show_all=True))
    with pytest.raises(MiseError, match="bad priority"):
        select(items, priority="urgent")


def test_sort_by_priority_then_eta(store: Store) -> None:
    seed(store)
    items = sorted(select(store.load()[1]), key=priority_key)
    assert titles(items) == ["p0-sooner", "p0-later", "p1-noeta", "p2-soon", "late-null"]


def test_top_is_open_mine_by_priority_then_eta_null_last(store: Store) -> None:
    seed(store)
    items = store.load()[1]
    assert titles(top(items)) == ["p0-sooner", "p0-later", "p2-soon", "late-null"]
    assert titles(top(items, 2)) == ["p0-sooner", "p0-later"]
