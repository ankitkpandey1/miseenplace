---
description: Reference for every miseenplace command (mep) - journal, inbox, triage, watch, drafts, style and request log.
---

# CLI reference

`miseenplace` and `mep` are the same program. Global options go before the command: `--dir PATH`, `--source NAME`, `--version`. Environment: `MISEENPLACE_DIR`, `MISEENPLACE_SOURCE`.

Exit codes: 0 ok, 1 usage or journal error, 2 source error (network, auth). Errors are one line on stderr.

## Journal

| Command | Purpose |
|---|---|
| `add TITLE [--kind mine\|waiting\|reminder] [--who] [--eta] [--ref] [--topic] [--context] [--next] [--priority]` | new item |
| `set ID [--state] [--note] [--spent] [--tag] [--decision]` | change an item |
| `done ID [--note]` / `drop ID` | close |
| `log TEXT [--topic] [--spent]` | diary entry |
| `list [--topic] [--tag] [--sort]` | list items |
| `top [-n N]` | what first |
| `brief ID_OR_TOPIC` | full view |
| `standup [--since yesterday]` | Did / Next / Waiting on |
| `sync` | pull, render the page, publish (storage decides) |

## Inbox

| Command | Purpose |
|---|---|
| `inbox [--cursor NAME] [--json] [--all] [--verbose]` | only actionable items, `id · who · where · ask · action` |
| `triage ID --todo\|--waiting\|--ignore\|--reply-draft TEXT [--eta] [--topic]` | one call per item; exactly one action |
| `watch [--cursor NAME] [--interval 300] [--once]` | block until a new actionable item, print, exit 0; `--interval` is at least 10; `--once` exits 0 with empty output when nothing is new; errors go to `state/watch.log` |

## Source

| Command | Purpose |
|---|---|
| `new [--cursor NAME] [--init]` | raw new messages, silent if none |
| `thread --channel C --topic T` or `--dm NAME` | read a thread |
| `digest [--days N]` | grouped history for humans |
| `draft TEXT [--channel --topic \| --dm] [--reply-to ID]` | store the exact text; posts nothing |
| `drafts` | unsent drafts |
| `send DRAFT_ID [--mark-sent\|--mark-unsent]` | post a stored draft, only an id; the flags record what you found after an unconfirmed send |
| `users QUERY` | find people |

## Your words

| Command | Purpose |
|---|---|
| `note-request TEXT` | append your exact words to `requests.jsonl` |
| `requests [-n N]` | read them back |
| `style show` / `style learn [--days N]` / `style sample` / `style set -` | style file and sent-message corpus |

## Setup

`init` creates the data root and `config.toml`; `demo` runs the guided tour; `skills` prints the path of the bundled skills.
