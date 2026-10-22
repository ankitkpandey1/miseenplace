"""An agent pays for every character it reads: keep the triage loop small, and prove it."""

from pathlib import Path

from miseenplace.sources.demo import DemoSource
from miseenplace.tour import seed
from tests.conftest import run_cli

INBOX_BUDGET = 700  # chars for the whole demo inbox; the raw digest is several times larger
LOOP_BUDGET = 2000  # chars (~500 tokens) for inbox + 3 triage calls


def test_demo_inbox_fits_the_budget(tmp_path: Path) -> None:
    seed(tmp_path)
    out = run_cli(tmp_path, "--source", "demo", "inbox", "--cursor", "demo").out
    assert 0 < len(out) <= INBOX_BUDGET
    assert all(len(line) <= 240 for line in out.splitlines())


def test_whole_triage_loop_fits_the_budget(tmp_path: Path) -> None:
    seed(tmp_path)
    total = run_cli(tmp_path, "--source", "demo", "inbox", "--cursor", "demo").out
    for args in (("3", "--todo"), ("4", "--waiting"), ("5", "--ignore")):
        total += run_cli(tmp_path, "--source", "demo", "triage", *args).out
    assert len(total) <= LOOP_BUDGET


def test_empty_inbox_prints_nothing(tmp_path: Path) -> None:
    seed(tmp_path)
    run_cli(tmp_path, "--source", "demo", "inbox", "--cursor", "demo")
    for item in ("3", "4", "5"):
        run_cli(tmp_path, "--source", "demo", "triage", item, "--ignore")
    assert run_cli(tmp_path, "--source", "demo", "inbox", "--cursor", "demo").out == ""


TODAY_BUDGET = 490  # chars for `today` on the demo day (405 measured, about 20% headroom)


def test_demo_today_fits_the_budget(tmp_path: Path) -> None:
    seed(tmp_path)
    out = run_cli(tmp_path, "--source", "demo", "today").out
    assert 0 < len(out) <= TODAY_BUDGET
    assert "http" not in out and "**" not in out and "\n\n" not in out


def test_an_empty_day_is_one_line(tmp_path: Path) -> None:
    seed(tmp_path)
    assert run_cli(tmp_path, "--source", "demo", "today", "--days", "0.001").out == (
        "0 threads today\n"
    )


def reconcile_day(root: Path) -> str:
    seed(root)
    DemoSource(root).append("DM: Rowan Hale", "Jordan Rivers", "on it", mine=True)
    for title, ref in (("review parser", 3), ("changelog", 1)):
        run_cli(
            root,
            "--source",
            "demo",
            "add",
            title,
            "--ref",
            f"https://z.example.com/#narrow/id/{ref}",
            "--no-sync",
        )
    return run_cli(root, "--source", "demo", "reconcile").out


RECONCILE_BUDGET = 145  # chars for `reconcile` on the demo day (121 measured, about 20% headroom)


def test_demo_reconcile_fits_the_budget(tmp_path: Path) -> None:
    out = reconcile_day(tmp_path)
    assert 0 < len(out) <= RECONCILE_BUDGET
    assert all(len(line) <= 90 for line in out.splitlines())


LESSONS_BUDGET = 330  # chars for `lessons list` with three lessons (about 250 measured)


def test_lessons_list_fits_the_budget(tmp_path: Path) -> None:
    for n in range(3):
        run_cli(
            tmp_path, "lessons", "add", "--when", f"writing chat message {n}",
            "--do", "link PRs as full URLs", "--not", "a bare #42",
            "--evidence", "a long quote of the correction " * 20, "--no-sync",
        )  # fmt: skip
    out = run_cli(tmp_path, "lessons", "list").out
    assert 0 < len(out) <= LESSONS_BUDGET
    assert out.count("\n") == 3 and "evidence" not in out
    assert all(len(line) <= 120 for line in out.splitlines())


def stats_day(root: Path, *args: str) -> str:
    seed(root)
    DemoSource(root).append("DM: Rowan Hale", "Jordan Rivers", "on it", mine=True)
    run_cli(root, "stats", "--record", "--focus", "4", "--stress", "2", env={"PATH": ""})
    return run_cli(root, "--source", "demo", "stats", *args, env={"PATH": ""}).out


STATS_BUDGET = 250  # chars for `stats` on the demo day (209 measured)
DELTA_BUDGET = 335  # chars for `stats --delta` (277 measured, about 20% headroom)


def test_stats_fits_the_budget(tmp_path: Path) -> None:
    out = stats_day(tmp_path)
    assert 0 < len(out) <= STATS_BUDGET
    assert all(len(line) <= 80 for line in out.splitlines())


def test_stats_delta_fits_the_budget(tmp_path: Path) -> None:
    out = stats_day(tmp_path, "--delta")
    assert 0 < len(out) <= DELTA_BUDGET
    assert all(len(line) <= 90 for line in out.splitlines())
