"""Small helpers for tests that need a real git identity inside a temp repository."""

import subprocess
from pathlib import Path


def clone_identity(root: Path) -> None:
    for key, value in (("user.name", "t"), ("user.email", "t@t.invalid")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
