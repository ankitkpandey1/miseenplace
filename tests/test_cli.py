"""The command line, driven as a process the way a shell or an agent would."""

import json
import os
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from miseenplace.tour import seed
from tests.conftest import REPO, run_cli

ALL_COMMANDS = (
    "add", "set", "done", "drop", "log", "list", "top", "brief", "sync", "standup", "init",
    "new", "thread", "digest", "draft", "drafts", "send", "users", "demo", "skills", "style",
    "update",
)  # fmt: skip


def cli(root: Path, *args: str, ok: bool = True) -> str:
    extra = ["--no-sync"] if args[0] in ("add", "set", "done", "drop", "log") else []
    r = run_cli(root, *args, *extra)
    assert (r.code == 0) == ok, r.err
    return r.out if ok else r.err


def test_full_flow(tmp_path: Path) -> None:
    cli(tmp_path, "add", "low one", "--priority", "p3", "--topic", "T", "--tag", "a", "--tag", "b")
    cli(
        tmp_path, "add", "urgent", "--priority", "p0", "--eta", "2026-10-30",
        "--context", "boss asked", "--next", "draft", "--decision", "go::cheap", "--spent", "1h",
    )  # fmt: skip
    cli(tmp_path, "set", "low one", "--untag", "a", "--spent", "20m", "--blocker", "Sam")
    cli(tmp_path, "log", "wrote docs", "--topic", "T", "--spent", "30m", "--tag", "doc")
    assert cli(tmp_path, "top", "-n", "1").startswith("- **#2** urgent")
    listed = cli(tmp_path, "list", "--sort", "priority", "--tag", "b")
    assert "low one" in listed
    assert "urgent" not in listed
    text = cli(tmp_path, "brief", "T")
    for want in ("blocker: Sam", "tags: b", "spent: 20m"):
        assert want in text
    assert "wrote docs [T] — 30m" in cli(tmp_path, "standup", "--since", "today")


def test_bad_input_exits_with_a_fix_hint_and_writes_nothing(tmp_path: Path) -> None:
    assert "use 30m, 1h or 1h30m" in cli(tmp_path, "add", "x", "--spent", "lots", ok=False)
    assert "p0" in cli(tmp_path, "add", "x", "--priority", "urgent", ok=False)
    assert not (tmp_path / "data" / "audit_log.jsonl").exists()


@pytest.mark.parametrize("cmd", ALL_COMMANDS)
def test_every_subcommand_has_help(tmp_path: Path, cmd: str) -> None:
    assert "usage:" in run_cli(tmp_path, cmd, "--help").out


def test_writes_commit_when_the_storage_backend_is_git(tmp_path: Path) -> None:
    root = tmp_path / "data-root"
    root.mkdir()
    (root / "config.toml").write_text('[storage]\nbackend = "git"\n')
    assert "ready" in run_cli(root, "init").out
    for key, value in (("user.name", "t"), ("user.email", "t@t.invalid")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    assert run_cli(root, "add", "synced").code == 0
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline"], capture_output=True, text=True, check=True
    ).stdout
    assert "add #1" in log
    assert "synced" in (root / "Action Items.md").read_text()


def test_version_and_skills(tmp_path: Path) -> None:
    assert run_cli(tmp_path, "--version").out.startswith("miseenplace ")
    skills = Path(run_cli(tmp_path, "skills").out.strip())
    assert (skills / "setup" / "SKILL.md").is_file()


