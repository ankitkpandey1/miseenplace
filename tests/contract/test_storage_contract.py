"""The storage contract, run against every backend: the two built-ins and a custom one.

The git backend runs against a REAL bare repository in a temp directory.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from miseenplace.model import Entry
from miseenplace.storage import open_storage
from miseenplace.storage.base import Storage, StorageError

Make = Callable[[Path, Path], Storage]  # (data root, scratch dir) -> backend


def local(root: Path, scratch: Path) -> Storage:
    return open_storage(root, {"backend": "local"})


def git_only(root: Path, scratch: Path) -> Storage:
    return open_storage(root, {"backend": "git"})


def git_remote(root: Path, scratch: Path) -> Storage:
    bare = scratch / "remote.git"
    if not bare.exists():
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return open_storage(root, {"backend": "git", "remote": str(bare)})


def memory(root: Path, scratch: Path) -> Storage:
    return open_storage(root, {"backend": "tests.memory_storage:MemoryStorage"})


BACKENDS = [
    pytest.param(local, id="local"),
    pytest.param(git_only, id="git"),
    pytest.param(git_remote, id="git-remote"),
    pytest.param(memory, id="custom-memory"),
]


def configure_git(root: Path) -> None:
    if (root / ".git").exists():
        for key, value in (("user.name", "t"), ("user.email", "t@t.invalid")):
            subprocess.run(["git", "-C", str(root), "config", key, value], check=True)


@pytest.fixture(params=BACKENDS)
def make(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[], Storage]:
    factory: Make = request.param
    root = tmp_path / "root"

    def build() -> Storage:
        backend = factory(root, tmp_path)
        configure_git(root)
        return backend

    return build


def entry(seq: int) -> Entry:
    changes: dict[str, object] = {
        "text": f"e{seq}",
        "topic": None,
        "tags": [],
        "spent": 0,
        "refs": [],
    }
    return {
        "seq": seq,
        "ts": "2026-10-21 09:00",
        "op": "log",
        "id": 0,
        "by": "me",
        "changes": changes,
    }


def test_it_satisfies_the_protocol(make: Callable[[], Storage]) -> None:
    assert isinstance(make(), Storage)


def test_log_appends_in_order_and_reads_after_a_sequence(make: Callable[[], Storage]) -> None:
    s = make()
    assert s.read_entries() == []
    for n in (1, 2, 3):
        s.append(entry(n))
    assert [e["seq"] for e in s.read_entries()] == [1, 2, 3]
    assert [e["seq"] for e in s.read_entries(after=2)] == [3]
    assert s.read_entries(after=3) == []


def test_data_survives_reopening_the_root(make: Callable[[], Storage]) -> None:
    first = make()
    first.append(entry(1))
    first.save_snapshot(1, [], "fp")
    first.write_file("style/demo.md", "short")
    again = make()
    assert [e["seq"] for e in again.read_entries()] == [1]
    assert again.load_snapshot() == (1, [], "fp")
    assert again.read_file("style/demo.md") == "short"


def test_snapshot_is_none_until_saved_then_replaced_whole(make: Callable[[], Storage]) -> None:
    s = make()
    assert s.load_snapshot() is None
    s.save_snapshot(1, [], "a")
    s.save_snapshot(2, [], "b")
    assert s.load_snapshot() == (2, [], "b")


def test_named_files_read_write_and_append(make: Callable[[], Storage]) -> None:
    s = make()
    assert s.read_file("requests.jsonl") is None
    s.append_file("requests.jsonl", "a\n")
    s.append_file("requests.jsonl", "b\n")
    assert s.read_file("requests.jsonl") == "a\nb\n"
    s.write_file("requests.jsonl", "c\n")
    assert s.read_file("requests.jsonl") == "c\n"


@pytest.mark.parametrize("bad", ["../escape", "/etc/passwd", "a/../../b", "", "a//b"])
def test_file_names_cannot_leave_the_data_root(make: Callable[[], Storage], bad: str) -> None:
    s = make()
    with pytest.raises(StorageError, match="bad file name"):
        s.write_file(bad, "x")
    with pytest.raises(StorageError):
        s.read_file(bad)


def test_refresh_and_sync_succeed_on_a_healthy_backend(make: Callable[[], Storage]) -> None:
    s = make()
    s.append(entry(1))
    s.write_file("Action Items.md", "# page\n")
    s.refresh()
    s.sync("contract")
    s.refresh()
    assert [e["seq"] for e in s.read_entries()] == [1]
