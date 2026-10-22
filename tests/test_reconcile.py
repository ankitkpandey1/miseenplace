"""`reconcile`: proposals from chat refs and PR state; --apply writes through the store."""

import time
from pathlib import Path

import pytest

from miseenplace.chat import Message
from miseenplace.cli import open_lookup
from miseenplace.model import Item, MiseError
from miseenplace.reconcile import (
    NO_GH,
    Change,
    chat_change,
    chat_refs,
    pick,
    pr_change,
    pr_urls,
    propose,
)
from miseenplace.sources.base import SourceError
from miseenplace.sources.demo import DemoSource
from miseenplace.sources.zulip import Credentials, ZulipSource
from miseenplace.store import Store
from miseenplace.tour import seed
from tests.conftest import Result, run_cli
from tests.http_server import Seen, serve

NOW = time.time()
PR = "https://github.com/acme/app/pull/42"


def msg(id_: str, mine: bool = False) -> Message:
    return Message(id_, "#eng > release", "Me" if mine else "Alex", NOW + int(id_), "x", mine=mine)


def item(store: Store, state: str = "todo", *refs: str, context: str | None = None) -> Item:
    kind = "waiting" if state == "waiting" else "mine"
    extra = {"context": context} if context else {}
    return store.add("an item", kind, refs=list(refs), **extra)  # type: ignore[arg-type]


# ---- pure rules ----


def test_message_ids_from_every_link_shape_and_the_triage_mark(store: Store) -> None:
    it = item(
        store, "todo",
        "https://z.example.com/#narrow/id/11",
        "https://z.example.com/#narrow/channel/4-eng/topic/rel/near/12",
        "https://z.example.com/#narrow/channel/4-eng/topic/rel/with/13",
        "https://z.example.com/#narrow/id/11",
        "https://example.com/page/99",
        context="Alex in #eng: hi [inbox:zulip:14]",
    )  # fmt: skip
    assert chat_refs(it) == [(None, "11"), (None, "12"), (None, "13"), ("zulip", "14")]


def test_a_non_numeric_mark_is_not_a_ref_and_a_bad_lookup_id_is_a_source_error(
    store: Store, tmp_path: Path
) -> None:
    assert chat_refs(item(store, "todo", context="x [inbox:zulip:abc]")) == []
    src = ZulipSource(tmp_path, Credentials("me@example.com", "http://127.0.0.1:1", "k"))
    with pytest.raises(SourceError, match="bad zulip message id"):
        src.message("abc")


def test_pr_urls_find_only_pull_requests_once(store: Store) -> None:
    it = item(store, "todo", PR, PR, "https://github.com/acme/app/issues/42", "https://example.com")
    assert pr_urls(it) == [PR]


def test_a_merged_pr_outranks_a_chat_finding_and_a_reply_outranks_waiting(store: Store) -> None:
    it = item(store)
    waiting = Change(it["id"], "todo", "waiting", "w")
    reply = Change(it["id"], "todo", "todo", "r")
    done = Change(it["id"], "todo", "done", "d")
    assert pick([waiting, done]) == done and pick([waiting, reply, done]) == done
    assert pick([waiting, reply]) == reply and pick([]) is None


def test_a_signal_prints_as_still_and_is_not_a_change() -> None:
    assert Change(14, "waiting", "waiting", "you posted 9; no reply since").line() == (
        "#14 still waiting: you posted 9; no reply since"
    )
    assert not Change(14, "waiting", "waiting", "x").changes_state
    assert Change(3, "todo", "done", "x").changes_state


def test_i_posted_after_their_ask_and_nobody_answered_means_waiting(store: Store) -> None:
    c = chat_change(item(store), msg("1"), [msg("1"), msg("2", mine=True)])
    assert c and (c.current, c.proposed) == ("todo", "waiting")
    assert "2" in c.reason and "no reply" in c.reason


def test_their_reply_after_my_last_message_reopens_a_waiting_item(store: Store) -> None:
    thread = [msg("1"), msg("2", mine=True), msg("3")]
    c = chat_change(item(store, "waiting"), msg("1"), thread)
    assert c and (c.current, c.proposed) == ("waiting", "todo")
    assert c.reason == "new reply 3 after 2"


