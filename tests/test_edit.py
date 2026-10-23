"""`mep edit`: replace the text of a message I sent, over a real local Zulip-shaped server."""

import argparse
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from miseenplace.cli import run_edit
from miseenplace.consent import pending_drafts
from miseenplace.model import MiseError
from miseenplace.sources.base import SourceError
from miseenplace.sources.demo import DemoSource
from miseenplace.sources.zulip import Credentials, ZulipSource, edit_failure
from miseenplace.tour import seed
from tests.conftest import run_cli
from tests.http_server import Seen, serve

ME = "me@example.com"


def row(id_: int, email: str) -> dict[str, object]:
    return {
        "id": id_, "type": "stream", "timestamp": 1_700_000_000 + id_, "subject": "release",
        "content": "status: building", "sender_full_name": "Me", "sender_email": email,
        "sender_id": 3, "display_recipient": "eng", "flags": [],
    }  # fmt: skip


def source(tmp_path: Path, base: str) -> ZulipSource:
    return ZulipSource(tmp_path, Credentials(ME, base, "secret-key"))


def test_edit_stores_a_draft_and_patches_nothing_until_send(tmp_path: Path) -> None:
    seen = Seen(messages=[row(7, ME)])
    with serve(seen) as base:
        d = source(tmp_path, base).edit("7", "status: tests green")
        assert seen.patches == [] and seen.posts == []
        assert d.target.edit_of == "7"
        assert [p.id for p in pending_drafts(tmp_path, "zulip")] == [d.id]
        assert "EDIT of message 7" in d.preview()
        assert source(tmp_path, base).send(d.id) == "7"
    assert seen.patches == [("/api/v1/messages/7", {"content": ["status: tests green"]})]
    assert seen.posts == []
    assert pending_drafts(tmp_path, "zulip") == []


def test_editing_someone_elses_message_is_refused_and_stores_no_draft(tmp_path: Path) -> None:
    seen = Seen(messages=[row(7, "alex@example.com")])
    with serve(seen) as base, pytest.raises(MiseError, match="message 7 is not yours"):
        source(tmp_path, base).edit("7", "x")
    assert seen.patches == [] and pending_drafts(tmp_path, "zulip") == []


def test_editing_an_id_that_does_not_exist_says_so(tmp_path: Path) -> None:
    seen = Seen(messages=[row(7, ME)])
    with serve(seen) as base, pytest.raises(MiseError, match="no message 99"):
        source(tmp_path, base).edit("99", "x")
    assert pending_drafts(tmp_path, "zulip") == []


def test_an_expired_edit_window_says_so_at_send(tmp_path: Path) -> None:
    seen = Seen(mode="expired", messages=[row(7, ME)])
    with serve(seen) as base:
        src = source(tmp_path, base)
        d = src.edit("7", "x")
        with pytest.raises(MiseError, match="edit window expired"):
            src.send(d.id)
    assert len(seen.patches) == 1
    assert [p.id for p in pending_drafts(tmp_path, "zulip")] == [d.id]  # still pending, not sent
    assert src.outbox.get(d.id).status == "drafted"


def test_a_server_error_at_edit_time_is_reported_as_is_and_stores_no_draft(tmp_path: Path) -> None:
    seen = Seen(mode="down")
    with serve(seen) as base, pytest.raises(SourceError, match="HTTP 500") as e:
        source(tmp_path, base).edit("7", "x")
    assert "edit window" not in str(e.value) and "not yours" not in str(e.value)
    assert pending_drafts(tmp_path, "zulip") == []


def closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def test_a_refused_connection_at_edit_time_is_reported_as_is_and_stores_no_draft(
    tmp_path: Path,
) -> None:
    with pytest.raises(SourceError, match="zulip unreachable \\(ConnectionRefusedError\\)"):
        source(tmp_path, closed_port_url()).edit("7", "x")
    assert pending_drafts(tmp_path, "zulip") == []


def test_a_refused_connection_at_send_leaves_the_edit_draft_pending(tmp_path: Path) -> None:
    seen = Seen(messages=[row(7, ME)])
    with serve(seen) as base:
        src = source(tmp_path, base)
        d = src.edit("7", "x")
    with pytest.raises(MiseError, match="ConnectionRefusedError"):  # the server is gone now
        src.send(d.id)
    assert [p.id for p in pending_drafts(tmp_path, "zulip")] == [d.id]  # not `unknown`


def test_a_non_numeric_id_is_refused_without_a_request(tmp_path: Path) -> None:
    seen = Seen()
    with serve(seen) as base, pytest.raises(MiseError, match="bad zulip message id"):
        source(tmp_path, base).edit("../users", "x")
    assert seen.paths == [] and seen.patches == []


def test_changing_edit_of_after_drafting_fails_the_hash_check(tmp_path: Path) -> None:
    seen = Seen(messages=[row(7, ME), row(8, ME)])
    with serve(seen) as base:
        src = source(tmp_path, base)
        d = src.edit("7", "x")
        path = tmp_path / "state" / "drafts" / f"{d.id}.json"
        path.write_text(path.read_text().replace('"edit_of": "7"', '"edit_of": "8"'))
        with pytest.raises(MiseError, match="modified"):
            src.send(d.id)
    assert seen.patches == []


