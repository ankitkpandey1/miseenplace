"""Shared fixtures. Real directories and real processes only: no mocks, stubs or fakes."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from miseenplace.store import Store

REPO = Path(__file__).parents[1]

LEGACY_ADD: dict[str, object] = {
    "id": 1,
    "title": "old item",
    "kind": "mine",
    "state": "todo",
    "eta": "2026-10-21",
    "who": "Sam",
    "refs": ["https://example.com/pr/1"],
    "notes": [],
    "created": "2026-10-21 09:00",
    "updated": "2026-10-21 09:00",
}


def entry_line(seq: int, op: str, id_: int, changes: dict[str, object], by: str = "agent") -> str:
    e = {"seq": seq, "ts": "2026-10-21 09:00", "op": op, "id": id_, "by": by, "changes": changes}
    return json.dumps(e) + "\n"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path)


def log_path(store: Store) -> Path:
    return store.root / "data" / "audit_log.jsonl"


def snap_path(store: Store) -> Path:
    return store.root / "data" / "items.json"


def write_log(store: Store, *lines: str) -> None:
    log_path(store).parent.mkdir(parents=True, exist_ok=True)
    log_path(store).write_text("".join(lines))


@dataclass
class Result:
    code: int
    out: str
    err: str


def run_cli(root: Path, *args: str, env: dict[str, str] | None = None) -> Result:
    """Run the installed CLI as a separate process, as a shell or an agent would."""
    full_env = {k: v for k, v in os.environ.items() if not k.startswith(("ZULIP_", "MISEENPLACE_"))}
    full_env.update(env or {})
    done = subprocess.run(
        [sys.executable, "-m", "miseenplace", "--dir", str(root), *args],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )
    return Result(done.returncode, done.stdout, done.stderr)
