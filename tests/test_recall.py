"""Recall: BM25 ranking with fuzzy terms over current lessons, facts and approved recipes."""

import json
from pathlib import Path

from tests.conftest import run_cli


def ok(root: Path, *args: str) -> str:
    r = run_cli(root, *args)
    assert r.code == 0, r.err
    return r.out.strip()


def recall(root: Path, *args: str) -> list[str]:
    return ok(root, "recall", *args).splitlines()


def approved(root: Path, name: str, desc: str, *tags: str) -> None:
    flags = [x for t in tags for x in ("--tag", t)]
    ok(root, "recipe", "add", name, "--desc", desc, "--step", "do it", *flags, "--no-sync")
    ok(root, "recipe", "approve", name, "--no-sync")


def test_key_and_tag_matches_rank_above_a_body_match(tmp_path: Path) -> None:
    ok(tmp_path, "fact", "add", "koyal-apiv2-port", "8260", "--source", "user", "--no-sync")
    ok(tmp_path, "fact", "add", "notes", "koyal runs here", "--no-sync")
    approved(tmp_path, "stop-koyal", "turn a dev box off", "koyal")
    lines = recall(tmp_path, "koyal")
    assert lines[0].startswith("fact koyal-apiv2-port: 8260 (user, verified 0d)")
    assert lines[1].startswith("recipe stop-koyal: turn a dev box off")
    assert lines[2].startswith("fact notes:")
    assert lines[-1] == "3 of 3 items, " + str(sum(len(x) + 1 for x in lines[:-1])) + " chars"


def test_numbers_and_ids_are_kept_as_tokens(tmp_path: Path) -> None:
    ok(tmp_path, "fact", "add", "koyal-apiv2-port", "8260", "--no-sync")
    ok(tmp_path, "fact", "add", "other", "9999", "--no-sync")
    assert recall(tmp_path, "8260")[0].startswith("fact koyal-apiv2-port")
    assert recall(tmp_path, "8260")[-1].startswith("1 of 2 items")


def test_typos_and_word_forms_find_the_item(tmp_path: Path) -> None:
    approved(tmp_path, "challenger-stop", "stop the challenger box")
    approved(tmp_path, "release", "deploy the release")
    assert recall(tmp_path, "challnger")[0].startswith("recipe challenger-stop")
    assert recall(tmp_path, "deploys")[0].startswith("recipe release")
    assert recall(tmp_path, "zzzzqq") == ["0 of 2 items"]


def test_lessons_are_found_and_shown_as_when_do(tmp_path: Path) -> None:
    ok(
        tmp_path,
        *("lessons", "add", "--when", "posting to Zulip", "--do", "full PR URLs"),
        *("--not", "bare #42", "--evidence", "user 10-22", "--no-sync"),
    )
    assert recall(tmp_path, "zulip")[0] == "lesson when posting to Zulip -> do full PR URLs"


def test_superseded_facts_proposed_and_forgotten_recipes_never_appear(tmp_path: Path) -> None:
    ok(tmp_path, "fact", "add", "prod-runs-on", "EKS", "--no-sync")
    ok(tmp_path, "fact", "add", "prod-runs-on", "EC2", "--no-sync")
    ok(tmp_path, "fact", "add", "gone", "wrongvalue", "--no-sync")
    ok(tmp_path, "fact", "forget", "gone", "--no-sync")
    approved(tmp_path, "live", "alpha procedure")
    ok(tmp_path, "recipe", "add", "waiting", "--desc", "alpha waiting", "--step", "x", "--no-sync")
    approved(tmp_path, "retired", "alpha retired")
    ok(tmp_path, "recipe", "forget", "retired", "--no-sync")
    assert recall(tmp_path, "EKS") == ["0 of 2 items"]
    assert recall(tmp_path, "wrongvalue") == ["0 of 2 items"]
    out = recall(tmp_path, "alpha")
    assert out[0].startswith("recipe live:") and out[-1].startswith("1 of 2 items")


