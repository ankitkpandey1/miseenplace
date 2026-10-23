"""`today`: only the threads that involve the user, tagged by who is waiting on whom."""

import json
import time
from pathlib import Path

from miseenplace.chat import Message
from miseenplace.sources.zulip import ZMessage, quote_reply
from miseenplace.today import AWAITING, FYI, REPLIED, involving_me, is_ack
from tests.conftest import Result, run_cli

NOW = time.time()
ZMESSAGE: ZMessage = {
    "id": 7, "type": "stream", "timestamp": 1, "subject": "release", "content": "can you look?",
    "sender_full_name": "Alex", "sender_email": "alex@example.com", "sender_id": 3,
    "display_recipient": "eng", "flags": [],
}  # fmt: skip


def seed_day(root: Path, rows: list[tuple[str, str, str, bool, bool]]) -> None:
    """rows: (where, sender, text, mentioned, mine), oldest first, all within the last hour."""
    messages = [
        {"id": n, "where": w, "sender": s, "ts": NOW - 600 + n, "text": t, "mentioned": men,
         "mine": mine}
        for n, (w, s, t, men, mine) in enumerate(rows, 1)
    ]  # fmt: skip
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "inbox.json").write_text(
        json.dumps({"me": "Me", "people": [], "messages": messages})
    )


def today(root: Path, *args: str) -> Result:
    return run_cli(root, "--source", "demo", "today", *args)


DAY = [
    ("#eng > release", "Alex", "can you check again please?", True, False),
    ("#eng > release", "Alex", "old context", False, False),
    ("#eng > release", "Alex", "@**Me** can you check again please?", True, False),
    ("#ops > chatter", "Kim", "lunch plans", False, False),
    ("#ops > chatter", "Kim", "more lunch", False, False),
    ("DM: Sam", "Sam", "ping", False, False),
    ("DM: Sam", "Me", "done, added them", False, True),
    ("#web > bugs", "Rae", "@**Me** fyi, fixed", True, False),
    ("#web > bugs", "Me", "thanks", False, True),
    ("#web > bugs", "Rae", "np", False, False),
    ("#dev > ideas", "Lee", "I posted too", False, False),
    ("#dev > ideas", "Me", "ok", False, True),
    ("#dev > ideas", "Lee", "later", False, False),
]


def test_only_threads_that_involve_me_are_listed(tmp_path: Path) -> None:
    seed_day(tmp_path, DAY)
    r = today(tmp_path)
    assert r.code == 0, r.err
    assert "chatter" not in r.out  # no mention, no DM, no post of mine
    assert r.out.splitlines()[0] == "4 threads: 1 awaiting you, 1 you replied last, 2 fyi"


def test_state_tags_and_awaiting_first(tmp_path: Path) -> None:
    seed_day(tmp_path, DAY)
    heads = [ln for ln in today(tmp_path).out.splitlines() if "msgs" in ln]
    assert heads[0].startswith(f"{AWAITING}  #eng > release  3 msgs")
    tags = {h.split("  ")[1]: h.split("  ")[0] for h in heads}
    assert tags["DM: Sam"] == REPLIED
    assert tags["#web > bugs"] == FYI  # last message is not a mention and not a DM
    assert tags["#dev > ideas"] == FYI


def test_a_dm_awaiting_me_and_only_the_last_three_messages(tmp_path: Path) -> None:
    rows = [("DM: Sam", "Sam", f"msg {n}", False, False) for n in range(1, 6)]
    seed_day(tmp_path, rows)
    r = today(tmp_path)
    assert f"{AWAITING}  DM: Sam  5 msgs" in r.out
    assert "msg 3" in r.out and "msg 5" in r.out and "msg 2" not in r.out


def test_days_widens_the_window_and_width_cuts_a_line(tmp_path: Path) -> None:
    seed_day(tmp_path, [("DM: Sam", "Sam", "word " * 60, False, False)])
    inbox = tmp_path / "demo" / "inbox.json"
    data = json.loads(inbox.read_text())
    data["messages"][0]["ts"] = NOW - 2 * 86400
    inbox.write_text(json.dumps(data))
    assert today(tmp_path).out.startswith("0 threads")
    wide = today(tmp_path, "--days", "3", "--width", "20")
    assert wide.out.startswith("1 thread:")
    assert max(len(ln) for ln in wide.out.splitlines()) < 60 and "…" in wide.out


def test_it_is_far_shorter_than_digest(tmp_path: Path) -> None:
    rows = [
        (f"#c{n} > t", "Kim", f"message {m}", n == 0, False) for n in range(20) for m in range(8)
    ]
    seed_day(tmp_path, rows)
    brief = len(today(tmp_path).out.splitlines())
    full = len(run_cli(tmp_path, "--source", "demo", "digest", "--days", "1").out.splitlines())
    assert brief * 5 < full


