---
description: A 30-minute guide to writing a miseenplace storage backend - the Storage Protocol, a template, loading it by name and passing the contract tests.
---

# Writing a storage backend (30 minutes)

Storage holds the audit log, the snapshot and named files (page, style, corpus, request log). Per-machine state (cursors, drafts, inbox queue) is not storage and stays on local disk.

## The contract

`miseenplace.storage.base.Storage`:

| Method | Meaning |
|---|---|
| `append(entry)` | add one log entry, durably |
| `read_entries(after=0)` | entries with `seq > after`, in order |
| `load_snapshot()` / `save_snapshot(seq, items)` | the cache; `None` until saved |
| `read_file(name)` / `write_file(name, text)` / `append_file(name, text)` | named text files; `read_file` returns `None` if missing |
| `refresh()` | called before reads; pull remote changes |
| `sync(message)` | called after writes; publish them |

Reject unsafe names with `check_name(name)`: it raises `StorageError` for `..`, absolute paths and empty names. Raise `StorageError` for any failure. Never swallow one.

## Steps

1. Copy `src/miseenplace/storage/_template.py`; implement the methods (15 min). `tests/memory_storage.py` is a complete example in 40 lines.
2. Constructor signature: `(root: Path, settings: dict[str, object])`; `settings` is the `[storage]` table.
3. Load it by name:
   ```toml
   [storage]
   backend = "my_package.module:ClassName"
   ```
   or publish an entry point in the `miseenplace.storage` group and use the short name.
4. Run the contract suite against it (10 min): add your factory to `BACKENDS` in `tests/contract/test_storage_contract.py`. It checks ordering, `after`, persistence across reopening, snapshot, files and name safety.

## Sketches (prose only)

- **SQLite**: one table of entries keyed by `seq`, one for files; `append` is an INSERT inside a transaction; `refresh` and `sync` do nothing.
- **S3 or object storage**: keep the log as numbered objects (`log/000001.json`), because objects are not appendable; `refresh` lists new keys; `sync` does nothing because writes are already remote.
- **Notion or a database**: entries become rows; guard against two writers with a unique constraint on `seq`, and raise `StorageError` on conflict so the caller retries.
