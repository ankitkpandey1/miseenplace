"""`stats`: reply latency, review turnaround, items closed, the daily CSV and the weekly delta."""

import json
import time
from pathlib import Path

import pytest

from miseenplace.chat import Message
from miseenplace.model import MiseError
from miseenplace.sources.zulip import Credentials, ZulipSource
from miseenplace.stats import (
    DAY,
    Data,
    Waits,
    compute,
    gh_queue,
    p90,
    parse_page,
    parse_queue,
    read_rows,
    record,
    span,
    waits,
)
from miseenplace.storage.local import LocalStorage
from tests.conftest import Result, run_cli
from tests.http_server import Seen, serve

NOW = time.time()
HOUR = 3600
NO_GH = {"PATH": ""}  # a machine without gh: the same condition a user without it has
TODAY = time.strftime("%Y-%m-%d")


def msg(id_: int, ts: float, where: str = "#eng > release", **over: object) -> Message:
    base: dict[str, object] = {"sender": "Alex", "text": "x", "mentioned": True, "mine": False}
    return Message(str(id_), where, ts=ts, **(base | over))  # type: ignore[arg-type]


def mine(id_: int, ts: float, where: str = "#eng > release") -> Message:
    return msg(id_, ts, where, sender="Me", mentioned=False, mine=True)


def seed(root: Path, rows: list[tuple[float, str, bool, bool]]) -> None:
    """rows: (seconds ago, where, mentioned, mine); the demo source serves them as chat."""
    messages = [
        {"id": n, "where": w, "sender": "Me" if mi else "Alex", "ts": NOW - ago,
         "text": "x", "mentioned": men, "mine": mi}
        for n, (ago, w, men, mi) in enumerate(rows, 1)
    ]  # fmt: skip
    (root / "demo").mkdir(parents=True, exist_ok=True)
    (root / "demo" / "inbox.json").write_text(
        json.dumps({"me": "Me", "people": [], "messages": messages})
    )


def stats(root: Path, *args: str, source: bool = True) -> Result:
    src = ("--source", "demo") if source else ()
    return run_cli(root, *src, "stats", *args, env=NO_GH)


def day(n: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(NOW - n * 86400))


def csv_lines(root: Path) -> list[str]:
    return (root / "stats.csv").read_text().splitlines()


# ---- reply latency ---------------------------------------------------------------


def test_latency_runs_from_the_first_unanswered_ping_to_my_next_message() -> None:
    got = waits(
        [
            msg(1, 1000), msg(2, 1100), mine(3, 1600), mine(9, 1700),  # two pings: waited 600
            msg(4, 2000, "DM: Sam", mentioned=False), mine(5, 2060, "DM: Sam"),  # a DM: 60
            msg(6, 3000, "#ops > chat", mentioned=False), mine(7, 3100, "#ops > chat"),  # no ping
            msg(8, 4000, "#web > bugs"),  # never answered
        ]
    )  # fmt: skip
    found = got.answered
    assert sorted(w for _, w in found) == [60, 600]
    assert {w: t for t, w in found}[600] == 1600  # counted when I replied
    assert got.unanswered == [4000]  # the ping nobody answered, by when it arrived


def test_p90_is_the_nearest_rank_and_empty_is_none() -> None:
    assert p90(list(map(float, range(1, 11)))) == 9.0
    assert p90([]) is None


def test_a_mention_and_my_reply_are_read_from_the_wire(tmp_path: Path) -> None:
    def z(id_: int, ago: int, mention: bool, email: str) -> dict[str, object]:
        return {
            "id": id_, "type": "stream", "timestamp": int(NOW) - ago, "subject": "release",
            "content": "hi", "sender_full_name": "X", "sender_email": email, "sender_id": id_,
            "display_recipient": "eng", "flags": ["mentioned"] if mention else [],
        }  # fmt: skip

    seen = Seen(
        messages=[z(1, 2000, True, "alex@example.com"), z(2, 1400, False, "me@example.com")]
    )
    with serve(seen) as base:
        src = ZulipSource(tmp_path, Credentials("me@example.com", base, "k"), frozenset())
        found = waits(src.digest(1, include_noise=True)).answered
    assert [w for _, w in found] == [600]


def test_span_reads_like_a_person_would_say_it() -> None:
    assert [span(s) for s in (30, 720, 3 * HOUR, 6000, 30 * HOUR, None)] == [
        "30s", "12m", "3h", "1h40m", "1d6h", "n/a",
    ]  # fmt: skip


