"""`search` over real stores in a temp data root, the real demo source and a real HTTP server."""

import json
import random
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from miseenplace.search import Doc, Query, chat_hits, ranked
from miseenplace.sources.base import SourceError
from miseenplace.sources.demo import DemoSource
from miseenplace.sources.zulip import Credentials, ZulipSource
from miseenplace.store import Store
from miseenplace.tour import seed
from tests.conftest import Result, entry_line, run_cli, write_log
from tests.http_server import Seen, serve

ME = "me@example.com"


def mep(root: Path, *args: str) -> Result:
    write = args[0] in ("add", "set", "log", "ask", "fact", "recipe", "lessons", "note-request")
    return run_cli(root, "--source", "demo", *args, *(["--no-sync"] if write else []))


def find(root: Path, *args: str) -> list[str]:
    r = mep(root, "search", *args)
    assert r.code == 0, r.err
    return r.out.splitlines()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    seed(tmp_path)
    return tmp_path


def fill_every_store(root: Path) -> None:
    mep(root, "add", "Plan the gizmo launch", "--who", "Sam", "--topic", "launch",
        "--context", "Sam wants a quokka demo", "--next", "book a zebra room",
        "--note", "waiting for walrus budget", "--decision", "use narwhal::cheaper")  # fmt: skip
    mep(root, "log", "fixed the aardvark parser", "--topic", "parser")
    mep(root, "note-request", "find the ocelot thread")
    mep(root, "lessons", "add", "--when", "a pangolin appears", "--do", "wait", "--not", "run",
        "--evidence", "monday")  # fmt: skip
    mep(root, "fact", "add", "capybara-port", "8260", "--source", "ops")
    mep(root, "recipe", "add", "wombat-deploy", "--desc", "ship the wombat", "--step", "push")
    mep(root, "ask", "Ship the lemur?", "--recommend", "yes", "--default", "hold",
        "--confidence", "read")  # fmt: skip
    mep(root, "draft", "--reply-to", "5", "Thursday, ibex attached")


# ---- global and per kind ----


@pytest.mark.parametrize(
    ("word", "kind", "shown"),
    [
        ("gizmo", "item", "#1"),
        ("quokka", "item", "#1"),  # context
        ("zebra", "item", "#1"),  # next
        ("walrus", "item", "#1"),  # notes
        ("narwhal", "item", "#1"),  # decisions
        ("aardvark", "log", "fixed the aardvark parser"),
        ("ocelot", "request", "find the ocelot thread"),
        ("pangolin", "lesson", "L1"),
        ("capybara", "fact", "capybara-port: 8260"),
        ("wombat", "recipe", "wombat-deploy"),
        ("lemur", "ask", "A1"),
        ("ibex", "draft", "ibex attached"),
        ("branch", "chat", "Release branch is cut"),
    ],
)
def test_each_kind_is_found_globally_and_alone(
    root: Path, word: str, kind: str, shown: str
) -> None:
    fill_every_store(root)
    for lines in (find(root, word), find(root, word, "--in", kind)):
        assert len(lines) == 1, lines
        assert lines[0].startswith(f"{kind} · ") and shown in lines[0]
    others = [k for k in ("item", "log", "draft", "request", "lesson", "fact") if k != kind]
    assert find(root, word, "--in", others[0]) == []


def test_global_search_groups_by_kind_and_in_is_repeatable(root: Path) -> None:
    fill_every_store(root)
    mep(root, "log", "gizmo notes")
    mep(root, "note-request", "the gizmo question")
    both = find(root, "gizmo")
    assert [x.split(" · ")[0] for x in both] == ["item", "log", "request"]
    two = find(root, "gizmo", "--in", "request", "--in", "log")
    assert [x.split(" · ")[0] for x in two] == ["log", "request"]


def test_chat_hit_shows_id_sender_where_and_is_case_insensitive(root: Path) -> None:
    (line,) = find(root, "PARSER pr", "--in", "chat")
    assert line.startswith("chat · 3 · Rowan Hale · DM: Rowan Hale · Could you review the parser")


def test_an_item_hit_is_followed_by_brief(root: Path) -> None:
    mep(root, "add", "Plan the gizmo launch")
    (line,) = find(root, "gizmo")
    assert "#1" in line
    assert "Plan the gizmo launch" in mep(root, "brief", "1").out


def test_every_word_must_match(root: Path) -> None:
    mep(root, "add", "alpha beta")
    assert len(find(root, "alpha beta")) == 1
    assert find(root, "alpha gamma") == []


