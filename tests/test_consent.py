"""The consent gate, exercised through the real demo source and real files."""

import inspect
import json
from pathlib import Path

import pytest

from miseenplace.consent import ConsentError, Outbox, Target
from miseenplace.sources.base import Source, SourceError
from miseenplace.sources.demo import DemoSource
from miseenplace.tour import seed

CHANNEL = Target("platform", "release 2.4")


@pytest.fixture
def src(tmp_path: Path) -> DemoSource:
    seed(tmp_path)
    return DemoSource(tmp_path)


def sent_lines(src: DemoSource) -> list[dict[str, str]]:
    path = src.dir / "sent.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_draft_stores_the_exact_text_and_posts_nothing(src: DemoSource) -> None:
    text = "  exact text,\nwith  odd spacing 👍  "
    d = src.draft(CHANNEL, text)
    assert d.text == text
    assert src.outbox.get(d.id).text == text
    assert sent_lines(src) == []


def test_send_posts_exactly_the_stored_text_once(src: DemoSource) -> None:
    d = src.draft(CHANNEL, "ship it")
    ref = src.send(d.id)
    assert [x["text"] for x in sent_lines(src)] == ["ship it"]
    assert src.outbox.get(d.id).status == "sent"
    assert src.outbox.get(d.id).sent_ref == ref


def test_second_send_of_the_same_draft_is_refused(src: DemoSource) -> None:
    d = src.draft(CHANNEL, "once")
    src.send(d.id)
    with pytest.raises(ConsentError, match="already sent"):
        src.send(d.id)
    assert len(sent_lines(src)) == 1


@pytest.mark.parametrize("free_text", ["hello there", "d12345678", "../../etc/passwd", ""])
def test_send_refuses_anything_that_is_not_a_known_draft_id(
    src: DemoSource, free_text: str
) -> None:
    with pytest.raises(ConsentError):
        src.send(free_text)
    assert sent_lines(src) == []


def test_a_draft_edited_after_drafting_is_refused(src: DemoSource) -> None:
    d = src.draft(CHANNEL, "approved text")
    path = src.outbox.dir / f"{d.id}.json"
    data = json.loads(path.read_text())
    data["text"] = "something else entirely"
    path.write_text(json.dumps(data))
    with pytest.raises(ConsentError, match="modified"):
        src.send(d.id)
    assert sent_lines(src) == []


def test_failed_delivery_leaves_the_draft_sendable(src: DemoSource) -> None:
    d = src.draft(CHANNEL, "retry me")
    inbox = src.dir / "inbox.json"
    saved = inbox.read_text()
    inbox.unlink()  # the service is "down": delivery really fails
    with pytest.raises(SourceError):
        src.send(d.id)
    inbox.write_text(saved)
    src.send(d.id)
    assert [x["text"] for x in sent_lines(src)] == ["retry me"]


def test_empty_draft_is_refused(src: DemoSource) -> None:
    with pytest.raises(ConsentError, match="empty"):
        src.draft(CHANNEL, "   \n")


def test_drafts_belong_to_their_source(tmp_path: Path, src: DemoSource) -> None:
    d = src.draft(CHANNEL, "mine")
    other = Outbox(tmp_path, "other", lambda t, x: "never")
    with pytest.raises(ConsentError, match="belongs to source"):
        other.send(d.id)


def test_send_takes_only_a_draft_id() -> None:
    for fn in (Outbox.send, DemoSource.send):
        assert list(inspect.signature(fn).parameters)[1:] == ["draft_id"]
    assert "draft_id" in inspect.signature(Source.send).parameters


def test_reply_quotes_the_message_and_shows_where_it_goes(src: DemoSource) -> None:
    d = src.draft(Target("", reply_to="5"), "Thursday")
    assert d.text.startswith("> Changelog still pending, ETA?")
    assert d.text.endswith("Thursday")
    assert "release 2.4" in d.preview()


def test_a_discarded_draft_is_not_listed_and_cannot_be_sent(src: DemoSource) -> None:
    d = src.draft(CHANNEL, "never mind")
    src.outbox.discard(d.id)
    assert d.id not in [x.id for x in src.outbox.pending()]
    with pytest.raises(ConsentError):
        src.send(d.id)
    assert sent_lines(src) == []


def test_a_sent_draft_cannot_be_discarded(src: DemoSource) -> None:
    d = src.draft(CHANNEL, "posted")
    src.send(d.id)
    with pytest.raises(ConsentError, match="only an unsent draft"):
        src.outbox.discard(d.id)
    assert src.outbox.get(d.id).status == "sent"