def test_max_chars_cuts_whole_lines_and_the_footer_counts_what_was_left_out(
    tmp_path: Path,
) -> None:
    for i in range(5):
        ok(tmp_path, "fact", "add", f"item{i}", "shared word", "--no-sync")
    full = recall(tmp_path, "shared")
    assert full[-1].startswith("5 of 5 items") and "left out" not in full[-1]
    one = len(full[0]) + 1
    cut = recall(tmp_path, "shared", "--max-chars", str(2 * one + 3))
    assert cut[:-1] == full[:2]
    assert cut[-1] == f"2 of 5 items, {2 * one} chars, 3 left out"


def test_kind_filter_searches_only_that_kind(tmp_path: Path) -> None:
    ok(tmp_path, "fact", "add", "box", "shared word", "--no-sync")
    approved(tmp_path, "box-recipe", "shared word")
    only = recall(tmp_path, "shared", "--kind", "recipe")
    assert only[0].startswith("recipe box-recipe") and only[-1].startswith("1 of 1 items")
    assert recall(tmp_path, "shared", "--kind", "fact")[0].startswith("fact box:")
    assert run_cli(tmp_path, "recall", "x", "--kind", "bogus").code == 2


def test_each_query_is_logged_with_the_returned_keys(tmp_path: Path) -> None:
    ok(tmp_path, "fact", "add", "koyal-apiv2-port", "8260", "--no-sync")
    recall(tmp_path, "koyal port")
    recall(tmp_path, "nothing", "--kind", "fact")
    rows = [json.loads(x) for x in next(tmp_path.rglob("recall.jsonl")).read_text().splitlines()]
    assert [r["query"] for r in rows] == ["koyal port", "nothing"]
    assert rows[0]["returned"] == ["fact:koyal-apiv2-port"] and rows[1]["returned"] == []
    assert rows[1]["kind"] == "fact" and rows[0]["ts"]


def test_digit_terms_are_never_fuzzy_matched(tmp_path: Path) -> None:
    ok(tmp_path, "fact", "add", "port", "82600", "--no-sync")
    ok(tmp_path, "fact", "add", "short", "826", "--no-sync")
    assert recall(tmp_path, "826000") == ["0 of 2 items"]
    assert recall(tmp_path, "8261") == ["0 of 2 items"]
    assert recall(tmp_path, "82600")[0].startswith("fact port")


def test_blank_query_is_refused_and_not_logged(tmp_path: Path) -> None:
    for text in ("", "   "):
        r = run_cli(tmp_path, "recall", text)
        assert r.code == 2 and "give some words" in r.err
    assert not list(tmp_path.rglob("recall.jsonl"))


def test_item_matching_both_words_outranks_one_matching_one(tmp_path: Path) -> None:
    ok(tmp_path, "fact", "add", "one", "alpha filler words", "--no-sync")
    ok(tmp_path, "fact", "add", "two", "alpha beta filler", "--no-sync")
    lines = recall(tmp_path, "alpha beta")
    assert lines[0].startswith("fact two:") and lines[1].startswith("fact one:")


def facts_file(root: Path) -> Path:
    return next(root.rglob("facts.jsonl"))


def test_recently_verified_fact_outranks_an_otherwise_equal_old_one(tmp_path: Path) -> None:
    ok(tmp_path, "fact", "add", "aaa-old", "same words", "--no-sync")
    ok(tmp_path, "fact", "add", "bbb-new", "same words", "--no-sync")
    path = facts_file(tmp_path)
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    rows[0]["verified"] = "2020-01-01"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    lines = recall(tmp_path, "same words")
    assert lines[0].startswith("fact bbb-new:") and lines[1].startswith("fact aaa-old:")


def test_lesson_with_more_votes_outranks_an_otherwise_equal_one(tmp_path: Path) -> None:
    for _ in range(2):
        ok(
            tmp_path,
            *("lessons", "add", "--when", "same trigger", "--do", "same action"),
            *("--not", "x", "--evidence", "e", "--no-sync"),
        )
    ok(tmp_path, "lessons", "vote", "2", "up", "--no-sync")
    lines = recall(tmp_path, "same trigger")
    assert len(lines) == 3 and ok(tmp_path, "lessons", "list").splitlines()[1].startswith("L2 v3")
    log = [json.loads(x) for x in next(tmp_path.rglob("recall.jsonl")).read_text().splitlines()]
    assert log[0]["returned"] == ["lesson:L2", "lesson:L1"]