def test_involving_me_on_plain_messages() -> None:
    m = Message("1", "#a > b", "X", NOW, "hi", mentioned=True)
    assert [t.state for t in involving_me([m])] == [AWAITING]


def test_a_quote_reply_shows_the_new_text_not_its_header(tmp_path: Path) -> None:
    reply = quote_reply(ZMESSAGE, "https://z.example.com", "on it, Thursday")
    only_quote = quote_reply(ZMESSAGE, "https://z.example.com", "")
    seed_day(
        tmp_path,
        [
            ("DM: Sam", "Sam", reply, False, False),
            ("DM: Kim", "Kim", only_quote, False, False),
        ],
    )
    out = today(tmp_path).out
    assert "on it, Thursday" in out and "(quote)" in out
    assert "said" not in out and "http" not in out and "```" not in out


def test_many_involved_threads_still_cost_a_fifth_of_digest(tmp_path: Path) -> None:
    rows = [
        (f"#c{n} > t", "Kim", f"message {m}", m == 0, False) for n in range(20) for m in range(30)
    ]
    seed_day(tmp_path, rows)
    out = today(tmp_path).out
    assert out.startswith("20 threads")
    full = run_cli(tmp_path, "--source", "demo", "digest", "--days", "1").out
    assert len(out.splitlines()) * 5 < len(full.splitlines())


def test_the_state_comes_from_the_newest_message_whatever_the_input_order() -> None:
    mine = Message("2", "#a > b", "Me", NOW, "done", mine=True)
    theirs = Message("1", "#a > b", "X", NOW - 60, "hi", mentioned=True)
    [thread] = involving_me([mine, theirs])  # newest first, as a source might return it
    assert [m.id for m in thread.messages] == ["1", "2"]
    assert thread.state == REPLIED


def test_one_thread_and_one_message_are_not_plural(tmp_path: Path) -> None:
    seed_day(tmp_path, [("DM: Sam", "Sam", "ping", False, False)])
    out = today(tmp_path).out
    assert out.splitlines()[0] == "1 thread: 1 awaiting you, 0 you replied last, 0 fyi"
    assert "1 msg\n" in out


def test_rows_are_dense_links_and_markup_out_short_names_in(tmp_path: Path) -> None:
    text = "**Fix** see [the PR](https://example.com/pr/1) or https://example.com/x `now`"
    seed_day(tmp_path, [("DM: Sam", "Sam Lee", text, False, False)])
    row = today(tmp_path).out.splitlines()[-1]
    assert row[2:7].count(":") == 1 and row[8:] == "sam Fix see the PR or (link) now"


def test_answered_threads_show_two_messages_awaiting_ones_three(tmp_path: Path) -> None:
    rows = [("DM: Sam", "Sam", f"s{n}", False, n == 4) for n in range(1, 5)]
    rows += [("DM: Kim", "Kim", f"k{n}", False, False) for n in range(1, 5)]
    seed_day(tmp_path, rows)
    out = today(tmp_path).out
    assert "k2" in out and "k1" not in out  # awaiting: last 3
    assert "s2" not in out  # you replied last: last 2


def test_an_ack_closing_my_reply_does_not_wait_on_me(tmp_path: Path) -> None:
    seed_day(tmp_path, [
        ("DM: Haris", "Haris", "does it work on another account?", False, False),
        ("DM: Haris", "Me", "only one account for now", False, True),
        ("DM: Haris", "Haris", "Ok", False, False),
        ("DM: Vee", "Vee", "can you check?", False, False),
        ("DM: Vee", "Me", "approved, should I merge?", False, True),
        ("DM: Vee", "Vee", "Thanks, 1 sec, checking FE PR", False, False),
        ("DM: Kai", "Kai", "thanks!", False, False),
        ("DM: Jo", "Jo", "PR is up", False, False),
        ("DM: Jo", "Me", "approved, should I merge?", False, True),
        ("DM: Jo", "Jo", "sure", False, False),
    ])  # fmt: skip
    heads = [ln for ln in today(tmp_path).out.splitlines() if "msg" in ln.split("  ")[-1]]
    tags = {h.split("  ")[1]: h.split("  ")[0] for h in heads}
    assert tags["DM: Haris"] == FYI
    assert tags["DM: Vee"] == AWAITING  # more than an ack
    assert tags["DM: Kai"] == AWAITING  # no reply of mine for it to close
    assert tags["DM: Jo"] == AWAITING  # "sure" answers my question: a go-ahead to act on


def test_what_counts_as_a_bare_ack() -> None:
    for text in ("Ok", "thanks 🙏", "👍🙏", "np :D", "thank you so much", "**ok**"):
        assert is_ack(text), text
    for text in ("ok?", "ok, but can you check", "sure, can you check X", "k8s is down", "no"):
        assert not is_ack(text), text
