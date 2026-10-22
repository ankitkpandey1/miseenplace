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
| `today [--days N] [--width W]` | threads of the last N days (default 1) that involve you: you were @-mentioned, it is a DM, or you posted. Per thread: state tag, label, message count and the last messages (3 when awaiting you, 2 otherwise), one dense line each: `HH:MM name text`, links and markdown stripped, `you` for yours. An empty day prints `0 threads today`. Awaiting-you first; a header counts each state |
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

`init` creates the data root and `config.toml`; `demo` runs the guided tour.

| Command | Purpose |
|---|---|
| `skills` | print the path of the bundled skills |
| `skills --install DIR` | copy the bundled skills into `DIR` and remember `DIR` |
| `skills --refresh` | copy them again into every remembered directory |
| `update` | check PyPI now and upgrade if there is a newer version; prints the result |

## Updates

At most once a day, `mep` starts a detached background process that asks `https://pypi.org/pypi/miseenplace-cli/json` for the newest version (3 second timeout; every failure is silent). The check never delays your command. The timestamp and the result are kept in `$XDG_CACHE_HOME/miseenplace/update.json` (default `~/.cache/miseenplace`).

If a newer version exists, the installer is found from the environment's path:

| Installed with | Upgrade command |
|---|---|
| `uv tool install` (path contains `uv/tools`) | `uv tool upgrade miseenplace-cli` |
| `pipx install` (path contains `pipx/venvs`) | `pipx upgrade miseenplace-cli` |
| anything else (a virtual environment, system Python, a source checkout) | not upgraded; one line on stderr, once a day: `miseenplace X.Y.Z is available: pip install --upgrade miseenplace-cli` |

The new version applies on the next run. After an auto-update or `mep update`, skills copied with `skills --install` are re-copied. After a manual `uv tool upgrade`, run `mep skills --refresh`.

It is off when `MISEENPLACE_DISABLE_AUTOUPDATE` is set (to anything but `0`), when `auto_update = false` is in `config.toml`, when `CI` is set, when stderr is not a terminal, and always for `send`. `mep update` still runs when asked.
