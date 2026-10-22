---
description: Exact layout of the miseenplace data root - audit log, snapshot, config, inbox queue, cursors, drafts, style, corpus and request log.
---

# Data model

Everything lives under the data root: `--dir`, else `$MISEENPLACE_DIR`, else `~/.local/share/miseenplace`.

```text
config.toml                      settings (actors, sources, storage)
data/audit_log.jsonl             source of truth, append-only, fsynced
data/items.json                  snapshot cache, rebuilt from the log
Action Items.md                  rendered page
style/<source>.md                your writing style (learn-style)
corpus/<source>/sent.jsonl       your sent messages, deduplicated by message id
requests.jsonl                   what you asked your agent to do, verbatim
state/cursors/<source>/<name>    one read position per consumer
state/inbox/<source>/<id>.json   one queued message per file
state/drafts/<id>.json           stored drafts (and .sending while claimed)
state/watch.log                  watcher errors
```

`state/` and `data/items.json` are per machine or rebuildable and are not committed by the git backend.

## Audit log

One JSON object per line, validated by `schema/log-entry.schema.json`:

```json
{"seq": 3, "ts": "2026-10-21 09:00", "op": "add", "id": 1, "by": "agent", "changes": {"title": "Review parser PR"}}
```

Replay checks: sequence numbers have no gaps, `by` is a configured actor, and each `old` value in an update matches the replayed state. A broken log fails loudly instead of rendering a wrong page.

## Items

Validated by `schema/item.schema.json`. Fields: `id`, `title`, `kind` (`mine`, `waiting`, `reminder`), `state`, `eta`, `who`, `refs`, `notes`, `created`, `updated`, plus optional work context: `topic`, `context`, `next`, `blocker`, `think`, `priority` (p0 to p3), `spent`, `tags`, `decisions`.

## Inbox item

`id`, `who`, `where`, `ts`, `text`, `url`, `status` (`new`, `open`, `auto-ignored`, `answered`, `todo`, `waiting`, `ignored`, `drafted`), `action` and `ref` (the journal id or draft id it led to).

## Draft

`id` (`d` plus 8 hex digits), `target`, `text`, `sha256`, `created`. The hash is checked on send; the file is claimed exclusively while sending so delivery happens once. A failed delivery leaves the draft sendable.

## Personal data

Corpus, style and request files are personal. See [Security](security.md).
