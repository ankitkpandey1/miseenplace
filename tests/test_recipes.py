"""Recipes: proposed -> approved, placeholders, use-ordering, retraction; mep never runs steps."""

import json
import time
from pathlib import Path

from tests.conftest import Result, run_cli

STEP = "post in #eng/ops > MMBot: @**mm-dev-bot** stop, {server}"


def recipe(root: Path, *args: str) -> Result:
    writes = ("add", "approve", "ran", "forget")
    return run_cli(root, "recipe", *args, *(["--no-sync"] if args[0] in writes else []))


def ok(root: Path, *args: str) -> str:
    r = recipe(root, *args)
    assert r.code == 0, r.err
    return r.out.strip()


def add(root: Path, name: str, *steps: str, desc: str = "does a thing") -> str:
    return ok(root, "add", name, "--desc", desc, *[x for s in steps for x in ("--step", s)])


def stored(root: Path) -> list[dict[str, object]]:
    path = next(root.rglob("recipes.jsonl"))
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_add_creates_a_proposed_recipe_that_list_hides_until_all(tmp_path: Path) -> None:
    assert add(tmp_path, "stop-server", STEP, desc="turn a dev box off") == (
        "added stop-server (proposed)"
    )
    assert ok(tmp_path, "list") == "0 recipes"
    assert ok(tmp_path, "list", "--all") == "stop-server  turn a dev box off  (proposed)"
    assert recipe(tmp_path, "show", "stop-server").code == 1  # not approved, not in use
    assert recipe(tmp_path, "add", "x", "--desc", "d").code == 2  # a step is required
    assert recipe(tmp_path, "add", "x", "--step", "s").code == 2  # a description is required


def test_approve_puts_it_in_list_and_tags_filter(tmp_path: Path) -> None:
    add(tmp_path, "a", "one")
    ok(tmp_path, "add", "b", "--desc", "tagged", "--step", "one", "--tag", "infra")
    ok(tmp_path, "approve", "a")
    ok(tmp_path, "approve", "b")
    assert ok(tmp_path, "list").splitlines() == [
        "a  does a thing  (never run)",
        "b  tagged  (never run)",
    ]
    assert ok(tmp_path, "list", "--tag", "infra").startswith("b  tagged")
    assert recipe(tmp_path, "approve", "b").code == 1  # nothing proposed left
    assert recipe(tmp_path, "approve", "nope").code == 1


def test_readding_an_approved_recipe_keeps_the_approved_version_until_approve(
    tmp_path: Path,
) -> None:
    add(tmp_path, "r", "old step")
    ok(tmp_path, "approve", "r")
    add(tmp_path, "r", "new step", desc="edited")
    assert ok(tmp_path, "show", "r") == "1. old step"
    assert ok(tmp_path, "show", "r", "--proposed") == "1. new step"
    assert ok(tmp_path, "list") == "r  does a thing  (never run)"
    assert ok(tmp_path, "list", "--all").splitlines() == [
        "r  does a thing  (never run)",
        "r  edited  (proposed)",
    ]
    ok(tmp_path, "approve", "r")
    assert ok(tmp_path, "show", "r") == "1. new step"
    assert ok(tmp_path, "list", "--all") == "r  edited  (never run)"


def test_show_fills_placeholders_and_numbers_the_steps(tmp_path: Path) -> None:
    add(tmp_path, "stop-server", STEP, "confirm {server} is down")
    ok(tmp_path, "approve", "stop-server")
    out = ok(tmp_path, "show", "stop-server", "--set", "server=koyal")
    assert out.splitlines() == [
        "1. post in #eng/ops > MMBot: @**mm-dev-bot** stop, koyal",
        "2. confirm koyal is down",
    ]
    assert recipe(tmp_path, "show", "stop-server", "--set", "oops").code == 1


def test_an_unfilled_placeholder_is_listed_and_fails_never_guessed(tmp_path: Path) -> None:
    add(tmp_path, "r", "go {a} then {b} then {a}")
    ok(tmp_path, "approve", "r")
    r = recipe(tmp_path, "show", "r", "--set", "b=2")
    assert r.code == 1 and r.out.strip() == "1. go {a} then 2 then {a}"
    assert "{a}" in r.err and "{b}" not in r.err and r.err.count("{a}") == 1
    assert recipe(tmp_path, "show", "r", "--set", "a={b}", "--set", "b=2").code == 0


def test_ran_counts_uses_and_list_sorts_by_last_use_with_unused_last(tmp_path: Path) -> None:
    for name in ("never", "old", "mid", "new"):
        add(tmp_path, name, "s")
        ok(tmp_path, "approve", name)
    ok(tmp_path, "ran", "mid")
    ok(tmp_path, "ran", "mid")
    ok(tmp_path, "ran", "new")
    path = next(tmp_path.rglob("recipes.jsonl"))
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    for row in rows:
        if row["name"] == "old":
            row.update(ran=7, last_ran="2020-01-02")
        if row["name"] == "mid":
            row.update(last_ran="2020-05-06")
    path.write_text("".join(json.dumps(x) + "\n" for x in rows))
    day = time.strftime("%m-%d")
    assert ok(tmp_path, "list").splitlines() == [
        f"new  does a thing  (ran 1, last {day})",
        "mid  does a thing  (ran 2, last 05-06)",
        "old  does a thing  (ran 7, last 01-02)",
        "never  does a thing  (never run)",
    ]
    assert recipe(tmp_path, "ran", "nope").code == 1


