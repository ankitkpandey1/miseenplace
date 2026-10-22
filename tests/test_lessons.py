"""Lessons: votes, deletion at zero, and the weekly replay that reverts a change for the worse."""

from pathlib import Path

from tests.conftest import run_cli

BARE = r"(?<![/\w])#\d+\b"


def lessons(root: Path, *args: str) -> str:
    r = run_cli(root, "lessons", *args)
    assert r.code == 0, r.err
    return r.out.strip()


def add(root: Path, when: str = "writing a chat message", pattern: str = BARE) -> str:
    return lessons(
        root, "add", "--when", when, "--do", "link PRs as full URLs", "--not", "bare #42",
        "--evidence", "2026-10-22 correction", "--pattern", pattern, "--no-sync",
    )  # fmt: skip


def case(root: Path, wrong: str, fine: str) -> None:
    lessons(root, "case", "--wrong", wrong, "--fine", fine, "--no-sync")


def test_a_lesson_is_written_as_trigger_action_not_wrong_action(tmp_path: Path) -> None:
    assert add(tmp_path) == "L1 v2"
    assert lessons(tmp_path, "list") == (
        "L1 v2 when writing a chat message -> do link PRs as full URLs, not bare #42"
    )
    assert "evidence: 2026-10-22 correction" in lessons(tmp_path, "list", "--full")


def test_empty_lesson_fields_and_bad_patterns_are_refused(tmp_path: Path) -> None:
    assert lessons(tmp_path, "list") == "0 lessons"
    bad = run_cli(tmp_path, "lessons", "add", "--when", " ", "--do", "x", "--not", "y",
                  "--evidence", "e", "--no-sync")  # fmt: skip
    assert bad.code == 1 and "none empty" in bad.err
    broken = run_cli(tmp_path, "lessons", "add", "--when", "w", "--do", "x", "--not", "y",
                     "--evidence", "e", "--pattern", "(", "--no-sync")  # fmt: skip
    assert broken.code == 1 and "regex" in broken.err
    assert lessons(tmp_path, "list") == "0 lessons"


def test_votes_move_by_one_and_a_lesson_at_zero_is_deleted(tmp_path: Path) -> None:
    add(tmp_path)
    assert lessons(tmp_path, "vote", "1", "up", "--no-sync") == "L1 v3"
    assert lessons(tmp_path, "vote", "1", "down", "--no-sync") == "L1 v2"
    assert lessons(tmp_path, "vote", "1", "down", "--no-sync") == "L1 v1"
    assert lessons(tmp_path, "vote", "1", "down", "--no-sync") == "L1 deleted"
    assert lessons(tmp_path, "list") == "0 lessons"
    assert run_cli(tmp_path, "lessons", "vote", "1", "up", "--no-sync").code == 1


def test_check_counts_a_correction_only_if_wrong_is_caught_and_fine_is_not(tmp_path: Path) -> None:
    add(tmp_path)
    case(tmp_path, "see #42 for details", "see https://github.com/acme/app/pull/42")
    case(tmp_path, "thanks", "thanks")
    assert (
        lessons(tmp_path, "check", "--no-sync") == "ok: 1/2 corrections caught, 0 lessons unchecked"
    )


def test_a_lesson_change_that_makes_the_replay_worse_is_reverted(tmp_path: Path) -> None:
    add(tmp_path)
    case(tmp_path, "see #42 for details", "see https://github.com/acme/app/pull/42")
    assert lessons(tmp_path, "check", "--no-sync").startswith("ok: 1/1")
    add(tmp_path, when="any message", pattern=r"https")  # flags the wanted text too
    out = lessons(tmp_path, "check", "--no-sync")
    assert out == "reverted: 0/1 < 1/1; removed L2"
    assert lessons(tmp_path, "list").count("\n") == 0 and "L2" not in lessons(tmp_path, "list")


def test_a_change_that_helps_is_kept_and_becomes_the_new_baseline(tmp_path: Path) -> None:
    add(tmp_path)
    case(tmp_path, "see #42", "see https://x.example/pull/42")
    lessons(tmp_path, "check", "--no-sync")
    case(tmp_path, "ship it today!!!", "ship it today")
    add(tmp_path, when="tone", pattern=r"!!+")
    assert lessons(tmp_path, "check", "--no-sync").startswith("ok: 2/2")
    assert "L2" in lessons(tmp_path, "list")


def test_lessons_without_a_pattern_are_counted_as_unchecked(tmp_path: Path) -> None:
    add(tmp_path, pattern="")
    case(tmp_path, "see #42", "see a URL")
    assert (
        lessons(tmp_path, "check", "--no-sync") == "ok: 0/1 corrections caught, 1 lessons unchecked"
    )


def test_the_first_worsening_change_is_reverted_because_a_baseline_exists(tmp_path: Path) -> None:
    case(tmp_path, "see #42", "see https://x.example/pull/42")
    add(tmp_path)  # no `check` has ever run
    assert lessons(tmp_path, "check", "--no-sync").startswith("ok: 1/1")
    add(tmp_path, when="any", pattern=r"https")
    assert lessons(tmp_path, "check", "--no-sync").startswith("reverted")