# ---- the table -------------------------------------------------------------------


def test_the_table_shows_latency_and_says_n_a_where_it_has_no_data(tmp_path: Path) -> None:
    seed(tmp_path, [
        (5000, "#eng > a", True, False), (4400, "#eng > a", False, True),  # 600s
        (3000, "DM: Sam", False, False), (1200, "DM: Sam", False, True),  # 1800s
    ])  # fmt: skip
    out = stats(tmp_path, "--days", "7")
    assert out.code == 0
    assert "median 20m  p90 n/a  (2 answered, 0 unanswered)" in out.out
    assert "review turnaround  median n/a" in out.out
    assert "review queue    n/a now" in out.out
    assert "focus n/a  stress n/a" in out.out
    assert out.err.count("skipped review rows: gh is not installed") == 1  # one note


def test_no_chat_data_is_n_a_not_zero(tmp_path: Path) -> None:
    seed(tmp_path, [])
    out = stats(tmp_path)
    assert "median n/a  p90 n/a  (0 answered, 0 unanswered)" in out.out
    assert "pings answered/day  0.0" in out.out  # a real zero: the chat was read, nobody pinged


def test_without_a_source_the_chat_rows_are_n_a_with_one_note(tmp_path: Path) -> None:
    out = stats(tmp_path, source=False)
    assert out.code == 0
    assert "median n/a  p90 n/a" in out.out and "pings answered/day  n/a" in out.out
    assert "skipped reply rows:" in out.err


def test_items_closed_per_day_come_from_the_journal(tmp_path: Path) -> None:
    seed(tmp_path, [])
    for n in range(7):
        run_cli(tmp_path, "add", f"task {n}", "--no-sync")
    for n in (1, 2, 3, 4):
        run_cli(tmp_path, "done", str(n), "--no-sync")
    run_cli(tmp_path, "set", "5", "--state", "done", "--no-sync")
    run_cli(tmp_path, "done", "1", "--no-sync")  # closed again: still one item
    run_cli(tmp_path, "drop", "6", "--no-sync")  # dropped is not closed-as-done
    assert "closed/day      0.7" in stats(tmp_path, "--days", "7").out  # 5 / 7
    assert "closed/day      2.5" in stats(tmp_path, "--days", "2").out


# ---- the daily row ---------------------------------------------------------------


def test_record_writes_one_row_and_a_rerun_replaces_it(tmp_path: Path) -> None:
    first = stats(tmp_path, "--record", "--focus", "4", "--stress", "2")
    assert first.code == 0 and first.out == f"recorded {TODAY},4,2,\n"
    assert csv_lines(tmp_path) == ["date,focus,stress,queue", f"{TODAY},4,2,"]
    stats(tmp_path, "--record", "--focus", "3", "--stress", "5")
    assert csv_lines(tmp_path) == ["date,focus,stress,queue", f"{TODAY},3,5,"]


def test_a_rerun_without_ratings_keeps_the_ones_given(tmp_path: Path) -> None:
    stats(tmp_path, "--record", "--focus", "4", "--stress", "2")
    stats(tmp_path, "--record")
    stats(tmp_path, "--record", "--stress", "1")
    assert csv_lines(tmp_path)[1:] == [f"{TODAY},4,1,"]


def test_other_days_are_kept_in_date_order(tmp_path: Path) -> None:
    (tmp_path / "stats.csv").write_text(
        "date,focus,stress,queue\n2026-01-02,2,2,\n2026-01-01,1,1,\n"
    )
    stats(tmp_path, "--record", "--focus", "5")
    assert csv_lines(tmp_path)[1:] == ["2026-01-01,1,1,", "2026-01-02,2,2,", f"{TODAY},5,,"]


@pytest.mark.parametrize("value", ["0", "6", "x", "3.5"])
def test_a_rating_outside_1_to_5_is_refused(tmp_path: Path, value: str) -> None:
    out = stats(tmp_path, "--record", "--focus", value)
    assert out.code == 2 and not (tmp_path / "stats.csv").exists()


def test_ratings_without_record_and_record_with_delta_are_refused(tmp_path: Path) -> None:
    assert stats(tmp_path, "--focus", "3").code == 1
    assert stats(tmp_path, "--record", "--delta").code == 1


