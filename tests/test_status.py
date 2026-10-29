"""`status`: the live snapshot. GitHub is asked per run; the pure rules take `gh` output as text."""

import json
import subprocess
from pathlib import Path

import pytest

from miseenplace.model import Item, MiseError
from miseenplace.status import (
    Pr,
    ci_of,
    fetch_all,
    fetch_pr,
    parse_pr,
    render,
    stale,
)
from miseenplace.store import Store
from tests.conftest import Result, run_cli

PR = "https://github.com/acme/app/pull/42"
PR2 = "https://github.com/acme/app/pull/43"
SHA = "0123456789abcdef0123456789abcdef01234567"


def gh_json(state: str = "OPEN", review: str = "", rollup: object = (), sha: str = SHA) -> str:
    return json.dumps(
        {
            "state": state,
            "reviewDecision": review,
            "headRefOid": sha,
            "statusCheckRollup": list(rollup),  # type: ignore[call-overload]
            "mergedAt": None,
        }
    )


def add(store: Store, title: str, *refs: str, kind: str = "mine", state: str = "todo") -> Item:
    item = store.add(title, kind, refs=list(refs))
    return store.update(str(item["id"]), "set", state=state) if state != "todo" else item


# ---- the CI rollup ----


@pytest.mark.parametrize(
    ("rollup", "want"),
    [
        ([], "none"),
        (None, "none"),
        ([{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}], "pass"),
        ([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}], "fail"),
        ([{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS", "conclusion": ""}], "pending"),
        ([{"state": "SUCCESS"}, {"state": "PENDING"}], "pending"),
        ([{"state": "ERROR"}, {"state": "PENDING"}], "fail"),
        ([{"conclusion": "TIMED_OUT"}], "fail"),
    ],
)
def test_the_ci_rollup_is_pass_fail_pending_or_none(rollup: object, want: str) -> None:
    assert ci_of(rollup) == want


# ---- one PR ----


def test_a_pr_is_read_from_gh_output() -> None:
    pr = parse_pr(PR, gh_json("OPEN", "APPROVED", [{"conclusion": "SUCCESS"}]))
    assert (pr.ref, pr.state, pr.review, pr.ci, pr.head) == (
        "acme/app#42",
        "OPEN",
        "APPROVED",
        "pass",
        SHA[:7],
    )


def test_no_review_decision_reads_none() -> None:
    assert parse_pr(PR, gh_json()).review == "none"


def test_unreadable_gh_output_is_a_line_not_a_crash() -> None:
    assert parse_pr(PR, "not json").error == "gh: unreadable output"
    assert parse_pr(PR, "[]").error == "gh: unreadable output"


def test_a_failing_gh_says_so_on_that_prs_line() -> None:
    def broken(url: str) -> str:
        raise MiseError("no pull requests found for branch")

    assert fetch_pr(PR, broken).error == "gh: no pull requests found for branch"


def test_a_gh_timeout_says_so_on_that_prs_line() -> None:
    def hung(url: str) -> str:
        raise subprocess.TimeoutExpired("gh", 30)

    assert fetch_pr(PR, hung).error == "gh: timed out"


def test_without_gh_every_pr_says_so() -> None:
    assert fetch_pr(PR, None).error == "gh: not installed"


# ---- many PRs ----


def test_each_distinct_pr_is_asked_once_even_when_two_items_ref_it(store: Store) -> None:
    asked: list[str] = []

    def fetch(url: str) -> str:
        asked.append(url)
        return gh_json()

    items = [add(store, "a", PR, PR2), add(store, "b", PR)]
    got = fetch_all(items, fetch)
    assert sorted(asked) == [PR, PR2] and set(got) == {PR, PR2}


def test_one_failing_pr_does_not_hide_the_others(store: Store) -> None:
    def fetch(url: str) -> str:
        if url == PR:
            raise MiseError("boom")
        return gh_json("MERGED")

    got = fetch_all([add(store, "a", PR, PR2)], fetch)
    assert got[PR].error == "gh: boom" and got[PR2].state == "MERGED"


def test_an_item_is_stale_only_when_every_pr_it_refs_is_closed(store: Store) -> None:
    item = add(store, "a", PR, PR2)
    merged, open_, failed = (
        Pr("acme/app", 42, state="MERGED"),
        Pr("acme/app", 43, state="OPEN"),
        Pr("acme/app", 43, error="gh: x"),
    )
    assert stale(item, {PR: merged, PR2: Pr("acme/app", 43, state="CLOSED")})
    assert not stale(item, {PR: merged, PR2: open_})
    assert not stale(item, {PR: merged, PR2: failed})  # unknown is not closed
    assert not stale(add(store, "no pr", "https://example.com/x"), {})


# ---- the page ----


def test_the_page_groups_by_who_holds_the_item(store: Store) -> None:
    add(store, "mine one")
    add(store, "asked of Sam", kind="waiting", state="waiting")
    add(store, "mine but parked", state="waiting")
    out = render(store.load()[1], {}).splitlines()
    assert out[0] == "On you (1)" and "mine one" in out[1]
    assert out[2] == "Waiting on others (2)"
    assert "asked of Sam" in out[3] and "mine but parked" in out[4]
    assert out[5] == "PRs (0)"


def test_an_item_shows_its_latest_note_on_one_line(store: Store) -> None:
    item = add(store, "report")
    store.update(str(item["id"]), "set", note="first")
    store.update(
        str(item["id"]), "set", note="agent report: [PR](https://x.test/1) " + "long " * 40
    )
    line = render(store.load()[1], {}).splitlines()[1]
    assert "first" not in line and "agent report: PR long" in line
    assert "http" not in line and line.endswith("…") and len(line) < 200


def test_a_pr_line_has_repo_state_review_ci_head_and_the_item(store: Store) -> None:
    items = [add(store, "ship it", PR)]
    pr = parse_pr(PR, gh_json("OPEN", "CHANGES_REQUESTED", [{"conclusion": "FAILURE"}]))
    line = render(items, {PR: pr}).splitlines()[-1]
    assert line == f"  acme/app#42 OPEN review=CHANGES_REQUESTED ci=fail {SHA[:7]} · #1 ship it"


def test_an_open_item_whose_prs_are_all_merged_is_flagged_with_the_fix(store: Store) -> None:
    items = [add(store, "ship it", PR)]
    out = render(items, {PR: parse_pr(PR, gh_json("MERGED"))}).splitlines()
    assert out[-1] == "  ! #1 is open but every PR is closed: mep done 1"
    assert "MERGED" in out[-2]


def test_a_pr_gh_could_not_read_keeps_its_line(store: Store) -> None:
    items = [add(store, "ship it", PR)]
    out = render(items, {PR: Pr("acme/app", 42, error="gh: not installed")}).splitlines()
    assert out[-1] == "  acme/app#42 gh: not installed · #1 ship it"


# ---- the command ----


def mep(root: Path, *args: str, env: dict[str, str] | None = None) -> Result:
    return run_cli(root, *args, env=env)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    s = Store(tmp_path)
    add(s, "ship it", PR)
    add(s, "ask Sam", kind="waiting", state="waiting")
    s.update("2", "set", who="Sam")
    s.add("api work", "mine", topic="api")
    return tmp_path


def test_without_gh_on_the_path_the_pr_line_says_so_and_nothing_is_dropped(
    root: Path, tmp_path: Path
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    r = mep(root, "status", env={"PATH": str(empty)})
    assert r.code == 0, r.err
    assert r.out.splitlines()[-1] == "  acme/app#42 gh: not installed · #1 ship it"


def test_topic_and_who_narrow_every_section(root: Path) -> None:
    out = mep(root, "status", "--topic", "api").out
    assert "api work" in out and "ship it" not in out and "ask Sam" not in out
    out = mep(root, "status", "--who", "sam").out
    assert "ask Sam" in out and "api work" not in out


def test_json_is_one_object_with_the_three_groups(root: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    r = mep(root, "status", "--json", env={"PATH": str(empty)})
    data = json.loads(r.out)
    assert [i["id"] for i in data["on_you"]] == [1, 3]
    assert [i["id"] for i in data["waiting"]] == [2]
    assert data["prs"][0]["pr"] == "acme/app#42" and data["prs"][0]["error"] == "gh: not installed"
    assert data["stale"] == [] and r.out.count("\n") == 1


def test_an_empty_journal_is_three_headings(tmp_path: Path) -> None:
    assert mep(tmp_path, "status").out.splitlines() == [
        "On you (0)",
        "Waiting on others (0)",
        "PRs (0)",
    ]