@pytest.mark.parametrize(
    ("detail", "want"),
    [
        ("The time limit for editing this message has passed", "edit window expired"),
        ("You don't have permission to edit this message", "is not yours"),
        ("Invalid message(s)", "no message 5"),
        ("Your organization has turned off message editing", "turned off message editing"),
        ("", "no reason given"),
    ],
)
def test_zulip_error_texts_map_to_a_reason(detail: str, want: str) -> None:
    assert want in edit_failure("5", detail)


def test_the_error_names_no_key(tmp_path: Path) -> None:
    seen = Seen(mode="expired", messages=[row(7, ME)])
    with serve(seen) as base:
        src = source(tmp_path, base)
        draft_id = src.edit("7", "x").id
        with pytest.raises(MiseError) as e:
            src.send(draft_id)
    assert "secret-key" not in str(e.value)


# ---- the command, as a process (demo source: no network) ----


def edit_draft(root: Path, message_id: str, text: str) -> str:
    r = run_cli(root, "--source", "demo", "edit", message_id, text)
    assert (r.code, r.err) == (0, "")
    assert "EDIT of message" in r.out
    return r.out.split("draft ")[1].split()[0]


def test_cli_edit_then_send_replaces_my_message(tmp_path: Path) -> None:
    seed(tmp_path)
    demo = DemoSource(tmp_path)
    posted = demo.append("#platform > release 2.4", "Jordan Rivers", "status: building", mine=True)
    draft_id = edit_draft(tmp_path, posted, "status: done")
    assert demo.message(posted).text == "status: building"  # edit alone changes nothing
    listed = run_cli(tmp_path, "--source", "demo", "drafts").out
    assert draft_id in listed and f"EDIT of message {posted}" in listed
    r = run_cli(tmp_path, "--source", "demo", "send", draft_id)
    assert (r.code, r.out.strip(), r.err) == (0, f"edited demo message {posted}", "")
    assert demo.message(posted).text == "status: done"


def test_cli_edit_with_a_dash_reads_the_new_text_from_stdin(tmp_path: Path) -> None:
    seed(tmp_path)
    posted = DemoSource(tmp_path).append("#platform > x", "Jordan Rivers", "a", mine=True)
    r = run_cli(tmp_path, "--source", "demo", "edit", posted, "-")
    assert r.code == 1 and "empty message" in r.err  # stdin is empty here


def test_cli_edit_of_a_message_that_is_not_mine_exits_1_with_the_reason(tmp_path: Path) -> None:
    seed(tmp_path)
    r = run_cli(tmp_path, "--source", "demo", "edit", "5", "x")
    assert r.code == 1
    assert "message 5 is not yours" in r.err
    assert r.out == ""
    assert run_cli(tmp_path, "--source", "demo", "drafts").out == ""  # no draft stored


def test_cli_edit_of_a_missing_id_exits_1_with_the_reason(tmp_path: Path) -> None:
    seed(tmp_path)
    r = run_cli(tmp_path, "--source", "demo", "edit", "9999", "x")
    assert r.code == 1
    assert "no message 9999" in r.err


def test_cli_send_prints_the_posted_id_as_the_last_word(tmp_path: Path) -> None:
    seed(tmp_path)
    drafted = run_cli(tmp_path, "--source", "demo", "draft", "--reply-to", "5", "Thursday")
    draft_id = drafted.out.split("draft ")[1].split()[0]
    sent = run_cli(tmp_path, "--source", "demo", "send", draft_id).out.strip()
    posted = sent.split()[-1]
    assert sent == f"sent demo message {posted}"
    edit_draft(tmp_path, posted, "Friday")


def test_cli_send_of_an_edit_draft_that_fails_exits_1_with_the_reason(tmp_path: Path) -> None:
    seed(tmp_path)
    demo = DemoSource(tmp_path)
    posted = demo.append("#platform > x", "Jordan Rivers", "a", mine=True)
    draft_id = edit_draft(tmp_path, posted, "b")
    inbox = tmp_path / "demo" / "inbox.json"
    inbox.write_text(inbox.read_text().replace('"mine": true', '"mine": false'))  # no longer mine
    r = run_cli(tmp_path, "--source", "demo", "send", draft_id)
    assert r.code == 1
    assert f"message {posted} is not yours" in r.err
    assert r.out == ""
    assert draft_id in run_cli(tmp_path, "--source", "demo", "drafts").out  # still pending


def test_edit_on_a_source_without_edit_says_so() -> None:
    class NoEdit:  # a real, minimal source object that offers no `edit`
        name = "plain"

    with pytest.raises(MiseError, match="source 'plain' does not offer edit"):
        run_edit(NoEdit(), argparse.Namespace(message_id="1", text="x"))  # type: ignore[arg-type]


def test_cli_edit_with_a_dash_and_real_stdin_text(tmp_path: Path) -> None:
    seed(tmp_path)
    posted = DemoSource(tmp_path).append("#platform > x", "Jordan Rivers", "a", mine=True)
    base = [sys.executable, "-m", "miseenplace", "--dir", str(tmp_path), "--source", "demo"]
    r = subprocess.run(
        [*base, "edit", posted, "-"], input="status: from stdin", capture_output=True,
        text=True, check=False,
    )  # fmt: skip
    assert (r.returncode, r.stderr) == (0, "")
    assert "status: from stdin" in r.stdout
    draft_id = r.stdout.split("draft ")[1].split()[0]
    assert run_cli(tmp_path, "--source", "demo", "send", draft_id).code == 0
    assert DemoSource(tmp_path).message(posted).text == "status: from stdin"
