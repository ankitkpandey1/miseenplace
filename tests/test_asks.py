"""Asks: the queue, the ranked capped digest, answering, and the rates the EOD review reports."""

import json
import time
from pathlib import Path

from tests.conftest import Result, run_cli


def ask(root: Path, decision: str, *extra: str) -> Result:
    return run_cli(
        root, "ask", decision, "--recommend", "yes", "--default", "hold",
        "--confidence", "read", "--no-sync", *extra,
    )  # fmt: skip


def asks(root: Path, *args: str) -> str:
    r = run_cli(root, "asks", *args)
    assert r.code == 0, r.err
    return r.out.strip()


def test_an_ask_is_queued_and_shown_decision_first(tmp_path: Path) -> None:
    assert ask(tmp_path, "Reply to alex?", "--by", "23:59").out == "A1\n"
    assert asks(tmp_path).splitlines() == [
        "1 decision (~1 min)",
        "A1. Reply to alex?  Recommended: yes.  Default if no answer by 23:59: hold.  [read]",
    ]


def test_blocking_and_destructive_come_first_then_the_earliest_deadline(tmp_path: Path) -> None:
    ask(tmp_path, "late", "--by", "18:00")
    ask(tmp_path, "none")
    ask(tmp_path, "early", "--by", "09:30")
    ask(tmp_path, "wipe", "--destructive")
    ask(tmp_path, "unblock", "--blocks-someone", "--by", "20:00")
    body = asks(tmp_path).splitlines()[1:]
    assert [x.split("  ")[0] for x in body] == [
        "A5. BLOCKS unblock", "A4. DESTRUCTIVE wipe", "A3. early", "A1. late", "A2. none",
    ]  # fmt: skip


def test_the_digest_stops_at_seven_asks_and_says_how_many_it_left_out(tmp_path: Path) -> None:
    for n in range(10):
        ask(tmp_path, f"question {n}")
    lines = asks(tmp_path).splitlines()
    assert lines[0].startswith("10 decisions") and len(lines) == 1 + 7 + 1
    assert lines[-1] == "+3 more"


def test_the_digest_stops_at_the_character_cap(tmp_path: Path) -> None:
    for n in range(5):
        text = "x" * 190
        run_cli(tmp_path, "ask", f"{n}{text}", "--recommend", text, "--default", text,
                "--confidence", "read", "--no-sync")  # fmt: skip
    lines = asks(tmp_path, "--width", "700").splitlines()
    assert lines[-1].startswith("+") and 1 < len(lines) - 2 < 5
    assert sum(len(x) for x in lines[1:-1]) <= 1800


def test_the_digest_ends_with_where_you_were(tmp_path: Path) -> None:
    ask(tmp_path, "q")
    assert run_cli(tmp_path, "ask", "--where", "date PR, 2 of 5 files", "--no-sync").code == 0
    assert asks(tmp_path).splitlines()[-1] == "Where you were: date PR, 2 of 5 files"
    assert asks(tmp_path, "--width", "20").splitlines()[-1] == "Where you were: date"


def test_an_empty_queue_is_one_line(tmp_path: Path) -> None:
    assert asks(tmp_path) == "0 decisions"


def test_a_bad_ask_is_refused(tmp_path: Path) -> None:
    assert "HH:MM" in ask(tmp_path, "q", "--by", "5pm").err
    assert ask(tmp_path, "q", "--by", "25:00").code == 1
    assert "non-empty decision" in ask(tmp_path, " ").err
    assert run_cli(tmp_path, "ask", "q", "--no-sync").code == 1
    assert ask(tmp_path, "q", "--destructive", "--blocks-someone").code == 2
    assert run_cli(tmp_path, "ask", "q", "--where", "x", "--no-sync").code == 1
    assert asks(tmp_path) == "0 decisions"


def test_answering_closes_the_ask_once(tmp_path: Path) -> None:
    ask(tmp_path, "one")
    ask(tmp_path, "two")
    assert asks(tmp_path, "answer", "1", "accepted", "--no-sync") == "A1 accepted"
    assert asks(tmp_path).splitlines()[1].startswith("A2.")
    again = run_cli(tmp_path, "asks", "answer", "1", "rejected", "--no-sync")
    assert again.code == 1 and "already accepted" in again.err
    assert run_cli(tmp_path, "asks", "answer", "9", "edited", "--no-sync").code == 1


def test_a_garbled_queue_line_names_the_line(tmp_path: Path) -> None:
    (tmp_path / "asks.jsonl").write_text("not json\n")
    r = run_cli(tmp_path, "asks")
    assert r.code == 1 and "asks.jsonl line 1" in r.err


def test_parallel_asks_all_land(tmp_path: Path) -> None:
    import subprocess
    import sys

    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "miseenplace",
                "--dir",
                str(tmp_path),
                "ask",
                f"q{n}",
                "--recommend",
                "a",
                "--default",
                "b",
                "--confidence",
                "read",
                "--no-sync",
            ],
            stdout=subprocess.DEVNULL,
        )
        for n in range(6)
    ]
    assert [p.wait() for p in procs] == [0] * 6
    rows = (tmp_path / "asks.jsonl").read_text().splitlines()
    assert sorted(json.loads(r)["id"] for r in rows) == [1, 2, 3, 4, 5, 6]


