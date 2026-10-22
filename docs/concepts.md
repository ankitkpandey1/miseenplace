---
description: The ideas behind miseenplace - the journal write-ahead log, sources, the inbox queue, cursors, the consent gate and pluggable storage.
---

# Concepts

miseenplace is a chef's mise en place for the workday: everything asked of you, promised by you or owed to you, laid out before the day starts. Agents drive the CLI; the CLI does the deterministic work.

## The journal

Items are things to do (`mine`), things you wait on (`waiting`) and reminders. Every change is one line appended to an audit log, which is the source of truth. A snapshot (`items.json`) is a cache rebuilt from the log, and `Action Items.md` is a page rendered from it. A write that fails validation writes nothing. See [Data model](data-model.md).

## Sources

A source is a chat service behind a small contract: `new`, `thread`, `draft`, `send`, `users`. Zulip and a file-backed demo ship today. Writing another takes about 30 minutes: [Writing a source](writing-a-source.md).

`new` never returns your own messages. So an agent must re-read the thread before calling anything pending: you may already have answered.

## Cursors

Each consumer (a watcher, an agent session, a cron job) has its own read position, one tiny file per cursor. Consumers never overwrite each other. A new cursor sets a baseline and reports nothing from before it.

## The inbox queue

`mep inbox` ingests new messages into a queue (one file per message), classifies each (`reply`, `todo`, `waiting`, `ignore`) and prints only the actionable ones, one line each. Ingest queues first and classifies after, so a network failure loses nothing. Messages you already answered are hidden. `mep triage` handles one item in one call.

## The consent gate

`draft` stores the exact text and returns a draft id. `send` accepts only a draft id and posts exactly the stored text, once. There is no way to send free text. This is structural, not proof that a human approved: see [Security](security.md) for what it does and does not guarantee.

## Your words are data

Your own sent messages (`corpus/`), the style file learned from them (`style/`) and a verbatim log of what you asked your agent to do (`requests.jsonl`) live in the data root. Summaries drift; verbatim words do not.

## Storage

The log, snapshot and named files go through a `Storage` backend chosen in `config.toml`:

```toml
[storage]
backend = "local"   # local (default) | git | "package.module:ClassName"
```

- `local`: plain files under the data root.
- `git`: local files in a git repository; pulls before a read, commits and pushes after a write; a failed pull or push is an error, never silent.
- Your own: [Writing a storage backend](writing-a-storage-backend.md). Backends are also discoverable through the `miseenplace.storage` entry-point group.

## Agent-native

Every command is terse by default and has a machine-readable form. Skills are short, with fixed decision tables, so a small model can drive them. See [Agent cost](agent-cost.md).

## Concurrency and sync

Every write takes an exclusive lock on the data root (`.lock`, POSIX `fcntl`; Windows is untested), pulls first when the storage has a remote, then appends. The snapshot (`data/items.json`) stores a fingerprint of the log entry it was built from and is rebuilt whenever it disagrees with the log. With the git backend, two machines that both wrote are merged by keeping the remote's entries first and re-appending the local unpushed entries after them, renumbered; if git cannot merge, the merge is aborted and the repository is left clean.
