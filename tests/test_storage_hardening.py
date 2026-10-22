"""Storage and store under failure: two machines, two processes, torn files, bad config."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from miseenplace.model import MiseError
from miseenplace.storage import open_storage
from miseenplace.storage.base import StorageError, StorageUnreachable
from miseenplace.store import Store
from tests.conftest import (
    LEGACY_ADD,
    entry_line,
    log_path,
    run_cli,
    snap_path,
    write_log,
)
from tests.test_storage_helpers import clone_identity


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return done.stdout


def titles(store: Store) -> list[str]:
    return [i["title"] for i in store.load()[1]]


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(path))
    return path


def machine(tmp_path: Path, name: str, remote: Path | str, extra: str = "") -> Store:
    root = tmp_path / name
    root.mkdir()
    (root / "config.toml").write_text(f'[storage]\nbackend = "git"\nremote = "{remote}"\n{extra}')
    store = Store(root)
    clone_identity(root)
    return store


# ---- B1: two machines, divergent work ----


def test_two_machines_that_both_wrote_end_up_with_both_entries(tmp_path: Path, bare: Path) -> None:
    laptop, desktop = machine(tmp_path, "laptop", bare), machine(tmp_path, "desktop", bare)
    laptop.add("L1")
    laptop.sync("l1")
    desktop.add("D1")  # pulled L1 first, wrote D1, has not pushed
    laptop.add("L2")
    laptop.sync("l2")  # the remote moved on
    desktop.sync("d1")  # diverged: this is the merge
    laptop.refresh()
    for store in (laptop, desktop):
        seq, items = store.load()
        assert sorted(titles(store)) == ["D1", "L1", "L2"]
        assert sorted(i["id"] for i in items) == [1, 2, 3]  # no duplicate id
        assert [e["seq"] for e in store.read_log()] == [1, 2, 3] == list(range(1, seq + 1))
        text = (store.root / "data" / "audit_log.jsonl").read_text()
        assert "<<<<<<<" not in text and ">>>>>>>" not in text
    assert git("-C", str(desktop.root), "status", "--porcelain", "-uno") == ""
    assert (desktop.root / "data" / "audit_log.jsonl").read_text() == (
        laptop.root / "data" / "audit_log.jsonl"
    ).read_text()


def test_a_merge_git_cannot_do_aborts_cleanly_and_says_what_to_do(
    tmp_path: Path, bare: Path
) -> None:
    laptop, desktop = machine(tmp_path, "laptop", bare), machine(tmp_path, "desktop", bare)
    (laptop.root / "style").mkdir()
    (laptop.root / "style" / "voice.md").write_text("short\n")
    laptop.add("first")
    laptop.sync("with style")
    desktop.refresh()
    (desktop.root / "style" / "voice.md").write_text("short and warm\n")
    desktop.add("desk work")  # commits the edited style file locally
    git("-C", str(laptop.root), "rm", "-q", "style/voice.md")
    laptop.add("lap work")
    laptop.sync("deleted style")
    before = (desktop.root / "data" / "audit_log.jsonl").read_text()
    with pytest.raises(StorageError, match="could not merge"):
        desktop.sync("conflict")
    assert git("-C", str(desktop.root), "status", "--porcelain", "-uno") == ""  # repo left clean
    assert not (desktop.root / ".git" / "MERGE_HEAD").exists()
    assert (desktop.root / "data" / "audit_log.jsonl").read_text() == before  # nothing lost


def test_a_rejected_push_is_loud_and_keeps_the_commit(tmp_path: Path, bare: Path) -> None:
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'no pushes today' >&2\nexit 1\n")
    hook.chmod(0o755)
    store = machine(tmp_path, "m", bare)
    store.add("kept")
    with pytest.raises(StorageError, match=r"git push failed.*no pushes today"):
        store.sync("w")
    assert "kept" in git("-C", str(store.root), "show", "HEAD:Action Items.md")


# ---- B2: the lock ----


def test_processes_adding_at_the_same_moment_never_share_a_sequence_number(tmp_path: Path) -> None:
    """Each process imports first, then waits for a go file, so all of them write together."""
    data = tmp_path / "root"
    go = tmp_path / "go"
    code = (
        "import sys, time; from pathlib import Path; from miseenplace.store import Store; "
        "go = Path(sys.argv[2]); "
        "[time.sleep(0.01) for _ in iter(lambda: not go.exists(), False)]; "
        "Store(Path(sys.argv[1])).add('t' + sys.argv[3])"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(data), str(go), str(n)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for n in range(12)
    ]
    time.sleep(1.0)  # let every interpreter reach the wait
    go.write_text("go")
    assert [p.wait(timeout=60) for p in procs] == [0] * 12
    store = Store(data)
    assert [e["seq"] for e in store.read_log()] == list(range(1, 13))
    assert sorted(i["id"] for i in store.load()[1]) == list(range(1, 13))


# ---- B3: no branch yet is not the same as unreachable ----


def test_an_empty_remote_is_fine_but_an_unreachable_one_is_reported(
    tmp_path: Path, bare: Path
) -> None:
    fresh = machine(tmp_path, "fresh", bare)
    fresh.refresh()  # the remote has no branch yet: nothing to pull, no error
    fresh.add("first")
    fresh.sync("first")
    assert "first" in git("-C", str(bare), "show", "main:Action Items.md")
    lost = machine(tmp_path, "lost", tmp_path / "nowhere.git")
    with pytest.raises(StorageUnreachable, match="cannot reach"):
        lost.storage.refresh()


def test_an_unreachable_remote_still_lets_the_user_write_and_says_so(tmp_path: Path) -> None:
    machine(tmp_path, "m", tmp_path / "nowhere.git")
    r = run_cli(tmp_path / "m", "add", "offline work", "--no-sync")
    assert r.code == 0
    assert "using local copy" in r.err


# ---- B4 and B5: damaged files ----


def test_a_torn_last_line_is_refused_with_the_exact_line(store: Store) -> None:
    write_log(store, entry_line(1, "add", 1, LEGACY_ADD), '{"seq": 2, "ts": "2026')
    with pytest.raises(StorageError, match=r"torn line.*line 2.*fix or delete that one line"):
        store.load()
    assert log_path(store).read_text().endswith('"2026')  # nothing was changed


def test_a_damaged_line_in_the_middle_is_refused_too(store: Store) -> None:
    write_log(store, entry_line(1, "add", 1, LEGACY_ADD), "garbage\n", entry_line(2, "log", 0, {}))
    with pytest.raises(StorageError, match="damaged line: line 2"):
        store.load()


def test_an_append_after_a_missing_newline_starts_on_its_own_line(store: Store) -> None:
    store.add("one")
    text = log_path(store).read_text()
    log_path(store).write_text(text.rstrip("\n"))  # the final newline was lost
    store.add("two")
    assert [e["seq"] for e in store.read_log()] == [1, 2]


@pytest.mark.parametrize("content", ["", "{ torn", "[]", '{"seq": 1}', "\x00\x01"])
def test_a_garbled_snapshot_is_rebuilt_from_the_log(store: Store, content: str) -> None:
    store.add("kept")
    snap_path(store).write_text(content)
    assert titles(Store(store.root)) == ["kept"]


def test_a_snapshot_ahead_of_the_log_is_discarded(store: Store) -> None:
    store.add("kept")
    snap = json.loads(snap_path(store).read_text())
    snap["seq"] = 99
    snap_path(store).write_text(json.dumps(snap))
    assert titles(Store(store.root)) == ["kept"]


def test_a_snapshot_made_from_other_entries_is_discarded(store: Store) -> None:
    store.add("kept")
    snap = json.loads(snap_path(store).read_text())
    snap["fingerprint"] = "0" * 64
    snap["items"][0]["title"] = "stale cache"
    snap_path(store).write_text(json.dumps(snap))
    assert titles(Store(store.root)) == ["kept"]


# ---- B6: files that must not be tracked ----


def test_a_snapshot_committed_by_an_old_version_is_untracked_on_the_next_sync(
    tmp_path: Path, bare: Path
) -> None:
    store = machine(tmp_path, "m", bare)
    store.add("x")
    git("-C", str(store.root), "add", "-f", "data/items.json")
    git("-C", str(store.root), "commit", "-qm", "old version tracked it")
    assert "data/items.json" in git("-C", str(store.root), "ls-files")
    store.sync("w")
    assert "data/items.json" not in git("-C", str(store.root), "ls-files")


# ---- B7: a cache that cannot be written does not fail the command ----


def test_a_snapshot_write_failure_after_the_append_is_a_warning(tmp_path: Path) -> None:
    (tmp_path / "data" / "items.json.tmp").mkdir(parents=True)  # the swap file cannot be made
    r = run_cli(tmp_path, "add", "still saved", "--no-sync")
    assert r.code == 0
    assert "snapshot not updated" in r.err
    assert titles(Store(tmp_path)) == ["still saved"]  # rebuilt from the log


# ---- B8: config ----


@pytest.mark.parametrize(
    "body",
    [
        "actors = 'me'\n",
        "actors = [1, 2]\n",
        "actors = []\n",
        "default_source = 5\n",
        "storage = 'git'\n",
        "sources = []\n",
        "[sources]\nzulip = 'x'\n",
        "[consent]\ndraft_ttl_hours = 'a day'\n",
        "[consent]\ndraft_ttl_hours = -1\n",
        "this is = = not toml\n",
    ],
)
def test_a_bad_config_is_a_clear_error_and_never_a_traceback(tmp_path: Path, body: str) -> None:
    (tmp_path / "config.toml").write_text(body)
    r = run_cli(tmp_path, "list")
    assert r.code == 1
    assert "config.toml" in r.err
    assert "Traceback" not in r.err
    with pytest.raises(MiseError):
        Store(tmp_path)


# ---- B9: no hard-coded file names, UTF-8 everywhere ----


def test_an_inconsistent_log_error_does_not_send_the_user_to_a_file_that_may_not_exist(
    store: Store,
) -> None:
    write_log(
        store,
        entry_line(1, "add", 1, LEGACY_ADD),
        entry_line(2, "set", 1, {"title": ["not the old title", "new"]}),
    )
    with pytest.raises(MiseError, match="inconsistent") as caught:
        store.load()
    assert "items.json" not in str(caught.value)


def test_non_ascii_survives_a_c_locale(tmp_path: Path) -> None:
    """Files are read and written as UTF-8 whatever the platform default is."""
    env = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    code = (
        "import sys; from pathlib import Path; from miseenplace.store import Store; "
        "s = Store(Path(sys.argv[1])); s.add('caf\\u00e9 \\u65e5\\u672c'); "
        "print(len(s.load()[1]))"
    )
    for _ in range(2):  # the second run reads back what the first wrote
        done = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            capture_output=True, text=True, env=env, check=False,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
    assert [i["title"] for i in Store(tmp_path).load()[1]] == ["caf\u00e9 \u65e5\u672c"] * 2


# ---- B10: configured branch and remote ----


def test_the_configured_branch_is_used_even_if_the_repo_was_left_on_another(
    tmp_path: Path, bare: Path
) -> None:
    store = machine(tmp_path, "m", bare)
    store.add("x")
    store.sync("w")
    git("-C", str(store.root), "checkout", "-q", "-b", "scratch")
    again = Store(store.root)
    again.add("y")
    again.sync("w2")
    assert git("-C", str(store.root), "branch", "--show-current").strip() == "main"
    assert "y" in git("-C", str(bare), "show", "main:Action Items.md")


def test_a_changed_remote_url_in_the_config_is_followed(tmp_path: Path, bare: Path) -> None:
    first = machine(tmp_path, "m", tmp_path / "old.git")
    first.add("x")
    (first.root / "config.toml").write_text(f'[storage]\nbackend = "git"\nremote = "{bare}"\n')
    moved = Store(first.root)
    moved.sync("to the new remote")
    assert git("-C", str(first.root), "remote", "get-url", "origin").strip() == str(bare)
    assert "x" in git("-C", str(bare), "show", "main:Action Items.md")


# ---- B11 ----


def test_log_returns_the_entry_it_appended(store: Store) -> None:
    entry = store.log("worked on it", topic="t")
    assert (entry["seq"], entry["op"], entry["changes"]["text"]) == (1, "log", "worked on it")
    assert store.read_log() == [entry]


def test_open_storage_still_works_for_the_default(tmp_path: Path) -> None:
    assert open_storage(tmp_path, {}).read_entries() == []