def test_a_reply_to_a_todo_item_keeps_it_todo(store: Store) -> None:
    c = chat_change(item(store), msg("1"), [msg("1"), msg("2", mine=True), msg("3")])
    assert c and (c.current, c.proposed) == ("todo", "todo")


def test_nothing_proposed_while_i_have_not_posted(store: Store) -> None:
    assert chat_change(item(store), msg("1"), [msg("1"), msg("2")]) is None


def test_a_referenced_message_of_mine_with_no_answer_is_waiting(store: Store) -> None:
    c = chat_change(item(store), msg("5", mine=True), [msg("4"), msg("5", mine=True)])
    assert c and c.proposed == "waiting"


def test_my_message_before_the_referenced_one_does_not_count(store: Store) -> None:
    assert chat_change(item(store), msg("5"), [msg("2", mine=True), msg("5")]) is None


@pytest.mark.parametrize("state", ["MERGED", "CLOSED", "merged"])
def test_a_merged_or_closed_pr_proposes_done(store: Store, state: str) -> None:
    c = pr_change(item(store), PR, f'{{"state":"{state}","reviewDecision":""}}')
    assert c and c.proposed == "done" and PR in c.reason


def test_an_open_pr_proposes_nothing_and_bad_output_is_loud(store: Store) -> None:
    assert pr_change(item(store), PR, '{"state":"OPEN","reviewDecision":"APPROVED"}') is None
    with pytest.raises(MiseError, match="unreadable"):
        pr_change(item(store), PR, "not json")


def test_without_gh_pr_items_are_skipped_with_one_note(store: Store) -> None:
    items = [item(store, "todo", PR), item(store, "todo", PR.replace("42", "43"))]
    changes, notes = propose(items, lambda: open_lookup(None, store), None)
    assert changes == [] and notes == [NO_GH]


# ---- the CLI over the real demo source ----


@pytest.fixture
def root(tmp_path: Path) -> Path:
    seed(tmp_path)
    return tmp_path


def mep(root: Path, *args: str, env: dict[str, str] | None = None) -> Result:
    return run_cli(root, "--source", "demo", *args, env=env)


def add(root: Path, title: str, ref: str, *extra: str) -> None:
    r = mep(root, "add", title, "--ref", ref, "--no-sync", *extra)
    assert r.code == 0, r.err


def state_of(root: Path, item_id: int) -> tuple[str, list[str]]:
    found = next(i for i in Store(root).load()[1] if i["id"] == item_id)
    return found["state"], found["notes"]


def test_a_dry_run_prints_one_line_per_change_and_writes_nothing(root: Path) -> None:
    DemoSource(root).append("DM: Rowan Hale", "Jordan Rivers", "on it", mine=True)  # id 7
    add(root, "review parser", "https://z.example.com/#narrow/id/3")
    add(root, "changelog", "https://z.example.com/#narrow/id/1")
    r = mep(root, "reconcile")
    assert r.code == 0, r.err
    assert r.out.splitlines() == [
        "#1 todo -> waiting: you posted 7; no reply since",
        "#2 still todo: new reply 5 after 2",
        "1 change proposed. Run with --apply.",
    ]
    assert state_of(root, 1) == ("todo", [])


def test_nothing_to_change_is_exactly_one_line(root: Path) -> None:
    add(root, "plain", "https://example.com/page")
    assert mep(root, "reconcile").out == "0 changes\n"


def test_apply_changes_only_differing_items_and_notes_the_reason(root: Path) -> None:
    DemoSource(root).append("DM: Rowan Hale", "Jordan Rivers", "on it", mine=True)
    add(root, "review parser", "https://z.example.com/#narrow/id/3")
    add(root, "changelog", "https://z.example.com/#narrow/id/1")
    r = mep(root, "reconcile", "--apply", "--by", "agent")
    assert r.code == 0 and r.out.splitlines()[-1] == "applied 1"
    state, notes = state_of(root, 1)
    assert state == "waiting" and notes[0].startswith("reconcile 20")
    assert notes[0].endswith("you posted 7; no reply since")
    assert state_of(root, 2) == ("todo", [])  # same state: untouched, no note


