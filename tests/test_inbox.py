"""The inbox queue and `triage`, driven through the real CLI against the demo source."""

import json
from pathlib import Path

import pytest

from miseenplace.sources.demo import DemoSource
from miseenplace.tour import seed
from tests.conftest import Result, run_cli


@pytest.fixture
def root(tmp_path: Path) -> Path:
    seed(tmp_path)
    return tmp_path


def mep(root: Path, *args: str) -> Result:
    return run_cli(root, "--source", "demo", *args)


def ids(r: Result) -> list[str]:
    return [line.split(" · ")[0] for line in r.out.splitlines() if " · " in line]


def test_inbox_lists_only_what_needs_the_user(root: Path) -> None:
    r = mep(root, "inbox", "--cursor", "demo")
    assert r.code == 0
    assert ids(r) == ["3", "4", "5"]  # 1 was answered by me, 6 is a promise from someone else
    assert "· reply" in r.out and "· todo" in r.out


def test_a_message_the_user_already_answered_is_hidden(root: Path) -> None:
    assert "1" not in ids(mep(root, "inbox", "--cursor", "demo"))
    assert "1 ·" in mep(root, "inbox", "--cursor", "demo", "--all").out  # still on record


def test_json_has_the_fixed_fields(root: Path) -> None:
    rows = json.loads(mep(root, "inbox", "--cursor", "demo", "--json").out)
    assert [r["id"] for r in rows] == ["3", "4", "5"]
    assert set(rows[0]) == {"id", "who", "where", "ask", "action", "url"}
    assert {r["action"] for r in rows} <= {"reply", "todo", "waiting", "ignore"}


def test_second_cursor_sees_the_same_queue_and_the_first_is_not_disturbed(root: Path) -> None:
    for name in ("a", "b"):
        DemoSource(root).cursors.set(name, "0")
    assert ids(mep(root, "inbox", "--cursor", "a")) == ["3", "4", "5"]
    DemoSource(root).append("DM: Rowan Hale", "Rowan Hale", "can you check the build?")
    assert DemoSource(root).fetch("b").messages  # b still has its own unread messages
    seen_by_b = ids(mep(root, "inbox", "--cursor", "b"))
    assert seen_by_b == ["3", "4", "5", "7"]  # a's read did not eat b's, and b sees the new one
    assert DemoSource(root).cursors.get("b") == "7"


def test_triage_todo_writes_a_journal_item_with_context_and_hides_the_message(root: Path) -> None:
    mep(root, "inbox", "--cursor", "demo")
    r = mep(root, "triage", "3", "--todo")
    assert r.out.strip() == "todo #1"
    listing = mep(root, "list").out
    assert "parser PR" in listing and "Rowan Hale" in listing
    assert "3" not in ids(mep(root, "inbox", "--cursor", "demo"))
    brief = mep(root, "brief", "1").out
    assert "Rowan Hale in DM: Rowan Hale" in brief  # context filled in, nobody typed it


def test_triage_waiting_uses_the_waiting_kind_and_eta(root: Path) -> None:
    mep(root, "inbox", "--cursor", "demo")
    mep(root, "triage", "4", "--waiting", "--eta", "2026-12-01", "--topic", "retro")
    out = mep(root, "list").out
    assert "[waiting]" in out and "retro" in out and "12-01" in out


def test_triage_ignore_writes_no_journal_item(root: Path) -> None:
    mep(root, "inbox", "--cursor", "demo")
    assert mep(root, "triage", "5", "--ignore").out.strip() == "ignored 5"
    assert mep(root, "list").out.strip() == ""
    assert "5" not in ids(mep(root, "inbox", "--cursor", "demo"))


def test_reply_draft_stores_text_and_posts_nothing(root: Path) -> None:
    mep(root, "inbox", "--cursor", "demo")
    r = mep(root, "triage", "5", "--reply-draft", "Thursday")
    assert "Thursday" in r.out and "> Changelog still pending" in r.out
    assert not (root / "demo" / "sent.jsonl").exists()
    draft_id = r.out.split("miseenplace send ")[1].split()[0]
    assert mep(root, "send", draft_id).code == 0
    assert "Thursday" in (root / "demo" / "sent.jsonl").read_text()


def test_triaging_the_same_item_twice_is_refused_and_adds_nothing(root: Path) -> None:
    mep(root, "inbox", "--cursor", "demo")
    mep(root, "triage", "3", "--todo")
    again = mep(root, "triage", "3", "--todo")
    assert again.code == 1 and "already todo" in again.err
    assert mep(root, "list").out.count("parser PR") == 1


def test_two_actions_at_once_are_refused(root: Path) -> None:
    mep(root, "inbox", "--cursor", "demo")
    r = mep(root, "triage", "5", "--todo", "--ignore")
    assert r.code == 1 and "pick one" in r.err
    assert mep(root, "list").out.strip() == ""


def test_unknown_id_and_missing_action_are_errors(root: Path) -> None:
    mep(root, "inbox", "--cursor", "demo")
    assert "no inbox item 99" in mep(root, "triage", "99", "--todo").err
    assert "say what to do" in mep(root, "triage", "5").err


def test_a_reply_to_a_thread_makes_the_earlier_ask_disappear(root: Path) -> None:
    assert "5" in ids(mep(root, "inbox", "--cursor", "demo"))  # open before the reply
    drafted = mep(root, "draft", "--reply-to", "5", "ok").out
    assert "5" in ids(mep(root, "inbox", "--cursor", "demo"))  # a draft alone answers nothing
    mep(root, "send", drafted.split("miseenplace send ")[1].split()[0])
    assert (root / "demo" / "sent.jsonl").exists()  # it was really sent
    assert "5" not in ids(mep(root, "inbox", "--cursor", "demo"))