def test_approving_a_new_version_keeps_the_use_count(tmp_path: Path) -> None:
    add(tmp_path, "r", "a")
    ok(tmp_path, "approve", "r")
    ok(tmp_path, "ran", "r")
    add(tmp_path, "r", "b")
    ok(tmp_path, "approve", "r")
    assert "ran 1" in ok(tmp_path, "list")


def test_ran_needs_an_approved_version(tmp_path: Path) -> None:
    add(tmp_path, "r", "a")
    assert recipe(tmp_path, "ran", "r").code == 1


def test_forget_retires_with_a_retraction_record(tmp_path: Path) -> None:
    add(tmp_path, "r", "a")
    ok(tmp_path, "approve", "r")
    add(tmp_path, "r", "b")
    ok(tmp_path, "forget", "r", "--why", "box is gone")
    assert ok(tmp_path, "list", "--all") == "0 recipes"
    assert {x["status"] for x in stored(tmp_path)} == {"retired"}
    assert {x["retracted"] for x in stored(tmp_path)} == {"box is gone"}
    for cmd in ("show", "ran", "approve"):
        r = recipe(tmp_path, cmd, "r")
        assert r.code == 1 and "box is gone" in r.err, cmd
    assert recipe(tmp_path, "forget", "r").code == 1
    add(tmp_path, "r", "again")  # a retired name can be proposed anew
    assert ok(tmp_path, "list", "--all").endswith("(proposed)")


def test_a_step_or_description_that_looks_like_a_secret_is_refused(tmp_path: Path) -> None:
    r = recipe(tmp_path, "add", "r", "--desc", "d", "--step", "curl -H 'Bearer abcdef1234567890'")
    assert r.code == 1 and "secret" in r.err
    r = recipe(tmp_path, "add", "r", "--desc", "password=hunter2", "--step", "s")
    assert r.code == 1 and "secret" in r.err
    assert ok(tmp_path, "list", "--all") == "0 recipes"
    assert recipe(tmp_path, "add", "r", "--desc", "d", "--step", "read 1Password item").code == 0


def test_a_tag_that_looks_like_a_secret_is_refused(tmp_path: Path) -> None:
    r = recipe(tmp_path, "add", "r", "--desc", "d", "--step", "s", "--tag", "ghp_abcdefgh1234")
    assert r.code == 1 and "secret" in r.err and "tag" in r.err
    assert ok(tmp_path, "list", "--all") == "0 recipes"


def test_doubled_braces_print_as_literals_and_shell_text_is_left_alone(tmp_path: Path) -> None:
    add(tmp_path, "r", "echo {{x}} ${HOME} {} {a-b} {{{n}}}")
    ok(tmp_path, "approve", "r")
    r = recipe(tmp_path, "show", "r", "--set", "n=1")
    assert r.code == 0, r.err
    assert r.out.strip() == "1. echo {x} ${HOME} {} {a-b} {1}"
    assert r.err == ""


def test_an_unused_set_key_is_noted_but_exit_stays_zero(tmp_path: Path) -> None:
    add(tmp_path, "r", "go {a}")
    ok(tmp_path, "approve", "r")
    r = recipe(tmp_path, "show", "r", "--set", "a=1", "--set", "z=9")
    assert r.code == 0 and r.out.strip() == "1. go 1" and "unused --set z" in r.err
    assert "unused --set a" not in r.err


def test_set_splits_on_the_first_equals_and_allows_empty(tmp_path: Path) -> None:
    add(tmp_path, "r", "a={a} b={b}")
    ok(tmp_path, "approve", "r")
    assert ok(tmp_path, "show", "r", "--set", "a=v=w", "--set", "b=") == "1. a=v=w b="


def test_dates_must_be_iso_and_two_approved_rows_for_a_name_fail_loud(tmp_path: Path) -> None:
    add(tmp_path, "r", "a")
    ok(tmp_path, "approve", "r")
    path = next(tmp_path.rglob("recipes.jsonl"))
    row = json.loads(path.read_text())
    for bad in ({"created": "yesterday"}, {"last_ran": "10-23"}):
        path.write_text(json.dumps({**row, **bad}) + "\n")
        r = recipe(tmp_path, "list")
        assert r.code == 1 and "line 1" in r.err, bad
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    r = recipe(tmp_path, "list")
    assert r.code == 1 and "line 2" in r.err and "second approved" in r.err


def test_forgetting_twice_keeps_both_retraction_records(tmp_path: Path) -> None:
    add(tmp_path, "r", "a")
    ok(tmp_path, "forget", "r", "--why", "first reason")
    add(tmp_path, "r", "b")
    ok(tmp_path, "forget", "r", "--why", "second reason")
    assert sorted(str(x["retracted"]) for x in stored(tmp_path)) == [
        "first reason",
        "second reason",
    ]
    assert "second reason" in recipe(tmp_path, "show", "r").err


def test_a_bad_row_names_its_line(tmp_path: Path) -> None:
    add(tmp_path, "r", "a")
    path = next(tmp_path.rglob("recipes.jsonl"))
    row = json.loads(path.read_text())
    for bad in ({"steps": "one"}, {"status": "maybe"}, {"ran": "3"}, {"tags": [1]}, {"desc": 5}):
        path.write_text(json.dumps(row) + "\n" + json.dumps({**row, **bad}) + "\n")
        r = recipe(tmp_path, "list")
        assert r.code == 1 and "line 2" in r.err, bad


def test_bad_names_are_refused(tmp_path: Path) -> None:
    for name in ("a:b", "two words"):
        assert recipe(tmp_path, "add", name, "--desc", "d", "--step", "s").code == 1
    assert recipe(tmp_path, "add", "n", "--desc", "d", "--step", " ").code == 1
