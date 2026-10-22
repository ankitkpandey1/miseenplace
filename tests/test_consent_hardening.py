"""The consent gate under failure: edited drafts, unsure deliveries, crashes, expiry, privacy."""

import json
import stat
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from miseenplace.chat import Target
from miseenplace.consent import ConsentError, Outbox, mark_draft, pending_drafts
from miseenplace.sources import zulip as zulip_module
from miseenplace.sources.base import SourceError
from miseenplace.sources.demo import DemoSource
from miseenplace.sources.zulip import Credentials, ZulipSource
from miseenplace.tour import seed
from tests.http_server import Seen, serve

STREAM = Target("ops", "deploys", label="#ops > deploys")


def zulip_at(root: Path, base: str) -> ZulipSource:
    return ZulipSource(root, Credentials("me@example.com", base, "key"))


@pytest.fixture
def fast_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zulip_module, "TIMEOUT", 0.4)


def draft_file(root: Path, draft_id: str) -> Path:
    return root / "state" / "drafts" / f"{draft_id}.json"


def edit_draft(root: Path, draft_id: str, change: Mapping[str, object]) -> None:
    path = draft_file(root, draft_id)
    data = json.loads(path.read_text())
    data["target"].update(change)
    path.write_text(json.dumps(data))


# ---- A3: the hash covers the destination ----


@pytest.mark.parametrize("change", [{"to": "elsewhere"}, {"thread": "other topic"}])
def test_editing_the_destination_of_a_draft_is_refused(
    tmp_path: Path, change: dict[str, str]
) -> None:
    seen = Seen()
    with serve(seen) as base:
        src = zulip_at(tmp_path, base)
        d = src.draft(STREAM, "hello")
        edit_draft(tmp_path, d.id, change)
        with pytest.raises(ConsentError, match="modified"):
            src.send(d.id)
    assert seen.posts == []  # nothing left the machine


# ---- A4: an unsure delivery is never released ----


def test_a_timeout_after_the_post_marks_the_draft_unknown_and_refuses_a_resend(
    tmp_path: Path, fast_timeout: None
) -> None:
    seen = Seen(mode="hang")
    with serve(seen) as base:
        src = zulip_at(tmp_path, base)
        d = src.draft(STREAM, "maybe posted")
        with pytest.raises(ConsentError, match="could not confirm delivery"):
            src.send(d.id)
        assert len(seen.posts) == 1  # the server did accept it
        with pytest.raises(ConsentError, match=r"--mark-sent.*--mark-unsent"):
            src.send(d.id)
        assert len(seen.posts) == 1  # and a second send did NOT post again
    assert json.loads(draft_file(tmp_path, d.id).read_text())["status"] == "unknown"
    assert not draft_file(tmp_path, d.id).with_suffix(".sending").exists()


def test_a_cut_off_answer_is_unknown_too(tmp_path: Path) -> None:
    seen = Seen(mode="truncated")
    with serve(seen) as base:
        src = zulip_at(tmp_path, base)
        d = src.draft(STREAM, "cut off")
        with pytest.raises(ConsentError, match="could not confirm"):
            src.send(d.id)
    assert pending_drafts(tmp_path, "zulip") == []  # not offered for sending again


def test_a_rejected_post_definitely_did_not_send_so_the_draft_stays_sendable(
    tmp_path: Path,
) -> None:
    seen = Seen(mode="reject")
    with serve(seen) as base:
        src = zulip_at(tmp_path, base)
        d = src.draft(STREAM, "refused by the server")
        with pytest.raises(SourceError, match="HTTP 400"):
            src.send(d.id)
        assert [x.id for x in pending_drafts(tmp_path, "zulip")] == [d.id]
        seen.mode = "ok"
        assert src.send(d.id)


def test_a_refused_connection_definitely_did_not_send(tmp_path: Path) -> None:
    src = zulip_at(tmp_path, "http://127.0.0.1:1")
    d = src.draft(STREAM, "nobody home")
    with pytest.raises(SourceError, match="unreachable"):
        src.send(d.id)
    assert [x.id for x in pending_drafts(tmp_path, "zulip")] == [d.id]


