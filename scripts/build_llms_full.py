#!/usr/bin/env python3
"""Rebuild llms-full.txt: the README and the docs pages, in order, each under a marker comment."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = "# miseenplace — full documentation for agents\n"
PAGES = (
    "README.md", "docs/index.md", "docs/quickstart.md", "docs/concepts.md", "docs/cli.md",
    "docs/data-model.md", "docs/sources.md", "docs/writing-a-source.md",
    "docs/writing-a-storage-backend.md", "docs/agent-cost.md", "docs/security.md",
)  # fmt: skip


def build(root: Path) -> str:
    parts = [HEADER]
    for page in PAGES:
        parts.append(f"\n<!-- {page} -->\n\n{(root / page).read_text(encoding='utf-8')}")
    return "\n".join(parts)


def main() -> int:
    out = ROOT / "llms-full.txt"
    text = build(ROOT)
    if "--check" in sys.argv:
        return 0 if out.read_text(encoding="utf-8") == text else 1
    out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
