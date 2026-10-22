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
| `reconcile [--apply] [--sync]` | check every open item against its sources and print one line per change, `#N current -> proposed: reason` (`0 changes` when none). Chat refs (message links, the `[inbox:src:N]` mark): you posted after the ref and nobody answered -> `waiting`; they answered after your last message -> `todo` ("new reply"). GitHub PR refs: MERGED or CLOSED -> `done`, via `gh` when installed (otherwise one skip note on stderr). Dry run by default; `--apply` writes through the same store call as `set`/`done` and adds the note `reconcile <date>: <reason>`; `--sync` (with `--apply`) runs `sync` after |
| `stats [--days N] [--delta] [--record [--focus 1-5] [--stress 1-5]]` | how long people wait on you, 7 lines. **Reply latency** (median; p90 only from 10 answered): a message that @-mentions you (group mentions like `@all` count too) or is a DM, to your next message in that thread; pings with no message of yours after them are counted as `N unanswered`. **Review turnaround**: the first review request made of you personally (team-only requests are skipped) to your first review after it, via `gh`; at most the newest 500 PRs, with a stderr note when more matched. **Review queue**: open PRs waiting on you now (asked twice when 0, since GitHub search sometimes answers 0 wrongly). Missing or failing `gh`: those rows read `n/a` and one note goes to stderr. **Closed/day**: from the journal log, an item counts once, when first set to done. Pings answered/day; focus and stress are the means of the CSV rows in the window. No data prints `n/a`, never a guess. `--delta` (takes no `--days`): the last 7 days against the 14 before, `1m vs 10m  -90%  n=3/5` per row, n being the samples on each side (lower is better for waits and stress). `--record` writes today's row of `stats.csv` (`date,focus,stress,queue`) in the data root; a rerun replaces that day's row, keeps a rating you do not give again, and keeps an earlier non-zero queue when the new one is 0. A bad CSV row (non-numeric rating, bad date, short row) is skipped with a stderr note `stats.csv:LINE: skipped, why` and is dropped when `--record` rewrites the file; a header without `date,focus,stress,queue` is an error. Takes the data-root lock; `--no-sync` skips `sync` |
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

## Lessons

Lessons from real corrections, kept in `lessons.jsonl` in the data root. One line each: `L3 v2 when <situation> -> do <action>, not <wrong action>`. A new lesson starts at 2 votes, a vote up or down moves it by one, at 0 it is deleted. `list` prints `0 lessons` when empty.

| Command | Purpose |
|---|---|
| `lessons list [--full] [--width W]` | one line per lesson, cut at `--width` (default 120); `--full` adds the evidence |
| `lessons add --when T --do A --not W --evidence E [--pattern RE]` | save a lesson; `--pattern` is a regex that matches the wrong action, which makes the lesson replayable |
| `lessons vote ID up\|down` | re-confirm or contradict; prints `L3 v3` or `L3 deleted` |
| `lessons case --wrong TEXT --fine TEXT` | record a past correction in `corrections.jsonl` |
| `lessons check` | replay every correction: it passes when some lesson pattern matches the `wrong` text and none matches the `fine` text. Compares with the last good set (`lessons.good.jsonl`): fewer passes restores that set (`reverted: 0/1 < 1/1; removed L2; changed L1`, naming each lesson that was removed, brought back or changed by votes), otherwise the current set becomes the good one (`ok: 4/5 corrections caught, 1 lessons unchecked`) |

Writes take `--no-sync` and the data-root lock, so a scheduled `check` never overlaps an `add`. The first `add` or `vote` saves the current set as the baseline. A `--pattern` that matches the empty string is refused. A value starting with a dash needs `=`: `--not=-x`. A garbled line stops with its file and line number. Lessons without a pattern are never replayed; `check` counts them as unchecked.

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
