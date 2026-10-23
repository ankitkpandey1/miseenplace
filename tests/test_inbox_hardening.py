"""Inbox and watch under failure: errors mid-way, answered elsewhere, retries, bad intervals."""

import json
from pathlib import Path

import pytest

from miseenplace.chat import Message
from miseenplace.inbox import Inbox, InboxItem, answered, from_message
from miseenplace.sources.demo import DemoSource
from miseenplace.store import Store
from miseenplace.tour import seed
from miseenplace.watch import watch
from tests.conftest import Result, run_cli


@pytest.fixture
def root(tmp_path: Path) -> Path:
    seed(tmp_path)
    return tmp_path


def mep(root: Path, *args: str) -> Result:
    return run_cli(root, "--source", "demo", *args)


def ids(items: list[InboxItem]) -> list[str]:
    return [i.id for i in items]


def obstacle(root: Path, item_id: str) -> Path:
    """A directory where one inbox file's temp file must go: that write fails."""
    blocked = root / "state" / "inbox" / "demo" / f"{item_id}.json.tmp"
    blocked.mkdir(parents=True)
    return blocked


# ---- A1: items opened before an error are still delivered ----


def test_an_error_midway_through_classification_loses_nothing(root: Path) -> None:
    src = DemoSource(root)
    inbox = Inbox(root, src)
    for m in src.fetch("demo").messages:  # queued earlier (status new), cursor not moved yet
        inbox._save(from_message(m))
    blocked = obstacle(root, "4")
    with pytest.raises(OSError, match=r"tmp|directory"):
        inbox.ingest("demo")  # 3 is opened and saved, then 4 cannot be written
    blocked.rmdir()
    assert ids(Inbox(root, src).ingest("demo")) == ["3", "4", "5"]  # 3 is not forgotten


# ---- A2: the cursor moves only after the queue write ----


def test_a_failed_queue_write_leaves_the_cursor_so_the_retry_loses_nothing(root: Path) -> None:
    src = DemoSource(root)
    before = src.cursors.get("demo")
    blocked = obstacle(root, "4")
    with pytest.raises(OSError, match=r"tmp|directory"):
        Inbox(root, src).ingest("demo")
    assert src.cursors.get("demo") == before  # not advanced past messages never queued
    blocked.rmdir()
    assert ids(Inbox(root, src).ingest("demo")) == ["3", "4", "5"]


def test_new_advances_the_cursor_after_printing(root: Path) -> None:
    r = mep(root, "new", "--cursor", "fresh", "--init")
    assert r.code == 0
    DemoSource(root).append("DM: Rowan Hale", "Rowan Hale", "ping")
    first = mep(root, "new", "--cursor", "fresh")
    assert "ping" in first.out
    assert mep(root, "new", "--cursor", "fresh").out.strip() == ""  # advanced after the output


# ---- A9: watch survives anything, stays silent, rejects silly intervals ----


@pytest.mark.parametrize("bad", ["0", "-5", "5"])
def test_watch_rejects_intervals_below_ten_seconds(root: Path, bad: str) -> None:
    r = mep(root, "watch", "--cursor", "demo", f"--interval={bad}", "--once")
    assert r.code != 0
    assert "10" in r.err


def test_watch_survives_an_error_that_is_not_ours_and_logs_it(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (root / "demo" / "inbox.json").write_text("{ not json")
    code = watch(root, lambda: DemoSource(root), "demo", 10.0, once=True, max_cycles=0)
    shown = capsys.readouterr()
    assert code == 0
    assert (shown.out, shown.err) == ("", "")  # a bad cycle never wakes anyone
    assert "JSONDecodeError" in (root / "state" / "watch.log").read_text()


def test_watch_once_with_nothing_new_exits_zero_and_prints_nothing(root: Path) -> None:
    mep(root, "inbox", "--cursor", "demo")  # shown
    r = mep(root, "watch", "--cursor", "demo", "--once")
    assert (r.code, r.out) == (0, "")


# ---- A10: answered is judged by message order ----


def listed(r: Result) -> list[str]:
    return [x.split(" · ")[0] for x in r.out.splitlines() if " · " in x]


def message(id_: str, ts: float, mine: bool) -> Message:
    return Message(id_, "#ops > x", "Sam", ts, "text", False, mine)


def test_my_reply_counts_even_when_its_timestamp_is_not_later() -> None:
    item = from_message(message("9", 1000.0, False))
    assert answered(item, [message("10", 1000.0, True)])  # equal timestamps
    assert answered(item, [message("10", 900.0, True)])  # clock skew
    assert not answered(item, [message("8", 2000.0, True)])  # earlier message, later clock


def test_replying_in_the_chat_app_removes_the_ask_from_the_inbox(root: Path) -> None:
    first = mep(root, "inbox", "--cursor", "demo")
    assert listed(first) == ["3", "4", "5"]
    where = next(m for m in DemoSource(root).digest(30) if m.id == "4").where
    DemoSource(root).append(where, "me", "done, thanks", mine=True)
    after = mep(root, "inbox", "--cursor", "demo")
    assert listed(after) == ["3", "5"]  # only the answered ask goes


# ---- B12: triage is safe to retry ----


def test_a_triage_that_died_after_filing_does_not_file_twice(root: Path) -> None:
    mep(root, "inbox", "--cursor", "demo")
    blocked = obstacle(root, "3")
    failed = mep(root, "triage", "3", "--todo")
    assert failed.code != 0
    blocked.rmdir()
    assert mep(root, "triage", "3", "--todo").code == 0
    filed = [i for i in Store(root).load()[1] if "[inbox:demo:3]" in (i["context"] or "")]
    assert len(filed) == 1


@pytest.mark.parametrize("flag", [["--ignore"], ["--reply-draft", "ok"]])
@pytest.mark.parametrize("extra", [["--eta", "2026-11-01"], ["--topic", "x"]])
def test_eta_and_topic_are_rejected_where_nothing_is_filed(
    root: Path, flag: list[str], extra: list[str]
) -> None:
    mep(root, "inbox", "--cursor", "demo")
    r = mep(root, "triage", "3", *flag, *extra)
    assert r.code == 1
    assert "only go with" in r.err
    assert Inbox(root, DemoSource(root)).get("3").status == "open"


def test_an_inbox_item_with_keys_from_a_newer_version_still_loads(root: Path) -> None:
    inbox = Inbox(root, DemoSource(root))
    inbox.ingest("demo")
    path = next(inbox.dir.glob("*.json"))
    data = json.loads(path.read_text())
    data["later"] = 1
    if data["target"]:
        data["target"]["edit_of"] = "7"
        data["target"]["later"] = 2
    path.write_text(json.dumps(data))
    assert inbox.get(path.stem).id == path.stem
    assert path.stem in [i.id for i in inbox.items()]
