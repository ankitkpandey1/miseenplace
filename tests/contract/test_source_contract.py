"""The source contract, run against every source we can reach.

`demo` always runs. `zulip` runs only when credentials are in the environment
(ZULIP_EMAIL, ZULIP_API_KEY, ZULIP_SITE, or MISEENPLACE_ZULIP_RC) and is READ-ONLY: it never
sends. Add your source to `harnesses()` with the same shape.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from miseenplace.chat import Target
from miseenplace.consent import ConsentError
from miseenplace.sources.base import Message, Person, Source
from miseenplace.sources.demo import DemoSource
from miseenplace.sources.zulip import ZulipSource
from miseenplace.tour import seed

Inject = Callable[[str], None]


@dataclass
class Harness:
    source: Source
    inject: Inject | None  # make one new message from someone else appear; None on live services
    thread_target: Target | None  # a thread known to exist, if any
    inject_mine: Inject | None = None  # make one message written by me appear; same limits


def demo_harness(root: Path) -> Harness:
    seed(root)
    src = DemoSource(root)

    def inject(text: str) -> None:
        src.append("DM: Rowan Hale", "Rowan Hale", text)

    def inject_mine(text: str) -> None:
        src.append("DM: Rowan Hale", "Jordan Rivers", text, mine=True)

    return Harness(src, inject, Target("platform", "release 2.4"), inject_mine)


def zulip_harness(root: Path) -> Harness:
    return Harness(ZulipSource.from_config(root, {}), None, None)


HAVE_ZULIP = bool(
    os.environ.get("MISEENPLACE_ZULIP_RC")
    or all(os.environ.get(k) for k in ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE"))
)
SOURCES = [
    pytest.param(demo_harness, id="demo"),
    pytest.param(
        zulip_harness,
        id="zulip",
        marks=pytest.mark.skipif(not HAVE_ZULIP, reason="no Zulip credentials in the environment"),
    ),
]


@pytest.fixture(params=SOURCES)
def h(request: pytest.FixtureRequest, tmp_path: Path) -> Harness:
    make: Callable[[Path], Harness] = request.param
    return make(tmp_path)


def need_inject(h: Harness) -> Inject:
    if h.inject is None:
        pytest.skip("live service: cannot inject messages")
    return h.inject


def test_it_satisfies_the_protocol(h: Harness) -> None:
    assert isinstance(h.source, Source)
    assert h.source.name


def test_unknown_cursor_sets_a_baseline_and_returns_nothing(h: Harness) -> None:
    assert h.source.new("contract-fresh") == []
    assert h.source.new("contract-fresh") == []  # still nothing: nothing arrived


def test_cursors_are_per_consumer(h: Harness) -> None:
    inject = need_inject(h)
    h.source.new("a")
    h.source.new("b")
    inject("hello from the contract")
    assert [m.text for m in h.source.new("a")] == ["hello from the contract"]
    assert h.source.new("a") == []
    assert [m.text for m in h.source.new("b")] == ["hello from the contract"]  # a did not eat it


def test_init_resets_the_baseline(h: Harness) -> None:
    inject = need_inject(h)
    h.source.new("c")
    inject("old news")
    assert h.source.new("c", init=True) == []
    assert h.source.new("c") == []


def test_new_never_returns_my_own_messages(h: Harness) -> None:
    inject, inject_mine = need_inject(h), h.inject_mine
    if inject_mine is None:
        pytest.skip("live service: cannot inject messages")
    h.source.new("own", init=True)
    inject_mine("written by me")
    inject("written by someone else")
    got = h.source.new("own")
    assert [m.text for m in got] == ["written by someone else"]  # non-empty, and mine is absent
    assert not any(m.mine for m in got)


def test_thread_returns_messages_oldest_first(h: Harness) -> None:
    if h.thread_target is None:
        pytest.skip("no known thread on this service")
    msgs: list[Message] = h.source.thread(h.thread_target, 10)
    assert msgs
    assert [m.ts for m in msgs] == sorted(m.ts for m in msgs)


def test_draft_stores_text_and_posts_nothing(h: Harness) -> None:
    target = h.thread_target or Target("nowhere", "nothing")
    try:
        d = h.source.draft(target, "contract draft, never sent")
    except Exception as e:  # a live service may refuse an unknown destination
        if h.thread_target is not None:
            raise
        pytest.skip(f"destination rejected at draft time: {type(e).__name__}")
    assert d.text.endswith("contract draft, never sent")
    assert d.id


@pytest.mark.parametrize("not_an_id", ["free text message", "", "dffffffff"])
def test_send_refuses_free_text_and_unknown_ids(h: Harness, not_an_id: str) -> None:
    with pytest.raises(ConsentError):
        h.source.send(not_an_id)


def test_users_returns_people(h: Harness) -> None:
    people = h.source.users("a")
    assert all(isinstance(p, Person) for p in people)