def test_focus_and_stress_in_the_table_are_the_means_of_the_days_in_range(tmp_path: Path) -> None:
    rows = [(day(0), 4, 2), (day(1), 2, 4), (day(3), 3, 3), (day(20), 1, 5)]
    (tmp_path / "stats.csv").write_text(
        "date,focus,stress,queue\n" + "".join(f"{d},{f},{s},\n" for d, f, s in rows)
    )
    seed(tmp_path, [])
    assert "focus 3.0  stress 3.0  (3 days logged)" in stats(tmp_path, "--days", "7").out


# ---- weekly delta ----------------------------------------------------------------


def test_delta_compares_the_last_7_days_with_the_14_before(tmp_path: Path) -> None:
    d = 86400
    seed(tmp_path, [
        (2 * d + 200, "#eng > a", True, False), (2 * d + 140, "#eng > a", False, True),  # 60s
        (9 * d + 700, "#eng > b", True, False), (9 * d + 100, "#eng > b", False, True),  # 600s
        (30 * d + 700, "#eng > c", True, False), (30 * d + 100, "#eng > c", False, True),  # old
    ])  # fmt: skip
    (tmp_path / "stats.csv").write_text(
        f"date,focus,stress,queue\n{day(1)},4,2,\n{day(10)},2,4,\n{day(40)},1,1,\n"
    )
    out = stats(tmp_path, "--delta").out.splitlines()
    assert out[0].startswith("last 7d vs previous 14d")
    assert out[1] == "reply median  1m vs 10m  -90%  n=1/1"
    assert out[2] == "review median  n/a vs n/a  n/a  n=0/0"
    assert out[5] == "focus  4.0 vs 2.0  +100%  n=1/1"
    assert out[6] == "stress  2.0 vs 4.0  -50%  n=1/1"


def test_delta_with_nothing_to_compare_says_n_a(tmp_path: Path) -> None:
    seed(tmp_path, [(100, "#eng > a", True, False), (50, "#eng > a", False, True)])
    out = stats(tmp_path, "--delta").out
    assert "reply median  50s vs n/a  n/a  n=1/0" in out


# ---- GitHub ----------------------------------------------------------------------


def page(prs: list[dict[str, object]], next_cursor: str | None = None) -> str:
    info = {"hasNextPage": next_cursor is not None, "endCursor": next_cursor}
    reviewed = {"nodes": prs, "pageInfo": info}
    return json.dumps({"data": {"viewer": {"login": "me"}, "reviewed": reviewed}})


def pr(asked: list[tuple[str, str]], reviews: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "requests": {
            "nodes": [{"createdAt": t, "requestedReviewer": {"login": w}} for t, w in asked]
        },
        "reviews": {"nodes": [{"author": {"login": a}, "submittedAt": t} for t, a in reviews]},
    }  # fmt: skip


def test_turnaround_is_request_to_my_first_review() -> None:
    got = parse_page(page([
        pr([("2026-10-20T10:00:00Z", "me")],
           [("2026-10-20T10:30:00Z", "kim"), ("2026-10-20T13:00:00Z", "me"),
            ("2026-10-21T09:00:00Z", "me")]),  # my first review counts, kim's does not
        pr([("2026-10-20T10:00:00Z", "kim")], [("2026-10-20T11:00:00Z", "me")]),  # not asked of me
        pr([("2026-10-20T10:00:00Z", "me")], [("2026-10-20T11:00:00Z", "kim")]),  # I never reviewed
        pr([("2026-10-20T10:00:00Z", "me")], [("2026-10-20T09:00:00Z", "me")]),  # reviewed before
    ]))  # fmt: skip
    assert [w for _, w in got.turnarounds] == [3 * HOUR]
    assert got.cursor is None


def test_a_team_only_request_is_skipped() -> None:
    team = {"createdAt": "2026-10-20T10:00:00Z", "requestedReviewer": None}
    one = pr([], [("2026-10-20T11:00:00Z", "me")])
    one["requests"] = {"nodes": [team]}
    assert parse_page(page([one])).turnarounds == []


def test_two_pages_are_read_through_the_cursor() -> None:
    first = parse_page(
        page([pr([("2026-10-20T10:00:00Z", "me")], [("2026-10-20T11:00:00Z", "me")])], "C1")
    )
    last = parse_page(
        page([pr([("2026-10-21T10:00:00Z", "me")], [("2026-10-21T12:00:00Z", "me")])])
    )
    assert first.cursor == "C1" and last.cursor is None
    assert [w for _, w in first.turnarounds + last.turnarounds] == [HOUR, 2 * HOUR]