def test_long_fields_are_refused_and_every_line_fits_the_width(tmp_path: Path) -> None:
    huge = run_cli(tmp_path, "ask", "q", "--recommend", "r" * 201, "--default", "d",
                   "--confidence", "read", "--no-sync")  # fmt: skip
    assert huge.code == 1 and "201 characters" in huge.err
    assert ask(tmp_path, "d" * 200, "--blocks-someone").code == 0
    run_cli(tmp_path, "ask", "q" * 200, "--recommend", "r" * 200, "--default", "d" * 200,
            "--confidence", "read", "--no-sync")  # fmt: skip
    for width in ("160", "60"):
        lines = asks(tmp_path, "--width", width).splitlines()
        assert all(len(x) <= int(width) for x in lines[1:]), width


def put(root: Path, *rows: tuple[str, str]) -> None:
    (root / "asks.jsonl").write_text(
        "".join(
            json.dumps({
                "id": n, "decision": name, "recommend": "r", "default": "x", "by": by,
                "confidence": "read", "flag": "", "kind": "", "created": "2026-01-01 09:00",
                "status": "open", "answered": "",
            }) + "\n"
            for n, (name, by) in enumerate(rows, 1)
        )
    )  # fmt: skip


def test_deadlines_sort_across_days_and_a_passed_one_is_overdue(tmp_path: Path) -> None:
    today = day(0)
    put(tmp_path, ("later", "2999-01-01 09:00"), ("tonight", f"{today} 23:59"),
        ("stale", "2000-01-01 08:00"))  # fmt: skip
    lines = asks(tmp_path).splitlines()[1:]
    assert [x.split()[1] for x in lines] == ["stale", "tonight", "later"]
    assert "by 01-01 08:00 (overdue)" in lines[0]
    assert "by 23:59:" in lines[1]
    assert "(overdue)" not in lines[1] + lines[2] and "by 01-01 09:00" in lines[2]


def test_the_window_is_the_last_n_calendar_days_including_today(tmp_path: Path) -> None:
    write_answered(tmp_path, [("k", "accepted", 6), ("k", "accepted", 7)])
    assert "  k  1/1 unchanged  100%" in asks(tmp_path, "report")  # day 6 in, day 7 out
    write_answered(tmp_path, [("k", "accepted", 1), ("k", "accepted", 0)])
    assert "  k  1/1" in asks(tmp_path, "report", "--days", "1")


def test_small_samples_are_marked_and_five_are_not(tmp_path: Path) -> None:
    write_answered(tmp_path, [("small", "accepted", 1)] * 4 + [("big", "accepted", 1)] * 5)
    out = asks(tmp_path, "report")
    assert "  small  4/4 unchanged  100% (n<5)" in out
    assert "  big  5/5 unchanged  100%" in out and "big  5/5 unchanged  100% (n<5)" not in out


def day(offset: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - offset * 86400))


def write_answered(root: Path, rows: list[tuple[str, str, int]]) -> None:
    lines = [
        json.dumps({
            "id": n, "decision": "d", "recommend": "r", "default": "x", "by": "",
            "confidence": "read", "flag": "", "kind": kind, "created": "2026-01-01 09:00",
            "status": status, "answered": day(age),
        })
        for n, (kind, status, age) in enumerate(rows, 1)
    ]  # fmt: skip
    (root / "asks.jsonl").write_text("\n".join(lines) + "\n")


def test_acceptance_is_per_kind_over_the_last_seven_days(tmp_path: Path) -> None:
    write_answered(
        tmp_path,
        [("reply", "accepted", 1), ("reply", "accepted", 2), ("reply", "edited", 3),
         ("reply", "rejected", 20), ("review", "rejected", 1), ("", "accepted", 1)],
    )  # fmt: skip
    out = asks(tmp_path, "report")
    assert "  reply  2/3 unchanged  67%" in out
    assert "  review  0/1 unchanged  0%" in out
    assert out.count("unchanged") == 2  # the old answer and the kindless one do not count


def test_report_says_na_without_data(tmp_path: Path) -> None:
    out = asks(tmp_path, "report")
    assert out.count("n/a") == 2


def test_top_three_routine_types_by_minutes(tmp_path: Path) -> None:
    for tag, spent in (("pr-review", "1h"), ("pr-review", "30m"), ("status", "20m"),
                       ("reply", "45m"), ("triage", "5m")):  # fmt: skip
        r = run_cli(tmp_path, "log", "did it", "--tag", f"routine:{tag}", "--spent", spent,
                    "--no-sync")  # fmt: skip
        assert r.code == 0, r.err
    run_cli(tmp_path, "log", "untagged", "--spent", "3h", "--no-sync")
    lines = asks(tmp_path, "report").splitlines()
    assert lines[1:4] == ["  pr-review  90m", "  reply  45m", "  status  20m"]
    assert lines[4] == "draft acceptance, last 7d"  # triage, the fourth, is left out


def test_old_routine_minutes_do_not_count(tmp_path: Path) -> None:
    run_cli(tmp_path, "log", "new", "--tag", "routine:fresh", "--spent", "10m", "--no-sync")
    log = tmp_path / "data" / "audit_log.jsonl"
    first = json.loads(log.read_text().splitlines()[0])
    old = dict(first, seq=first["seq"] + 1, ts="2020-01-01 09:00")
    old["changes"] = dict(first["changes"], tags=["routine:ancient"], spent=500)
    log.write_text(log.read_text() + json.dumps(old) + "\n")
    out = asks(tmp_path, "report")
    assert "fresh  10m" in out and "ancient" not in out