def test_no_hits_prints_nothing(root: Path) -> None:
    r = mep(root, "search", "xyzzyplugh")
    assert (r.code, r.out, r.err) == (0, "", "")


def test_the_limit_keeps_the_best_hits(root: Path) -> None:
    for n in range(5):
        mep(root, "add", f"widget {n}")
    assert len(find(root, "widget")) == 5
    assert len(find(root, "widget", "-n", "2")) == 2


def test_output_stays_compact(root: Path) -> None:
    mep(root, "add", "long " * 100 + "needle")
    out = "\n".join(find(root, "needle"))
    assert 0 < len(out) <= 200 and "**" not in out


# ---- --from and --days ----


def test_from_filters_items_and_chat_by_who(root: Path) -> None:
    mep(root, "add", "task for Sam", "--who", "Sam")
    mep(root, "add", "task for Alex", "--who", "Alex")
    mep(root, "log", "task diary")
    assert [x.split(" · ")[1] for x in find(root, "task", "--from", "sam")] == ["#1"]
    assert len(find(root, "changelog", "--in", "chat")) == 3
    from_alex = find(root, "changelog", "--from", "alex chen")
    assert len(from_alex) == 2 and all("Alex Chen" in x for x in from_alex)


def test_days_keeps_only_recent_hits(root: Path) -> None:
    store = Store(root)
    old: dict[str, object] = {
        "text": "old report",
        "topic": None,
        "tags": [],
        "spent": 0,
        "refs": [],
    }
    write_log(store, entry_line(1, "log", 0, old).replace("2026-10-21 09:00", "2020-01-01 09:00"))
    mep(root, "log", "new report")
    assert len(find(root, "report")) == 2
    (recent,) = find(root, "report", "--days", "1")
    assert "new report" in recent


def test_days_must_be_a_positive_whole_number(root: Path) -> None:
    for bad in ("0", "-1", "1.5", "x"):
        assert mep(root, "search", "report", "--days", bad).code == 2


# ---- fuzzy ----


def test_a_typo_finds_the_item_and_exact_does_not(root: Path) -> None:
    mep(root, "add", "Plan release management")
    (hit,) = find(root, "relase mgmt")
    assert "Plan release management" in hit
    assert find(root, "relase mgmt", "--exact") == []
    assert len(find(root, "release management", "--exact")) == 1


def test_a_partial_word_hits_even_when_exact(root: Path) -> None:
    mep(root, "add", "Plan release management")
    assert len(find(root, "manage", "--exact")) == 1


def test_exact_matches_rank_before_fuzzy_ones(root: Path) -> None:
    mep(root, "add", "relase managment typo")
    mep(root, "add", "release management exact")
    mep(root, "add", "release management exact, and later")
    order = [x.split(" · ")[1] for x in find(root, "release management")]
    assert sorted(order[:2]) == ["#2", "#3"]  # both exact
    assert order[2] == "#1"  # the typo comes last
    assert find(root, "release management", "--exact") == [
        x for x in find(root, "release management") if "#1" not in x
    ]


def test_a_closer_typo_ranks_before_a_farther_one(root: Path) -> None:
    mep(root, "add", "dployment notes")  # one letter off
    mep(root, "add", "deplymnt notes")  # two letters off
    mep(root, "add", "unrelated")
    order = [x.split(" · ")[1] for x in find(root, "deployment")]
    assert order == ["#1", "#2"]


def test_a_number_is_never_fuzzy(root: Path) -> None:
    mep(root, "fact", "add", "port", "82600")
    assert find(root, "826000") == []


# ---- chat ----


def test_chat_falls_back_to_one_search_per_word_then_fuzzy_ranks(root: Path) -> None:
    q = Query("changelog thursdy")
    src = DemoSource(root)
    assert src.search(q.text) == []  # the source alone finds nothing
    (hit,) = chat_hits(src, q)
    assert hit.line.startswith("chat · 2 · Jordan Rivers")
    assert chat_hits(src, Query("changelog thursdy", exact=True)) == []


def test_chat_without_a_configured_source_errors_alone_and_is_skipped_globally(
    root: Path,
) -> None:
    mep(root, "add", "changelog item")
    alone = run_cli(root, "search", "changelog", "--in", "chat")
    assert alone.code == 1 and "no source chosen" in alone.err
    everywhere = run_cli(root, "search", "changelog")
    assert everywhere.code == 0 and everywhere.err == ""
    assert everywhere.out.count("\n") == 1 and everywhere.out.startswith("item")