def test_the_queue_is_read() -> None:
    assert parse_queue('{"data": {"queue": {"issueCount": 3}}}') == 3


def test_unreadable_gh_output_is_an_error() -> None:
    for raw in ("not json", "{}", '{"data": {"queue": {}}}'):
        with pytest.raises(MiseError, match="unreadable"):
            parse_queue(raw)
    with pytest.raises(MiseError, match="unreadable"):
        parse_page("not json")


def test_a_partial_answer_with_errors_is_not_a_zero() -> None:
    raw = json.dumps({"data": {"queue": None}, "errors": [{"message": "search timed out"}]})
    with pytest.raises(MiseError, match="search timed out"):
        parse_queue(raw)


def test_gh_queue_without_gh_is_the_skip_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(MiseError, match="gh is not installed"):
        gh_queue()


# ---- garbled CSV and the queue rule ----------------------------------------------


def read(tmp_path: Path, text: str) -> tuple[list[dict[str, str]], list[str]]:
    (tmp_path / "stats.csv").write_text(text)
    notes: list[str] = []
    return read_rows(LocalStorage(tmp_path), notes), notes


def test_bad_rows_are_skipped_with_a_note_naming_the_line(tmp_path: Path) -> None:
    rows, notes = read(tmp_path, (
        "date,focus,stress,queue\n2026-01-01,4,2,\n2026-01-02,x,2,\n2026-01-03\n"
        "garbage,1,1,\n2026-01-05,1,1,abc\n2026-01-06,5,5,0\n"
    ))  # fmt: skip
    assert [r["date"] for r in rows] == ["2026-01-01", "2026-01-06"]
    assert notes[1] == "stats.csv:4: skipped, too few fields"
    assert [n.split(":")[1] for n in notes] == ["3", "4", "5", "6"]


def test_a_header_without_date_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "stats.csv").write_text("day,focus,stress,queue\n2026-01-01,4,2,\n")
    with pytest.raises(MiseError, match="header"):
        read_rows(LocalStorage(tmp_path), [])


def test_extra_columns_and_blank_file_are_tolerated(tmp_path: Path) -> None:
    rows, notes = read(tmp_path, "date,focus,stress,queue,mood\n2026-01-01,4,2,,ok\n")
    assert rows == [{"date": "2026-01-01", "focus": "4", "stress": "2", "queue": ""}] and not notes
    assert read(tmp_path, "")[0] == []
    record(LocalStorage(tmp_path), "2026-01-01", (None, None), None, [])  # rewrite drops "mood"
    assert (tmp_path / "stats.csv").read_text().splitlines()[0] == "date,focus,stress,queue"


def test_a_lone_zero_does_not_overwrite_a_real_queue(tmp_path: Path) -> None:
    st = LocalStorage(tmp_path)
    record(st, "2026-01-01", (None, None), 7, [])
    record(st, "2026-01-01", (None, None), 0, [])  # a hiccup: keep 7
    assert read_rows(st, [])[0]["queue"] == "7"
    record(st, "2026-01-01", (None, None), 2, [])  # a real change wins
    assert read_rows(st, [])[0]["queue"] == "2"
    record(st, "2026-01-02", (None, None), 0, [])  # no earlier number: 0 is recorded
    assert read_rows(st, [])[1]["queue"] == "0"


def test_p90_needs_ten_replies_and_delta_refuses_days(tmp_path: Path) -> None:
    rows = [(10000 - 100 * n, "#eng > a", True, False) for n in range(10)]
    rows += [(10000 - 100 * n - 50, "#eng > a", False, True) for n in range(10)]
    seed(tmp_path, [r for pair in zip(rows[:10], rows[10:], strict=True) for r in pair])
    assert "p90 50s  (10 answered" in stats(tmp_path).out
    assert stats(tmp_path, "--delta", "--days", "7").code == 1


def test_a_window_holds_the_days_after_its_start_and_up_to_its_end() -> None:
    end = 100 * DAY
    answered = [(end - 7 * DAY, 10.0), (end - 7 * DAY + 1, 20.0), (end, 30.0)]
    data = Data(Waits(answered, []), None, [], [])
    assert compute(data, end - 7 * DAY, end).replied == 2  # the start is the previous window's
    assert compute(data, end - 21 * DAY, end - 7 * DAY).replied == 1


def test_an_empty_first_page_is_marked_so_it_can_be_asked_again() -> None:
    assert parse_page(page([])).matched == 0
    assert parse_page(page([pr([], [])])).matched == 1