def test_data_root_comes_from_the_environment(tmp_path: Path) -> None:
    env = dict(os.environ, MISEENPLACE_DIR=str(tmp_path))
    done = subprocess.run(
        [sys.executable, "-m", "miseenplace", "add", "via env", "--no-sync"],
        capture_output=True, text=True, env=env, check=False, cwd=REPO,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert (
        json.loads((tmp_path / "data" / "items.json").read_text())["items"][0]["title"] == "via env"
    )


def test_short_alias_is_the_same_program() -> None:
    """Both console scripts resolve to one entry point; no PATH lookup involved."""
    points = {e.name: e.value for e in entry_points(group="console_scripts")}
    assert points["mep"] == points["miseenplace"] == "miseenplace.cli:main"


# ---- inbox commands against the demo source ----


@pytest.fixture
def demo_root(tmp_path: Path) -> Path:
    seed(tmp_path)
    return tmp_path


def test_new_thread_draft_send_flow(demo_root: Path) -> None:
    new = run_cli(demo_root, "--source", "demo", "new", "--cursor", "demo")
    assert new.code == 0
    assert "Changelog still pending" in new.out
    assert "I can take the changelog" not in new.out  # my own message is excluded
    assert run_cli(demo_root, "--source", "demo", "new", "--cursor", "demo").out == ""
    thread = run_cli(demo_root, "--source", "demo", "thread", "--channel", "platform",
                     "--topic", "release 2.4")  # fmt: skip
    assert "I can take the changelog" in thread.out  # the thread shows what `new` hides
    drafted = run_cli(demo_root, "--source", "demo", "draft", "--reply-to", "5", "Thursday")
    draft_id = drafted.out.split("draft ")[1].split()[0]
    assert "Thursday" in drafted.out
    assert not (demo_root / "demo" / "sent.jsonl").exists()
    assert draft_id in run_cli(demo_root, "--source", "demo", "drafts").out
    sent = run_cli(demo_root, "--source", "demo", "send", draft_id)
    assert sent.code == 0
    assert "Thursday" in (demo_root / "demo" / "sent.jsonl").read_text()
    assert draft_id not in run_cli(demo_root, "--source", "demo", "drafts").out


def test_send_with_free_text_is_refused_and_posts_nothing(demo_root: Path) -> None:
    r = run_cli(demo_root, "--source", "demo", "send", "hello team")
    assert r.code == 1
    assert "never message text" in r.err
    assert not (demo_root / "demo" / "sent.jsonl").exists()


def test_source_comes_from_the_environment(demo_root: Path) -> None:
    r = run_cli(demo_root, "users", "row", env={"MISEENPLACE_SOURCE": "demo"})
    assert r.out.startswith("Rowan Hale")


def test_unreachable_source_fails_quietly_on_stdout(tmp_path: Path) -> None:
    env = {
        "ZULIP_EMAIL": "me@example.com",
        "ZULIP_API_KEY": "x",
        "ZULIP_SITE": "https://127.0.0.1:1",
    }
    r = run_cli(tmp_path, "--source", "zulip", "new", "--cursor", "watcher", env=env)
    assert r.code == 2
    assert r.out == ""
    assert "unreachable" in r.err


def test_demo_runs_end_to_end_without_an_account(tmp_path: Path) -> None:
    r = run_cli(tmp_path, "demo", "--keep-dir", str(tmp_path / "tour"))
    assert r.code == 0, r.err
    for want in ("miseenplace inbox", "never message text", "already sent", "**Did**"):
        assert want in r.out
    assert (tmp_path / "tour" / "demo" / "sent.jsonl").exists()
    again = run_cli(tmp_path, "demo", "--keep-dir", str(tmp_path / "tour"))
    assert again.code == 1
    assert "not empty" in again.err


# ---- watch ----

ZULIP_DOWN = {
    "ZULIP_EMAIL": "me@example.com",
    "ZULIP_API_KEY": "x",
    "ZULIP_SITE": "https://127.0.0.1:1",
}


def test_watch_stays_silent_when_the_network_is_down_and_logs_why(tmp_path: Path) -> None:
    r = run_cli(tmp_path, "--source", "zulip", "watch", "--once", env=ZULIP_DOWN)
    assert (r.code, r.out, r.err) == (0, "", "")  # nothing wakes the agent
    assert "unreachable" in (tmp_path / "state" / "watch.log").read_text()


def test_watch_does_not_even_print_a_missing_login(tmp_path: Path) -> None:
    env = {"MISEENPLACE_ZULIP_RC": str(tmp_path / "missing-rc")}
    r = run_cli(tmp_path, "--source", "zulip", "watch", "--once", env=env)
    assert (r.code, r.out, r.err) == (0, "", "")
    assert "no Zulip credentials" in (tmp_path / "state" / "watch.log").read_text()


def test_watch_exits_with_one_line_per_new_actionable_item(demo_root: Path) -> None:
    r = run_cli(demo_root, "--source", "demo", "watch", "--cursor", "demo")
    assert r.code == 0
    assert [ln.split(" · ")[0] for ln in r.out.splitlines()] == ["3", "4", "5"]
    again = run_cli(demo_root, "--source", "demo", "watch", "--cursor", "demo", "--once")
    assert again.out == ""  # already delivered: no repeat


def test_watch_and_inbox_use_separate_cursors(demo_root: Path) -> None:
    run_cli(demo_root, "--source", "demo", "watch", "--cursor", "demo", "--once")
    # the agent's own cursor starts at now, and the watcher's items wait in the shared queue
    seen = run_cli(demo_root, "--source", "demo", "inbox", "--cursor", "agent").out
    assert [ln.split(" · ")[0] for ln in seen.splitlines()] == ["3", "4", "5"]
