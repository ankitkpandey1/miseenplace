"""The user's own words as data: request log, style file, sent-message corpus."""

import json
from pathlib import Path

import pytest

from miseenplace.tour import seed
from tests.conftest import Result, run_cli


@pytest.fixture
def root(tmp_path: Path) -> Path:
    seed(tmp_path)
    return tmp_path


def mep(root: Path, *args: str) -> Result:
    return run_cli(root, "--source", "demo", *args)


def test_requests_are_kept_verbatim_with_source_and_time(tmp_path: Path) -> None:
    words = 'Move the retro to "fri", and tell Sam -- ASAP!'
    assert run_cli(tmp_path, "note-request", words).code == 0
    run_cli(tmp_path, "note-request", "second")
    rows = [json.loads(x) for x in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert [r["text"] for r in rows] == [words, "second"]
    assert all(r["ts"] for r in rows)
    shown = run_cli(tmp_path, "requests").out
    assert words in shown and shown.index(words) < shown.index("second")


def test_requests_n_limits_to_the_newest(tmp_path: Path) -> None:
    for word in ("one", "two", "three"):
        run_cli(tmp_path, "note-request", word)
    out = run_cli(tmp_path, "requests", "-n", "2").out
    assert "one" not in out and "two" in out and "three" in out


def test_style_show_without_a_file_says_how_to_make_one(root: Path) -> None:
    r = mep(root, "style", "show")
    assert "learn-style" in r.out


def test_style_set_then_show_round_trips(root: Path) -> None:
    mep(root, "style", "set", "short and lowercase")
    assert mep(root, "style", "show").out.strip() == "short and lowercase"
    assert (root / "style" / "demo.md").read_text().strip() == "short and lowercase"


def test_style_learn_archives_only_my_messages_and_dedups(root: Path) -> None:
    first = mep(root, "style", "learn", "--days", "30")
    assert first.code == 0
    corpus = root / "corpus" / "demo" / "sent.jsonl"
    rows = [json.loads(x) for x in corpus.read_text().splitlines()]
    assert rows
    assert not any("Who owns the changelog" in r["text"] for r in rows)  # others' words stay out
    again = mep(root, "style", "learn", "--days", "30")
    assert again.code == 0
    assert len(corpus.read_text().splitlines()) == len(rows)  # nothing duplicated
    assert "+0" in again.out


def test_style_sample_prints_the_archive(root: Path) -> None:
    mep(root, "style", "learn", "--days", "30")
    assert "I can take the changelog" in mep(root, "style", "sample").out