def test_apply_with_sync_renders_the_page_and_sync_alone_is_refused(root: Path) -> None:
    DemoSource(root).append("DM: Rowan Hale", "Jordan Rivers", "on it", mine=True)
    add(root, "review parser", "https://z.example.com/#narrow/id/3")
    assert mep(root, "reconcile", "--sync").code == 1
    assert mep(root, "reconcile", "--apply", "--sync").code == 0
    assert "waiting" in (root / "Action Items.md").read_text()


def test_without_gh_on_the_path_the_skip_is_said_once_on_stderr(root: Path, tmp_path: Path) -> None:
    add(root, "a", PR)
    add(root, "b", PR.replace("42", "43"))
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    r = mep(root, "reconcile", env={"PATH": str(empty)})
    assert r.out == "0 changes\n"
    assert r.err.count("gh is not installed") == 1


# ---- the chat side over a real Zulip HTTP server ----


def test_zulip_message_lookup_then_thread_finds_the_reply(tmp_path: Path) -> None:
    def z(id_: int, mine: bool) -> dict[str, object]:
        return {
            "id": id_, "type": "stream", "timestamp": int(NOW) - 100 + id_, "subject": "release",
            "content": "hello", "sender_full_name": "Me" if mine else "Alex",
            "sender_email": "me@example.com" if mine else "alex@example.com", "sender_id": 3,
            "display_recipient": "eng", "flags": [],
        }  # fmt: skip

    seen = Seen(messages=[z(1, True), z(2, False)])
    store = Store(tmp_path)
    it = item(store, "todo", "https://z.example.com/#narrow/id/1")
    with serve(seen) as base:
        src = ZulipSource(tmp_path, Credentials("me@example.com", base, "k"))
        changes, notes = propose([it], lambda: src, None)
    assert [c.line() for c in changes] == ["#1 still todo: new reply 2 after 1"]
    assert notes == []


def test_with_several_refs_the_new_reply_outranks_waiting(root: Path) -> None:
    DemoSource(root).append("DM: Rowan Hale", "Jordan Rivers", "on it", mine=True)
    add(
        root,
        "both",
        "https://z.example.com/#narrow/id/3",
        "--ref",
        "https://z.example.com/#narrow/id/1",
    )
    assert mep(root, "reconcile").out == "#1 still todo: new reply 5 after 2\n0 changes\n"


def test_each_message_and_thread_is_read_once_per_run(tmp_path: Path) -> None:
    def z(id_: int, mine: bool) -> dict[str, object]:
        return {
            "id": id_, "type": "stream", "timestamp": int(NOW) - 100 + id_, "subject": "release",
            "content": "hello", "sender_full_name": "Me" if mine else "Alex",
            "sender_email": "me@example.com" if mine else "alex@example.com", "sender_id": 3,
            "display_recipient": "eng", "flags": [],
        }  # fmt: skip

    seen = Seen(messages=[z(1, True), z(2, False)])
    store = Store(tmp_path)
    items = [item(store, "todo", "https://z.example.com/#narrow/id/1") for _ in range(3)]
    with serve(seen) as base:
        src = ZulipSource(tmp_path, Credentials("me@example.com", base, "k"))
        changes, _ = propose(items, lambda: src, None)
    assert len(changes) == 3
    assert sum(p.startswith("/api/v1/messages/1") for p in seen.paths) == 1
    assert sum(p.startswith("/api/v1/messages?") for p in seen.paths) == 1


def test_a_mark_from_another_source_is_skipped_with_a_note(root: Path) -> None:
    r = mep(root, "add", "x", "--context", "Alex: hi [inbox:zulip:1]", "--no-sync")
    assert r.code == 0, r.err
    r = mep(root, "reconcile")
    assert r.out == "0 changes\n"
    assert "skipped #1: ref zulip:1 is not demo" in r.err