def zmsg(id_: int, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": id_, "type": "stream", "timestamp": int(time.time()) - 100 + id_,
        "subject": "release", "content": "hello", "sender_full_name": "Alex Chen",
        "sender_email": "alex@example.com", "sender_id": 3, "display_recipient": "eng",
        "flags": [],
    }  # fmt: skip
    return base | over


def test_zulip_search_uses_the_server_search_and_sender_narrow(tmp_path: Path) -> None:
    seen = Seen(
        messages=[zmsg(1, content="ship it"), zmsg(2, content="old", timestamp=1000)],
        members=[{"user_id": 3, "email": "alex@example.com", "full_name": "Alex Chen",
                  "is_active": True, "is_bot": False}],
    )  # fmt: skip
    with serve(seen) as base:
        src = ZulipSource(tmp_path, Credentials(ME, base, "k"), frozenset())
        found = src.search("ship it", "chen", 1.0, 5)
    assert [m.id for m in found] == ["1"]  # the 1970 message is outside --days
    assert found[0].url and found[0].url.endswith("/#narrow/id/1")
    narrow = json.loads(parse_qs(urlsplit(seen.paths[-1]).query)["narrow"][0])
    assert narrow == [
        {"operator": "search", "operand": "ship it"},
        {"operator": "sender", "operand": "alex@example.com"},
    ]


def test_an_exact_hit_beats_a_fuzzy_one_even_with_a_lower_score(root: Path) -> None:
    mep(root, "add", "alpha management")  # whole word + one typo: mean 0.97
    mep(root, "add", "alphabet managments")  # both only substrings: mean 0.95, but exact
    order = [x.split(" · ")[1] for x in find(root, "alpha managment")]
    assert order == ["#2", "#1"]


def test_a_query_with_no_letters_or_digits_is_a_usage_error_for_every_kind(root: Path) -> None:
    for args in (("--in", "chat"), ("--in", "item"), ()):
        r = mep(root, "search", "?!...", *args)
        assert r.code == 1 and r.out == "" and r.err.count("\n") == 1, r.err
        assert "nothing to search for" in r.err


def test_a_unicode_query_matches_case_insensitively_and_reaches_the_server(
    root: Path, tmp_path: Path
) -> None:
    mep(root, "add", "Réunion CAFÉ 日本語 notes")
    assert len(find(root, "café")) == 1 and len(find(root, "日本語", "--exact")) == 1
    seen = Seen(messages=[zmsg(1, content="un café")])
    with serve(seen) as base:
        src = ZulipSource(tmp_path, Credentials(ME, base, "k"), frozenset())
        assert [m.id for m in src.search("café")] == ["1"]
    narrow = json.loads(parse_qs(urlsplit(seen.paths[-1]).query)["narrow"][0])
    assert narrow == [{"operator": "search", "operand": "café"}]


def test_an_ambiguous_sender_is_an_error_not_a_guess(tmp_path: Path) -> None:
    people: list[dict[str, object]] = [
        {"user_id": n, "email": f"alex{n}@example.com", "full_name": f"Alex {n}",
         "is_active": True, "is_bot": False}
        for n in (1, 2)
    ]  # fmt: skip
    with serve(Seen(messages=[zmsg(1)], members=people)) as base:
        src = ZulipSource(tmp_path, Credentials(ME, base, "k"), frozenset())
        with pytest.raises(SourceError, match="2 zulip users match 'alex'"):
            src.search("hello", "alex")
        with pytest.raises(SourceError, match="0 zulip users match 'zed'"):
            src.search("hello", "zed")


def test_chat_text_cannot_carry_terminal_control_characters(root: Path) -> None:
    DemoSource(root).append("#ops > \x1b[31mred", "Eve\x07", "pay\x1b]0;owned\x07 now\rfake line")
    (line,) = find(root, "fake", "--in", "chat")
    assert not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in line)
    assert "\n" not in line


def test_five_thousand_documents_are_searched_in_under_two_seconds() -> None:
    rng = random.Random(1)

    def word() -> str:
        return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(4, 9)))

    docs = [Doc("item", f"item · #{n}", " ".join(word() for _ in range(40))) for n in range(5000)]
    docs.append(Doc("item", "item · #needle", "x release management y"))
    start = time.perf_counter()
    hits = ranked(docs, Query("relase mgmt"))
    assert time.perf_counter() - start < 2.0
    assert [h.line for h in hits] == ["item · #needle"]
