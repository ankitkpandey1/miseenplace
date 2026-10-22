---
description: How miseenplace keeps agent token cost low - deterministic work in the CLI, one compact line per item, short skills and a tested output budget.
---

# Agent cost

A small model should be able to drive miseenplace. The design:

1. **All deterministic work lives in the CLI.** Classification, bookkeeping, deduplication and formatting are code, not prompts.
2. **The agent never reads raw digests.** `mep inbox` returns only actionable items, one line each: `id · who · where · ask (120 chars) · action`.
3. **One call per decision.** `mep triage ID --todo` creates the journal item with reference, topic and context filled in.
4. **Terse by default.** `--verbose` is for humans. The watcher prints at most one line per new item.
5. **Short skills.** Each `SKILL.md` is at most 60 lines: imperative steps, exact commands, a fixed decision table. Detail is in `references/`, read on demand.

## Measured

`tests/test_output_budget.py` fails if `mep inbox` for the demo data exceeds 700 characters, or if the whole triage loop (inbox plus three triage calls) exceeds 2000 characters, about 500 tokens. Compare: the demo chat in raw form is several times larger.

If you change an output format, the test tells you what it cost.
