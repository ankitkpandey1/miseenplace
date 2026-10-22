"""An agent pays for every character it reads: keep the triage loop small, and prove it."""

from pathlib import Path

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
