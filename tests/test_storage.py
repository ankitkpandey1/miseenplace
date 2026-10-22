"""Backend behaviour beyond the shared contract: git publishing, loud failures, loading."""

import subprocess
from pathlib import Path

import pytest

from miseenplace.storage import open_storage, resolve_class
from miseenplace.storage.base import StorageError
from miseenplace.store import Store
from tests.test_storage_helpers import clone_identity


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return done.stdout


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(path))
    return path


def machine(tmp_path: Path, name: str, bare: Path) -> Store:
    root = tmp_path / name
    (root).mkdir()
    (root / "config.toml").write_text(f'[storage]\nbackend = "git"\nremote = "{bare}"\n')
    store = Store(root)
    clone_identity(root)
    return store


def test_a_write_on_one_machine_reaches_the_other(tmp_path: Path, bare: Path) -> None:
    laptop, desktop = machine(tmp_path, "laptop", bare), machine(tmp_path, "desktop", bare)
    laptop.add("from the laptop")
    laptop.sync("laptop write")
    desktop.refresh()
    assert [i["title"] for i in desktop.load()[1]] == ["from the laptop"]
    desktop.add("from the desktop")
    desktop.sync("desktop write")
    laptop.refresh()
    assert [i["title"] for i in laptop.load()[1]] == ["from the laptop", "from the desktop"]


def test_git_tracks_data_but_not_machine_state(tmp_path: Path, bare: Path) -> None:
    store = machine(tmp_path, "m", bare)
    store.add("x")
    (store.root / "state").mkdir(exist_ok=True)
    (store.root / "state" / "cursor").write_text("5")
    store.sync("w")
    tracked = git("-C", str(store.root), "ls-files").splitlines()
    assert "data/audit_log.jsonl" in tracked
    assert "Action Items.md" in tracked
    assert not any(t.startswith("state/") or t == "data/items.json" for t in tracked)


def test_a_failed_push_is_loud_and_nothing_is_lost(tmp_path: Path) -> None:
    root = tmp_path / "m"
    root.mkdir()
    gone = tmp_path / "does-not-exist.git"
    (root / "config.toml").write_text(f'[storage]\nbackend = "git"\nremote = "{gone}"\n')
    store = Store(root)
    clone_identity(root)
    store.add("kept")
    with pytest.raises(StorageError, match="git push failed"):
        store.sync("w")
    assert [i["title"] for i in store.load()[1]] == ["kept"]
    assert "kept" in git("-C", str(root), "show", "HEAD:Action Items.md")  # committed locally


def test_unknown_backend_names_the_available_ones() -> None:
    with pytest.raises(StorageError, match="available: git, local"):
        resolve_class("s3-someday")


def test_module_colon_class_loads_a_custom_backend(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        '[storage]\nbackend = "tests.memory_storage:MemoryStorage"\n'
    )
    store = Store(tmp_path)
    store.add("in memory")
    assert not (tmp_path / "data").exists()  # nothing touched the disk
    assert [i["title"] for i in Store(tmp_path).load()[1]] == ["in memory"]


def test_a_missing_module_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="cannot load storage backend"):
        open_storage(tmp_path, {"backend": "no_such_pkg:Thing"})


def test_a_class_that_is_not_a_storage_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="does not implement"):
        open_storage(tmp_path, {"backend": "tests.memory_storage:NotAStorage"})