def test_the_human_resolves_an_unknown_draft_either_way(tmp_path: Path, fast_timeout: None) -> None:
    seen = Seen(mode="hang")
    with serve(seen) as base:
        src = zulip_at(tmp_path, base)
        was_posted, was_not = src.draft(STREAM, "one"), src.draft(STREAM, "two")
        for d in (was_posted, was_not):
            with pytest.raises(ConsentError):
                src.send(d.id)
        assert mark_draft(tmp_path, "zulip", was_posted.id, sent=True).status == "sent"
        with pytest.raises(ConsentError, match="already sent"):
            src.send(was_posted.id)
        assert mark_draft(tmp_path, "zulip", was_not.id, sent=False).status == "drafted"
        seen.mode = "ok"
        assert src.send(was_not.id)


def test_marking_a_healthy_draft_is_refused(tmp_path: Path) -> None:
    src = zulip_at(tmp_path, "http://127.0.0.1:1")
    d = src.draft(STREAM, "fine")
    with pytest.raises(ConsentError, match="nothing to mark"):
        mark_draft(tmp_path, "zulip", d.id, sent=True)


# ---- A5: a claim left behind by a crash ----


def test_a_stale_claim_blocks_sending_and_says_what_to_do(tmp_path: Path) -> None:
    seed(tmp_path)
    src = DemoSource(tmp_path)
    d = src.draft(Target("platform", "release 2.4"), "after the crash")
    draft_file(tmp_path, d.id).with_suffix(".sending").write_text("crashed")
    with pytest.raises(ConsentError, match=r"cut off.*--mark-sent.*--mark-unsent"):
        src.send(d.id)
    mark_draft(tmp_path, "demo", d.id, sent=False)
    assert src.send(d.id)


def test_the_claim_file_is_removed_after_a_successful_send(tmp_path: Path) -> None:
    seed(tmp_path)
    src = DemoSource(tmp_path)
    d = src.draft(Target("platform", "release 2.4"), "clean")
    src.send(d.id)
    assert not draft_file(tmp_path, d.id).with_suffix(".sending").exists()


# ---- A6: several sources share the drafts directory ----


def test_drafts_of_other_sources_do_not_break_listing(tmp_path: Path) -> None:
    seed(tmp_path)
    demo, zulip = DemoSource(tmp_path), zulip_at(tmp_path, "http://127.0.0.1:1")
    mine = demo.draft(Target("platform", "release 2.4"), "demo draft")
    theirs = zulip.draft(STREAM, "zulip draft")
    assert [d.id for d in pending_drafts(tmp_path, "demo")] == [mine.id]
    assert [d.id for d in pending_drafts(tmp_path, "zulip")] == [theirs.id]
    with pytest.raises(ConsentError, match="belongs to source"):
        demo.send(theirs.id)


# ---- A7: drafts expire ----


def age(root: Path, draft_id: str, hours: float) -> None:
    path = draft_file(root, draft_id)
    data = json.loads(path.read_text())
    data["created_ts"] = time.time() - hours * 3600
    path.write_text(json.dumps(data))


def test_a_draft_older_than_a_day_is_refused_and_not_listed(tmp_path: Path) -> None:
    seed(tmp_path)
    src = DemoSource(tmp_path)
    old, fresh = (src.draft(Target("platform", "release 2.4"), t) for t in ("old", "fresh"))
    age(tmp_path, old.id, 25)
    age(tmp_path, fresh.id, 23)
    assert [d.id for d in pending_drafts(tmp_path, "demo")] == [fresh.id]
    with pytest.raises(ConsentError, match="expired"):
        src.send(old.id)
    assert src.send(fresh.id)


def test_the_expiry_is_configurable(tmp_path: Path) -> None:
    seed(tmp_path)
    (tmp_path / "config.toml").write_text("[consent]\ndraft_ttl_hours = 1\n")
    src = DemoSource(tmp_path)
    d = src.draft(Target("platform", "release 2.4"), "short lived")
    age(tmp_path, d.id, 2)
    with pytest.raises(ConsentError, match="expired"):
        src.send(d.id)


# ---- A11: personal files are private ----


def test_drafts_are_owner_only(tmp_path: Path) -> None:
    seed(tmp_path)
    d = DemoSource(tmp_path).draft(Target("platform", "release 2.4"), "private")
    assert stat.S_IMODE(draft_file(tmp_path, d.id).stat().st_mode) == 0o600
    assert stat.S_IMODE(draft_file(tmp_path, d.id).parent.stat().st_mode) == 0o700


def test_outbox_requires_the_exact_id(tmp_path: Path) -> None:
    box = Outbox(tmp_path, "demo", lambda t, x: "never")
    with pytest.raises(ConsentError, match="not a draft id"):
        box.send("hello team")
