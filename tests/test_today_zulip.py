"""`today` over a Zulip source talking to a real local HTTP server."""

import argparse
import time
from pathlib import Path

import pytest

from miseenplace.cli import run_today
from miseenplace.sources.zulip import Credentials, ZulipSource
from tests.http_server import Seen, serve

ME = "me@example.com"
NOW = int(time.time())


def zmsg(id_: int, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": id_, "type": "stream", "timestamp": NOW - 100 + id_, "subject": "release",
        "content": "hello", "sender_full_name": "Alex", "sender_email": "alex@example.com",
        "sender_id": 3, "display_recipient": "eng", "flags": [],
    }  # fmt: skip
    return base | over


def run(tmp_path: Path, base: str, capsys: pytest.CaptureFixture[str]) -> str:
    src = ZulipSource(tmp_path, Credentials(ME, base, "k"), frozenset({"alerts"}))
    run_today(src, argparse.Namespace(days=1.0, width=100))
    return capsys.readouterr().out


def test_wildcard_mention_and_a_three_person_dm_are_read_from_the_wire(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = Seen(messages=[
        zmsg(1, flags=["wildcard_mentioned"], content="@**all** standup moved"),
        zmsg(2, type="private", subject="", display_recipient=[],
             content="lunch?"),
        zmsg(3, subject="other", content="not for me"),
    ])  # fmt: skip
    seen.messages[1]["display_recipient"] = [
        {"id": 1, "email": ME, "full_name": "Me"},
        {"id": 2, "email": "kim@example.com", "full_name": "Kim"},
        {"id": 3, "email": "alex@example.com", "full_name": "Alex"},
    ]
    with serve(seen) as base:
        out = run(tmp_path, base, capsys)
    assert out.splitlines()[0] == "2 threads: 2 awaiting you, 0 you replied last, 0 fyi"
    assert "awaiting you  #eng > release  1 msg" in out
    assert "awaiting you  DM: Alex, Kim  1 msg" in out  # names sorted, me left out
    assert "other" not in out


def test_a_thread_i_posted_in_counts_even_in_a_noise_stream(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = Seen(messages=[
        zmsg(1, display_recipient="alerts", subject="disk", content="disk 90%"),
        zmsg(2, display_recipient="alerts", subject="disk", content="I'll look",
             sender_email=ME, sender_full_name="Me"),
        zmsg(3, display_recipient="alerts", subject="cpu", content="cpu high"),
    ])  # fmt: skip
    with serve(seen) as base:
        out = run(tmp_path, base, capsys)
    assert "you replied last  #alerts > disk  2 msgs" in out
    assert "cpu" not in out  # noise I never touched stays out
