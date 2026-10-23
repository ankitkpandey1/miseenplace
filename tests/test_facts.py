"""Facts: history on supersede, retraction warnings, secrets refused, staleness, provenance."""

import json
from pathlib import Path

from tests.conftest import Result, run_cli

DAY = "%Y-%m-%d"


def fact(root: Path, *args: str) -> Result:
    return run_cli(
        root, "fact", *args, *(["--no-sync"] if args[0] in ("add", "verify", "forget") else [])
    )


def ok(root: Path, *args: str) -> str:
    r = fact(root, *args)
    assert r.code == 0, r.err
    return r.out.strip()


def test_add_stores_source_tags_and_list_shows_age(tmp_path: Path) -> None:
    ok(tmp_path, "add", "prod-runs-on", "EC2, not EKS", "--source", "user 10-22", "--tag", "infra")
    ok(tmp_path, "add", "other", "x", "--tag", "misc")
    assert (
        ok(tmp_path, "list", "--tag", "infra")
        == "prod-runs-on: EC2, not EKS  (user 10-22, verified 0d)"
    )
    assert "other: x  (unknown, verified 0d)" in ok(tmp_path, "list")
    assert ok(tmp_path, "find", "ec2 eks").startswith("prod-runs-on")
    assert ok(tmp_path, "find", "nothing here") == "0 facts"
    assert ok(tmp_path, "get", "other") == "other: x  (unknown, verified 0d)"


def test_adding_an_existing_key_supersedes_and_history_keeps_both(tmp_path: Path) -> None:
    ok(tmp_path, "add", "prod-runs-on", "EC2, not EKS")
    ok(tmp_path, "add", "prod-runs-on", "EKS")
    assert ok(tmp_path, "list").count("prod-runs-on") == 1
    assert "EKS" in ok(tmp_path, "get", "prod-runs-on") and "EC2" not in ok(tmp_path, "list")
    old, new = ok(tmp_path, "get", "prod-runs-on", "--history").splitlines()[::-1]
    assert old.startswith("EC2, not EKS  valid 20") and not old.endswith("->")
    assert new.startswith("EKS  valid 20") and new.endswith("->")


def test_forget_then_readding_the_same_value_warns_with_the_reason(tmp_path: Path) -> None:
    ok(tmp_path, "add", "db-host", "old.example.com")
    ok(tmp_path, "forget", "db-host", "--why", "decommissioned")
    assert ok(tmp_path, "list") == "0 facts"
    assert "decommissioned" in ok(tmp_path, "get", "db-host", "--history")
    r = fact(tmp_path, "add", "db-host", "old.example.com")
    assert r.code == 0 and "retracted on 20" in r.err and "decommissioned" in r.err
    quiet = fact(tmp_path, "add", "db-host", "new.example.com")
    assert quiet.err == ""
    assert fact(tmp_path, "forget", "nope").code == 1


def test_a_value_that_looks_like_a_secret_is_refused(tmp_path: Path) -> None:
    for value in (
        "password=hunter2",
        "ghp_abcdefghijklmnop1234",
        "aZ3kQ9xLm2PwR7tYb4NcV8dF6gH1jS5u",
    ):
        r = fact(tmp_path, "add", "cred", value)
        assert r.code == 1 and "secret" in r.err
    assert ok(tmp_path, "list") == "0 facts"
    assert fact(tmp_path, "add", "cred-lives-in", "1Password vault infra").code == 0


def test_check_lists_facts_not_verified_within_days_and_verify_clears_them(tmp_path: Path) -> None:
    row = {"key": "old-one", "value": "v", "source": "me", "tags": [], "valid_from": "2020-01-01",
           "verified": "2020-01-01", "invalid_at": "", "retracted": ""}  # fmt: skip
    (tmp_path / "data").mkdir(exist_ok=True)
    ok(tmp_path, "add", "fresh", "v")
    data = next(tmp_path.rglob("facts.jsonl"))
    data.write_text(data.read_text() + json.dumps(row) + "\n")
    stale = ok(tmp_path, "check")
    assert "old-one" in stale and "fresh" not in stale
    assert "fresh" in ok(tmp_path, "check", "--days", "0")
    ok(tmp_path, "verify", "old-one")
    assert ok(tmp_path, "check") == "0 facts"


def test_a_chat_sourced_fact_is_marked_untrusted(tmp_path: Path) -> None:
    ok(tmp_path, "add", "from-chat", "claim", "--source", "chat message from Sam")
    ok(tmp_path, "add", "from-me", "claim", "--source", "user 10-22")
    lines = dict(x.split(":", 1) for x in ok(tmp_path, "list").splitlines())
    assert "untrusted" in lines["from-chat"] and "untrusted" not in lines["from-me"]


