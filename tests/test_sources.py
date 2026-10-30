"""Cursors, the demo source and the Zulip helpers that need no network."""

import time
from pathlib import Path

import pytest

from miseenplace.chat import Target
from miseenplace.config import Config
from miseenplace.model import MiseError
from miseenplace.sources import open_source
from miseenplace.sources.base import Cursors, SourceError
from miseenplace.sources.demo import DemoSource
from miseenplace.sources.zulip import (
    Credentials,
    ZMessage,
    ZulipSource,
    credentials_from_env,
    credentials_from_rc,
    key_of,
    load_credentials,
    quote_reply,
    to_message,
)
from miseenplace.tour import seed


@pytest.fixture
def demo(tmp_path: Path) -> DemoSource:
    seed(tmp_path)
    return DemoSource(tmp_path)


def test_two_consumers_never_swallow_each_others_messages(demo: DemoSource) -> None:
    demo.cursors.set("session", "0")
    first = demo.new("session")
    assert [m.id for m in first] == ["1", "3", "4", "5", "6"]
    assert [m.id for m in demo.new("session")] == []  # consumed
    # the watcher has not looked yet: it must still get everything session already read
    demo.cursors.set("watcher", "0")
    assert [m.id for m in demo.new("watcher")] == ["1", "3", "4", "5", "6"]
    demo.append("DM: Rowan Hale", "Rowan Hale", "one more")
    assert [m.text for m in demo.new("session")] == ["one more"]
    assert [m.text for m in demo.new("watcher")] == ["one more"]


def test_unknown_cursor_sets_a_baseline_and_returns_nothing(demo: DemoSource) -> None:
    assert demo.new("fresh") == []
    assert demo.cursors.get("fresh") == "6"
    demo.append("DM: Sam Okafor", "Sam Okafor", "hi")
    assert [m.text for m in demo.new("fresh")] == ["hi"]


def test_init_resets_the_cursor_to_now(demo: DemoSource) -> None:
    assert demo.new("demo", init=True) == []
    assert demo.new("demo") == []


def test_my_own_messages_are_never_new(demo: DemoSource) -> None:
    demo.new("c", init=True)
    demo.append("DM: Rowan Hale", "Jordan Rivers", "my reply", mine=True)
    assert demo.new("c") == []
    # and the thread shows it, which is why a thread must be re-read before "pending"
    assert "my reply" in [m.text for m in demo.thread(Target("Rowan Hale"))]


@pytest.mark.parametrize("bad", ["", "a b", "../x", "x" * 65])
def test_bad_cursor_names_are_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(MiseError, match="bad cursor name"):
        Cursors(tmp_path, "demo").get(bad)


def test_registry_rejects_unknown_and_missing_sources(tmp_path: Path) -> None:
    with pytest.raises(MiseError, match="unknown source"):
        open_source("nope", tmp_path, Config())
    with pytest.raises(MiseError, match="no source chosen"):
        open_source(None, tmp_path, Config())


# ---- zulip: pure helpers over real payload shapes ----


def payload(**over: object) -> ZMessage:
    base: dict[str, object] = {
        "id": 10, "type": "stream", "timestamp": int(time.time()), "subject": "deploys",
        "content": "ship it", "sender_full_name": "Alex Chen", "sender_email": "alex@example.com",
        "sender_id": 7, "display_recipient": "ops", "flags": [],
    }  # fmt: skip
    return ZMessage(**{**base, **over})  # type: ignore[typeddict-item]


@pytest.fixture
def zulip(tmp_path: Path) -> ZulipSource:
    creds = Credentials("me@example.com", "https://127.0.0.1:1", "not-a-real-key")
    return ZulipSource(
        tmp_path, creds, noise_streams=frozenset({"alerts"}), noise_topics=frozenset({"Build"})
    )


def test_zulip_for_me_rules(zulip: ZulipSource) -> None:
    topics = {("ops", "deploys")}
    assert zulip.is_for_me(payload(type="private", display_recipient=[]), topics)
    assert zulip.is_for_me(payload(flags=["mentioned"]), topics)
    assert zulip.is_for_me(payload(), topics)  # a topic I posted in
    assert not zulip.is_for_me(payload(subject="other"), topics)
    assert not zulip.is_for_me(payload(sender_email="me@example.com"), topics)


def test_zulip_noise_is_config_and_a_mention_overrides_it(zulip: ZulipSource) -> None:
    assert zulip.is_noise(payload(display_recipient="alerts"))
    assert zulip.is_noise(payload(subject="Build"))
    assert not zulip.is_noise(payload(display_recipient="alerts", flags=["wildcard_mentioned"]))
    assert not zulip.is_noise(payload(display_recipient="ops"))


def test_zulip_thread_labels_and_message_conversion() -> None:
    dm = payload(
        type="private",
        display_recipient=[
            {"id": 1, "email": "me@example.com", "full_name": "Me"},
            {"id": 7, "email": "alex@example.com", "full_name": "Alex Chen"},
        ],
    )
    assert key_of(dm, "me@example.com") == "DM: Alex Chen"
    assert key_of(payload(), "me@example.com") == "#ops > deploys"
    m = to_message(payload(sender_email="me@example.com"), "me@example.com", "https://z.example")
    assert (m.mine, m.url) == (True, "https://z.example/#narrow/id/10")


def test_zulip_reply_uses_zulips_quote_format() -> None:
    text = quote_reply(payload(content="  ready?  "), "https://z.example", "yes")
    assert text.startswith("@_**Alex Chen|7** [said](https://z.example/#narrow/id/10):")
    assert "```quote\nready?\n```" in text
    assert text.endswith("\n\nyes")


def test_zulip_reply_nests_a_quote_inside_the_quoted_message() -> None:
    inner = "@_**Sam|12** [said](x):\n````quote\naddressed\n````\n\nfixed"
    text = quote_reply(payload(content=inner), "https://z.example", "approved")
    assert "\n`````quote\n" + inner + "\n`````\n\napproved" in text


def test_credentials_from_env_rc_and_hidden_key(tmp_path: Path) -> None:
    env = {
        "ZULIP_EMAIL": "a@example.com",
        "ZULIP_API_KEY": "k3y",
        "ZULIP_SITE": "https://z.example/",
    }
    creds = credentials_from_env(env)
    assert creds == Credentials("a@example.com", "https://z.example", "k3y")
    assert "k3y" not in repr(creds)
    assert credentials_from_env({"ZULIP_EMAIL": "a"}) is None
    rc = tmp_path / "rc"
    rc.write_text("[api]\nemail=b@example.com\nkey=abc\nsite=https://rc.example\n")
    assert load_credentials({"rc": str(rc)}, {}).email == "b@example.com"
    assert load_credentials({}, {"MISEENPLACE_ZULIP_RC": str(rc)}).site == "https://rc.example"
    with pytest.raises(SourceError, match="no Zulip credentials"):
        credentials_from_rc(tmp_path / "missing")
    rc.write_text("[api]\nemail=b@example.com\n")
    with pytest.raises(MiseError, match="needs an"):
        credentials_from_rc(rc)


def test_unreachable_zulip_raises_a_retryable_source_error(zulip: ZulipSource) -> None:
    with pytest.raises(SourceError, match="unreachable"):
        zulip.new("watcher")
