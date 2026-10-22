"""Write-ahead log behaviour: replay, validation, actors, work fields, diary."""

import json
from pathlib import Path

import pytest

from miseenplace.model import MiseError
from miseenplace.store import Store
from tests.conftest import LEGACY_ADD, entry_line, log_path, snap_path, write_log

NEW_FIELDS: dict[str, object] = {
    "topic": None, "context": None, "next": None, "blocker": None, "decisions": [],
    "think": None, "priority": None, "spent": 0, "tags": [],
}  # fmt: skip


def test_snapshot_rebuilds_from_log_alone(store: Store) -> None:
    store.add("review #1", who="A", eta="2026-10-22")
    store.update("1", "set", state="doing", note="started")
    snap_path(store).unlink()
    seq, items = store.load()
    assert seq == 2
    assert items[0]["state"] == "doing"
    assert items[0]["notes"] == ["started"]


def test_rebuild_after_every_op_is_identical_to_snapshot(store: Store) -> None:
    store.add("a", priority="p1", topic="t", spent="30m", tags=["x"])
    store.update("a", "set", spent="1h", decisions=["use A::simpler"], untags=["x"])
    store.log("diary", spent="10m")
    store.update("a", "done")
    snapshot = json.loads(snap_path(store).read_text())
    snap_path(store).unlink()
    seq, items = store.load()
    assert {"seq": seq, "items": items} == {k: snapshot[k] for k in ("seq", "items")}


def test_replay_uses_only_entries_newer_than_snapshot(store: Store) -> None:
    store.add("a")
    store.add("b")
    snap = json.loads(snap_path(store).read_text())
    snap["seq"], snap["items"] = 1, snap["items"][:1]
    snap_path(store).write_text(json.dumps(snap))
    seq, items = store.load()
    assert (seq, [i["title"] for i in items]) == (2, ["a", "b"])


def test_log_records_old_and_new_values_and_actor(store: Store) -> None:
    store.add("x", eta="2026-10-22")
    store.update("x", "done", by="me", note="merged")
    last = store.read_log()[-1]
    assert (last["op"], last["by"]) == ("done", "me")
    assert last["changes"]["state"] == ["todo", "done"]


def test_log_gap_fails_loudly(store: Store) -> None:
    store.add("a")
    with log_path(store).open("a") as f:
        f.write(entry_line(5, "set", 1, {}))
    snap_path(store).unlink()
    with pytest.raises(MiseError, match="log gap"):
        store.load()


def test_invalid_item_is_never_written(store: Store) -> None:
    with pytest.raises(MiseError):
        store.add("bad", refs=["not-a-url"])
    assert store.read_log() == []


