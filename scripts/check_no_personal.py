#!/usr/bin/env python3
"""Fail if a tracked file names a person, company or domain that must stay out of the repo.

Edit DENYLIST for your own checkout. The match is case-insensitive. This file is skipped, as is
anything listed in ALLOWED_LITERALS (public handles that the project deliberately uses).
Usage: python scripts/check_no_personal.py [paths...]   (default: every file git tracks)
"""

import re
import subprocess
import sys
from pathlib import Path

DENYLIST = ("mammoth", "edgemetric", "ankit", "zulip.mammoth")
# The public repository handle is intentional (URLs, citation); nothing else may contain "ankit".
ALLOWED_LITERALS = ("ankitkpandey1",)
SKIP_SUFFIXES = (".png", ".gif", ".jpg", ".ico", ".whl", ".gz", ".lock")
ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()


def candidate_files(args: list[str]) -> list[Path]:
    if args:
        return [Path(a).resolve() for a in args]
    done = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    names = done.stdout.split() if done.returncode == 0 else []
    return [ROOT / n for n in names] or [p for p in ROOT.rglob("*") if p.is_file()]


def violations(path: Path, pattern: re.Pattern[str]) -> list[str]:
    if path == SELF or path.suffix in SKIP_SUFFIXES or not path.is_file():
        return []
    text = path.read_text(errors="ignore")
    for allowed in ALLOWED_LITERALS:
        text = text.replace(allowed, "")
    return [
        f"{path.relative_to(ROOT)}:{n}: {m.group(0)!r}"
        for n, line in enumerate(text.splitlines(), 1)
        for m in pattern.finditer(line)
    ]


def main(argv: list[str]) -> int:
    pattern = re.compile("|".join(map(re.escape, DENYLIST)), re.IGNORECASE)
    found = [v for p in candidate_files(argv) for v in violations(p, pattern)]
    for v in found:
        print(v)
    if found:
        print(f"{len(found)} personal reference(s); remove them or edit DENYLIST", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