def test_ordinary_text_that_mentions_secrets_or_is_long_is_accepted(tmp_path: Path) -> None:
    for i, value in enumerate(
        (
            "/var/log/nginx/access-error-logs/app.log",
            "prod-runs-on-ec2-not-eks-in-us-east-1-region",
            "token: rotates weekly in vault",
            "password: in 1Password",
        )
    ):
        assert fact(tmp_path, "add", f"k{i}", value).code == 0


def test_tokens_urls_and_bearer_headers_are_refused_in_value_source_and_tag(tmp_path: Path) -> None:
    bad = [
        "Bearer abcdef1234567890",
        "postgres://u:hunter2@db:5432/x",
        "see github_pat_11ABCDEFG0abcd",
        "key AKIAIOSFODNN7EXAMPLE here",
        "xoxb-1234567890-abcdef",
        "uses sk-proj-abcdefgh12",
    ]
    for text in bad:
        assert fact(tmp_path, "add", "k", text).code == 1, text
        assert fact(tmp_path, "add", "k", "fine", "--source", text).code == 1, text
        assert fact(tmp_path, "add", "k", "fine", "--tag", text).code == 1, text
    assert ok(tmp_path, "list") == "0 facts"


def test_chat_words_match_whole_words_only(tmp_path: Path) -> None:
    sources = {"a": "dmarc docs", "b": "chatgpt", "c": "message-queue docs", "d": "user via zulip",
               "e": "zulip 1750823", "f": "slack DM"}  # fmt: skip
    for key, src in sources.items():
        ok(tmp_path, "add", key, "v", "--source", src)
    lines = dict(x.split(":", 1) for x in ok(tmp_path, "list").splitlines())
    assert [k for k, v in lines.items() if "untrusted" in v] == ["d", "e", "f"]


def test_readding_warns_with_the_latest_retraction(tmp_path: Path) -> None:
    for why in ("first reason", "second reason"):
        ok(tmp_path, "add", "h", "same")
        ok(tmp_path, "forget", "h", "--why", why)
    r = fact(tmp_path, "add", "h", "same")
    assert "second reason" in r.err and "first reason" not in r.err


def test_readding_the_same_value_updates_source_and_tags_and_says_so(tmp_path: Path) -> None:
    ok(tmp_path, "add", "k", "v")
    out = ok(tmp_path, "add", "k", "v", "--source", "user 10-23", "--tag", "infra")
    assert "already current" in out and "updated" in out
    assert ok(tmp_path, "list", "--tag", "infra").startswith("k: v  (user 10-23,")
    assert len(ok(tmp_path, "get", "k", "--history").splitlines()) == 1


def test_bad_keys_negative_days_and_forgotten_keys_are_reported(tmp_path: Path) -> None:
    for key in ("a:b", "two words"):
        assert fact(tmp_path, "add", key, "v").code == 1
    assert fact(tmp_path, "check", "--days", "-1").code == 2
    ok(tmp_path, "add", "gone", "v")
    ok(tmp_path, "forget", "gone", "--why", "moved")
    r = fact(tmp_path, "get", "gone")
    assert r.code == 1 and "retracted on 20" in r.err and "moved" in r.err
    assert fact(tmp_path, "verify", "never-there").code == 1
    ok(tmp_path, "add", "gone", "w")  # a forgotten key can be stated anew
    assert ok(tmp_path, "get", "gone").startswith("gone: w")
    assert len(ok(tmp_path, "get", "gone", "--history", "--width", "20").splitlines()) == 2


def test_find_matches_source_and_tag(tmp_path: Path) -> None:
    ok(tmp_path, "add", "k", "v", "--source", "runbook", "--tag", "infra")
    assert ok(tmp_path, "find", "runbook").startswith("k:")
    assert ok(tmp_path, "find", "infra").startswith("k:")


def test_a_fact_line_with_wrong_field_types_names_its_line(tmp_path: Path) -> None:
    ok(tmp_path, "add", "k", "v")
    path = next(tmp_path.rglob("facts.jsonl"))
    row = json.loads(path.read_text())
    for bad in ({"tags": "infra"}, {"tags": [1]}, {"value": 5}, {"verified": "yesterday"}):
        path.write_text(json.dumps({**row, **bad}) + "\n")
        r = fact(tmp_path, "list")
        assert r.code == 1 and "line 1" in r.err, bad