def test_old_format_log_replays_with_defaults(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('actors = ["alice", "bob"]\n')
    s = Store(tmp_path)
    write_log(
        s,
        entry_line(1, "add", 1, LEGACY_ADD, by="alice"),
        entry_line(2, "set", 1, {"state": ["todo", "doing"]}, by="alice"),
    )
    seq, items = s.load()
    assert seq == 2
    assert items[0]["state"] == "doing"
    for k in ("topic", "context", "next", "blocker", "think", "priority"):
        assert items[0][k] is None, k
    assert (items[0]["decisions"], items[0]["spent"], items[0]["tags"]) == ([], 0, [])


def test_old_snapshot_without_work_fields_accepts_new_writes(store: Store) -> None:
    write_log(store, entry_line(1, "add", 1, LEGACY_ADD))
    snap_path(store).parent.mkdir(parents=True, exist_ok=True)
    snap_path(store).write_text(json.dumps({"seq": 1, "items": [LEGACY_ADD]}))
    store.update("1", "set", priority="p0", spent="15m")
    _, items = store.load()
    assert (items[0]["priority"], items[0]["spent"]) == ("p0", 15)


def test_new_writes_must_carry_every_field(store: Store) -> None:
    store.add("x")
    with pytest.raises(MiseError, match="item fields"):
        store.append_entry(1, [], "add", 2, dict(LEGACY_ADD, id=2), "agent")
    assert len(store.read_log()) == 1


def test_existing_log_lines_are_never_rewritten(store: Store) -> None:
    write_log(store, entry_line(1, "add", 1, LEGACY_ADD))
    before = log_path(store).read_text()
    store.update("1", "set", topic="t")
    assert log_path(store).read_text().startswith(before)


# ---- actors ----


def test_unknown_actor_is_rejected_and_not_written(store: Store) -> None:
    with pytest.raises(MiseError, match=r"unknown actor 'mallory'.*config.toml"):
        store.add("x", by="mallory")
    assert store.read_log() == []


def test_actors_come_from_config_with_a_default(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('actors = ["alice", "bob"]\ndefault_actor = "bob"\n')
    s = Store(tmp_path)
    assert s.add("x")["id"] == 1
    assert s.read_log()[0]["by"] == "bob"
    s.add("y", by="alice")
    with pytest.raises(MiseError):
        s.add("z", by="agent")


def test_bad_config_default_actor_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('actors = ["a"]\ndefault_actor = "b"\n')
    with pytest.raises(MiseError, match="default_actor 'b' is not in actors"):
        Store(tmp_path)


# ---- work fields ----


def test_spent_accumulates_across_writes_in_minutes(store: Store) -> None:
    store.add("x", spent="30m")
    store.update("x", "set", spent="1h")
    item = store.update("x", "set", spent="1h30m")
    assert item["spent"] == 180
    assert store.read_log()[-1]["changes"]["spent"] == [90, 180]


@pytest.mark.parametrize("bad", ["abc", "0m", "", "1.5h", "-5m", "90"])
def test_invalid_spent_is_rejected_and_never_written(store: Store, bad: str) -> None:
    store.add("x")
    with pytest.raises(MiseError, match="bad time spent"):
        store.update("x", "set", spent=bad)
    with pytest.raises(MiseError):
        store.add("y", spent=bad)
    assert len(store.read_log()) == 1


@pytest.mark.parametrize("bad", ["p9", "high", "P1"])
def test_invalid_priority_is_rejected_and_never_written(store: Store, bad: str) -> None:
    store.add("x")
    with pytest.raises(MiseError, match="bad priority"):
        store.update("x", "set", priority=bad)
    with pytest.raises(MiseError):
        store.add("y", priority=bad)
    assert len(store.read_log()) == 1


def test_priority_none_clears(store: Store) -> None:
    store.add("x", priority="p1")
    assert store.update("x", "set", priority="none")["priority"] is None


def test_decisions_append_and_are_never_rewritten(store: Store) -> None:
    store.add("x", decisions=["use A::it is simpler"])
    item = store.update("x", "set", decisions=["drop B::too slow", "ship C::cheap"])
    assert [d["what"] for d in item["decisions"]] == ["use A", "drop B", "ship C"]
    assert item["decisions"][0]["why"] == "it is simpler"
    assert len(store.update("x", "set", note="n")["decisions"]) == 3


@pytest.mark.parametrize("bad", ["no separator", "::why only", "what only::"])
def test_malformed_decision_is_rejected(store: Store, bad: str) -> None:
    store.add("x")
    with pytest.raises(MiseError, match="what::why"):
        store.update("x", "set", decisions=[bad])
    assert len(store.read_log()) == 1


def test_tag_and_untag(store: Store) -> None:
    store.add("x", tags=["a", "b", "a"])
    assert store.update("x", "set", tags=["c", "b"])["tags"] == ["a", "b", "c"]
    assert store.update("x", "set", untags=["b", "zzz"])["tags"] == ["a", "c"]


def test_text_fields_set_and_clear(store: Store) -> None:
    item = store.add("x", topic="T", context="why", next="step", blocker="Sam", think="slug")
    assert (item["topic"], item["context"], item["think"]) == ("T", "why", "slug")
    item = store.update("x", "set", topic="", blocker="")
    assert item["topic"] is None
    assert item["blocker"] is None
    assert item["next"] == "step"


def test_hand_written_bad_priority_fails_replay(store: Store) -> None:
    write_log(store, entry_line(1, "add", 1, {**LEGACY_ADD, **NEW_FIELDS, "priority": "p9"}))
    with pytest.raises(MiseError, match="bad priority"):
        store.load()


# ---- diary ----


def test_log_entry_is_stored_with_id_zero_and_continues_seq(store: Store) -> None:
    store.add("x")
    e = store.log(
        "paired on parser",
        topic="Parser",
        tags=["pair", "pair"],
        spent="1h",
        refs=["https://example.com/2"],
    )
    assert (e["seq"], e["op"], e["id"]) == (2, "log", 0)
    assert e["changes"]["spent"] == 60
    assert e["changes"]["tags"] == ["pair"]
    store.add("y")
    snap_path(store).unlink()
    seq, items = store.load()
    assert (seq, [i["id"] for i in items]) == (3, [1, 2])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": "  "},
        {"text": "x", "spent": "soon"},
        {"text": "x", "refs": ["nope"]},
        {"text": "x", "topic": " "},
    ],
)
def test_invalid_log_entries_are_rejected_and_never_written(
    store: Store, kwargs: dict[str, str | list[str]]
) -> None:
    with pytest.raises(MiseError):
        store.log(**kwargs)  # type: ignore[arg-type]
    assert store.read_log() == []


def test_log_entry_with_nonzero_id_fails_replay(store: Store) -> None:
    changes: dict[str, object] = {"text": "x", "topic": None, "tags": [], "spent": 0, "refs": []}
    write_log(store, entry_line(1, "log", 3, changes))
    with pytest.raises(MiseError, match="id 0"):
        store.load()


# ---- items ----


def test_waiting_items_start_in_waiting_state(store: Store) -> None:
    assert store.add("nudge V", kind="waiting")["state"] == "waiting"


def test_ambiguous_match_is_refused(store: Store) -> None:
    store.add("review 7042")
    store.add("review 7043")
    with pytest.raises(MiseError, match="2 items match"):
        store.update("review", "done")


# ---- sync ----


def test_sync_on_local_files_renders_the_page(store: Store) -> None:
    store.add("x")
    store.sync("test")
    assert "x" in (store.root / "Action Items.md").read_text()