def test_a_baseline_is_saved_before_the_first_change(tmp_path: Path) -> None:
    case(tmp_path, "see #42", "see a URL")
    add(tmp_path, pattern=r"zzz")
    add(tmp_path, when="w2")
    good = next(tmp_path.rglob("lessons.good.jsonl"))
    assert good.read_text() == ""  # the set as it was before the first add


def test_a_revert_names_the_lessons_it_undoes(tmp_path: Path) -> None:
    add(tmp_path)
    case(tmp_path, "see #42", "see https://x.example/pull/42")
    lessons(tmp_path, "check", "--no-sync")
    lessons(tmp_path, "vote", "1", "up", "--no-sync")
    add(tmp_path, when="any", pattern=r"https")
    out = lessons(tmp_path, "check", "--no-sync")
    assert out == "reverted: 0/1 < 1/1; removed L2; changed L1"


def test_a_revert_names_a_restored_lesson(tmp_path: Path) -> None:
    add(tmp_path)
    case(tmp_path, "see #42", "see a URL")
    lessons(tmp_path, "check", "--no-sync")
    lessons(tmp_path, "vote", "1", "down", "--no-sync")
    lessons(tmp_path, "vote", "1", "down", "--no-sync")
    assert lessons(tmp_path, "check", "--no-sync") == "reverted: 0/1 < 1/1; restored L1"
    assert lessons(tmp_path, "list").startswith("L1 v2")


def test_a_pattern_that_matches_the_empty_string_is_refused(tmp_path: Path) -> None:
    for pattern in (".*", "^", "a?"):
        r = run_cli(tmp_path, "lessons", "add", "--when", "w", "--do", "x", "--not", "y",
                    "--evidence", "e", "--pattern", pattern, "--no-sync")  # fmt: skip
        assert r.code == 1 and "empty string" in r.err
    assert lessons(tmp_path, "list") == "0 lessons"


def test_writes_one_after_another_all_land_and_leave_the_lock_file(tmp_path: Path) -> None:
    for n in range(3):
        add(tmp_path, when=f"w{n}")
    lessons(tmp_path, "vote", "2", "up", "--no-sync")
    assert lessons(tmp_path, "list").count("\n") == 2
    assert "L2 v3" in lessons(tmp_path, "list")
    assert (tmp_path / ".lock").exists()


def test_a_write_waits_for_a_held_lock(tmp_path: Path) -> None:
    import subprocess
    import sys
    import time

    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time,pathlib\nfrom miseenplace import lock\n"
         "with lock.exclusive(pathlib.Path(sys.argv[1])):\n"
         " print('held',flush=True); time.sleep(1.5)",
         str(tmp_path)],
        stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
    started = time.monotonic()
    add(tmp_path)
    assert time.monotonic() - started >= 1.0
    holder.wait()


def test_a_garbled_line_names_the_file_and_line(tmp_path: Path) -> None:
    add(tmp_path)
    path = next(tmp_path.rglob("lessons.jsonl"))
    path.write_text(path.read_text() + "{not json\n")
    r = run_cli(tmp_path, "lessons", "list")
    assert r.code == 1 and "lessons.jsonl line 2" in r.err and "Traceback" not in r.err
    path.write_text('{"id": 1}\n')
    assert "line 1" in run_cli(tmp_path, "lessons", "list").err
    case(tmp_path, "a", "b")
    cases = next(tmp_path.rglob("corrections.jsonl"))
    cases.write_text('{"wrong": "a"}\n')
    assert "corrections.jsonl line 1" in run_cli(tmp_path, "lessons", "check", "--no-sync").err


def test_empty_files_read_as_nothing(tmp_path: Path) -> None:
    add(tmp_path)
    next(tmp_path.rglob("lessons.jsonl")).write_text("")
    assert lessons(tmp_path, "list") == "0 lessons"
    assert (
        lessons(tmp_path, "check", "--no-sync") == "ok: 0/0 corrections caught, 0 lessons unchecked"
    )


def test_check_leaves_the_good_file_alone_when_nothing_changed(tmp_path: Path) -> None:
    add(tmp_path)
    case(tmp_path, "see #42", "see a URL")
    lessons(tmp_path, "check", "--no-sync")
    good = next(tmp_path.rglob("lessons.good.jsonl"))
    before = good.stat().st_mtime_ns
    lessons(tmp_path, "check", "--no-sync")
    assert good.stat().st_mtime_ns == before


def test_width_must_be_positive_and_a_not_value_may_start_with_a_dash(tmp_path: Path) -> None:
    add(tmp_path)
    assert run_cli(tmp_path, "lessons", "list", "--width", "0").code == 2
    out = lessons(
        tmp_path, "add", "--when", "w", "--do", "x", "--not=-x", "--evidence", "e", "--no-sync"
    )
    assert out == "L2 v2" and "not -x" in lessons(tmp_path, "list")
