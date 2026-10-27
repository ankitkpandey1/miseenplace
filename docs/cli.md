---
description: Reference for every miseenplace command (mep) - journal, inbox, triage, watch, drafts, style and request log.
---

# CLI reference

`miseenplace` and `mep` are the same program. Global options go before the command: `--dir PATH`, `--source NAME`, `--version`. Environment: `MISEENPLACE_DIR`, `MISEENPLACE_SOURCE`.

Exit codes: 0 ok, 1 usage or journal error, 2 source error (network, auth). Errors are one line on stderr.

## Journal

| Command | Purpose |
|---|---|
| `add TITLE [--kind mine\|waiting\|reminder] [--who] [--eta] [--ref] [--topic] [--context] [--next] [--priority] [--session]` | new item |
| `set ID [--state] [--note] [--spent] [--tag] [--decision] [--session]` | change an item |
| `done ID [--note]` / `drop ID` | close |
| `log TEXT [--topic] [--spent]` | diary entry |
| `list [--topic] [--tag] [--sort]` | list items; `[session xN]` marks items with sessions |
| `top [-n N]` | what first |
| `brief ID_OR_TOPIC` | full view; a `session:` line per attached Claude Code session |
| `resume ITEM` | print `cd CWD && claude --resume UUID`, one line per session, newest first. ITEM is an id, a unique title substring, a PR/issue url or `repo#N` (`owner/repo#N`) matched against the item's refs. Exit 1 with a reason when the item has no session or does not match one item |
| `standup [--since yesterday]` | Did / Next / Waiting on |
| `reconcile [--apply] [--sync]` | check every open item against its sources and print one line per change, `#N current -> proposed: reason` (`0 changes` when none). Chat refs (message links, the `[inbox:src:N]` mark): you posted after the ref and nobody answered -> `waiting`; they answered after your last message -> `todo` ("new reply"). GitHub PR refs: MERGED or CLOSED -> `done`, via `gh` when installed (otherwise one skip note on stderr). Dry run by default; `--apply` writes through the same store call as `set`/`done` and adds the note `reconcile <date>: <reason>`; `--sync` (with `--apply`) runs `sync` after |
| `stats [--days N] [--delta] [--record [--focus 1-5] [--stress 1-5]]` | how long people wait on you, 7 lines. **Reply latency** (median; p90 only from 10 answered): a message that @-mentions you (group mentions like `@all` count too) or is a DM, to your next message in that thread; pings with no message of yours after them are counted as `N unanswered`. **Review turnaround**: the first review request made of you personally (team-only requests are skipped) to your first review after it, via `gh`; at most the newest 500 PRs, with a stderr note when more matched. **Review queue**: open PRs waiting on you now (asked twice when 0, since GitHub search sometimes answers 0 wrongly). Missing or failing `gh`: those rows read `n/a` and one note goes to stderr. **Closed/day**: from the journal log, an item counts once, when first set to done. Pings answered/day; focus and stress are the means of the CSV rows in the window. No data prints `n/a`, never a guess. `--delta` (takes no `--days`; `pings answered/day` is only a proxy for interruptions: it counts pings you answered, not every interruption, and is not a measure of them): the last 7 days against the 14 before, `1m vs 10m  -90%  n=3/5` per row, n being the samples on each side (lower is better for waits and stress). `--record` writes today's row of `stats.csv` (`date,focus,stress,queue`) in the data root; a rerun replaces that day's row, keeps a rating you do not give again, and keeps an earlier non-zero queue when the new one is 0. A bad CSV row (non-numeric rating, bad date, short row) is skipped with a stderr note `stats.csv:LINE: skipped, why` and is dropped when `--record` rewrites the file; a header without `date,focus,stress,queue` is an error. Takes the data-root lock; `--no-sync` skips `sync` |
| `sync` | pull, render the page, publish (storage decides) |

## Asks

The assistant queues its questions in `asks.jsonl` in the data root and you answer one short list at fixed times, instead of being interrupted. Only something that blocks another person, or a destructive action, should interrupt you at once.

| Command | Purpose |
|---|---|
| `ask DECISION --recommend X --default Y --confidence verified\|read\|guessed [--by HH:MM] [--blocks-someone \| --destructive] [--kind TYPE]` | queue a decision; prints `A3`. `--default` is what happens with no answer, `--by` the time it applies, today at HH:MM; once passed the digest marks it `(overdue)`. The decision, recommendation and default are each under 200 characters. `--kind` names a draft type (`reply-draft`, `pr-review`) so acceptance can be tracked. Takes the data-root lock; `--no-sync` skips `sync` |
| `ask --where TEXT` | replace the note the digest ends with |
| `asks [--width W]` | the digest: `3 decisions (~1 min)`, one line each `A3. DECISION  Recommended: X.  Default if no answer by 17:00: Y.  [read]`, ending `Where you were: TEXT`. Order: blocking or destructive first, then the earliest `--by` (none last), then the oldest. Cap, about 2 minutes of reading: at most 7 asks and 1800 characters of ask lines; the rest is one line `+N more`. every line is at most `--width` (default 160) characters; the decision is shortened first |
| `asks answer ID accepted\|edited\|rejected` | close an ask. `accepted` means the recommended text went out unchanged. An ask can be answered once |
| `asks report [--days N]` | for the EOD review, default 7 days: the top 3 routine types by minutes (from `mep log --tag routine:TYPE --spent 30m`), and per `--kind` how many answered asks were accepted unchanged, marked `(n<5)` when fewer than 5 were answered. The window is the last N calendar days including today. No data prints `n/a` |

Twice a day, at times you pick, an agent or you run `mep asks`. With cron, a digest you read at 10:00 and 16:00 on weekdays:

```cron
0 10,16 * * 1-5 MISEENPLACE_DIR=$HOME/work-journal mep asks >> $HOME/work-journal/asks.log 2>&1
```

Queue deadlines (`--by`) at or just after those times, so every default falls after a digest you saw.

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
| `edit MESSAGE_ID TEXT` | store a draft that replaces the text of a message you sent (`-` reads stdin). Checks read-only that the message exists and is yours, then prints the draft and the same `send: miseenplace send DRAFT_ID` line as `draft`. `send DRAFT_ID` then edits instead of posting and prints `edited SOURCE message ID`. `drafts` lists it as `EDIT of message ID`. Nothing reaches the chat except a stored draft sent with `send`, so a status message can be updated in place: `id=$(mep send D \| awk '{print $NF}')`, later `mep edit $id TEXT` and `mep send` the new draft. Exit 1 with the reason on stderr when the message is not yours (`message N is not yours`, at `edit`, no draft stored), does not exist (`no message N`) or Zulip's edit time limit has passed (`edit window expired ...`, at `send`; post a new message instead). `send` prints `sent SOURCE message ID`: the id is the last word |
| `delete MESSAGE_ID` | delete one of your own messages at once, irreversibly (no draft; this is the one write that skips `send`). Checks read-only that it is yours, then prints `deleted SOURCE message ID`. Exit 1 with the reason on stderr when it is not yours, does not exist, or the organization disallows deletion or its time limit has passed |
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

## Facts

What is true about your world, kept in `facts.jsonl` in the data root with its history. One line each: `prod-runs-on: EC2, not EKS  (user 10-22, verified 1d)`. Nothing is overwritten or resolved automatically: only an explicit `add` or `forget` changes a fact.

| Command | Purpose |
|---|---|
| `fact add KEY VALUE [--source S] [--tag T]...` | state a fact; an existing key is superseded and the old value kept with its dates. The source defaults to `unknown`; one starting with `chat`, `zulip`, `slack`, `dm` or `message` is shown as `untrusted` |
| `fact list [--tag T] [--width W]` | current facts with source and days since last verified; `0 facts` when empty |
| `fact get KEY [--history]` | one fact, or every value it had, newest first (`EKS  valid 2026-10-25 ->`) |
| `fact find WORDS` | current facts containing every word in key, value, source or tags |
| `fact verify KEY` | mark a fact as checked today |
| `fact check [--days N]` | facts not verified in N days (default 30) |
| `fact forget KEY [--why TEXT]` | close a fact and record the retraction; adding the same value later warns `retracted on <date>: <why>` (the fact is still stored) |

Values that look like secrets (`token=...`, `password: ...`, `ghp_...`, private-key headers, long random strings) are refused: store where a secret lives, not the secret. Writes take `--no-sync` and the data-root lock.

## Recipes

Named procedures kept in `recipes.jsonl` in the data root: a name, a one-line description and numbered steps with `{placeholders}`. A new recipe is `proposed` and only counts once you `approve` it. mep never runs a step; it prints them for you or an agent to carry out.

| Command | Purpose |
|---|---|
| `recipe add NAME --desc D --step S [--step S]... [--tag T]...` | propose a recipe. On an existing name the new version is also `proposed`; the approved one stays in use until `approve` |
| `recipe approve NAME` | put the proposed version in use (its use count carries over) |
| `recipe list [--all] [--tag T] [--width W]` | approved recipes, most recently `ran` first, never-run last: `stop-server  turn a dev box off  (ran 3, last 10-23)`. `--all` adds proposed ones tagged `(proposed)`; `0 recipes` when empty |
| `recipe show NAME [--set K=V]... [--proposed]` | numbered steps with `{K}` filled. An unfilled placeholder is named on stderr and the exit code is 1; nothing is guessed. `--proposed` shows the version awaiting approval |
| `recipe ran NAME` | count one use |
| `recipe forget NAME [--why TEXT]` | retire every version and record why; `show`, `ran` and `approve` then report the reason |

Placeholders are `{name}` with letters, digits and `_`. Write `{{` and `}}` for literal braces (`{{x}}` prints `{x}`); `${HOME}`, `{}` and `{a-b}` are left as written. A `--set` key no step uses gets a note on stderr (exit stays 0); `--set k=v=w` sets `k` to `v=w` and `--set k=` sets it empty.

Steps, descriptions and tags that look like secrets are refused, as for facts. Writes take `--no-sync` and the data-root lock.

## Recall

One read at the start of a task: the lessons, facts and recipes most relevant to some text, inside a size budget. `mep recall "stop koyal and challenger"` prints

```
recipe stop-koyal: turn a dev box off via mm-dev-bot
fact koyal-apiv2-port: 8260 (user 10-20, verified 3d)
lesson when posting to Zulip -> do full PR URLs, not #42
3 of 41 items, 212 chars
```

| Command | Purpose |
|---|---|
| `recall TEXT [--max-chars N] [--kind lesson\|fact\|recipe]` | rank and print; `--max-chars` defaults to 1200 and cuts whole lines, `--kind` searches one store |

Only current lessons, current facts and approved recipes are searched; superseded, forgotten, proposed and retired ones never appear. Ranking is BM25 (k1 1.2, b 0.75) in pure Python, no embeddings, no network. Words are lowercase runs of letters and digits, so `8260`, `koyal` and `pr7101` match exactly. A name, key or tag word counts twice a body word (lesson when/do/not, fact value and source, recipe description and steps). A query word that no item contains is replaced by the closest known word by character-trigram similarity (at least 0.5): `challnger` finds `challenger`, `deploys` finds `deploy`. Synonyms are not guessed; add a tag. A recipe used recently or a fact verified recently scores up to 1.2x (the bonus halves every 30 days); a lesson gets 1% per vote, at most 10%.

The footer is `N of M items, C chars`: N shown out of M searched. When the budget cut matches off it adds `, K left out`. No match prints `0 of M items` and exits 0. Every query is appended to `recall.jsonl` in the data root as `{ts, query, kind, returned}` (keys like `fact:koyal-apiv2-port`), so you can later check whether the item you needed came back. Like the other read commands, `recall` first refreshes the data root from its remote and falls back to the local copy, with a note on stderr, when offline. A blank query is refused (exit 2); a word containing a digit only matches exactly.

## Setup

`init` creates the data root and `config.toml`; `demo` runs the guided tour.

| Command | Purpose |
|---|---|
| `skills` | print the path of the bundled skills |
| `skills --install DIR` | copy the bundled skills into `DIR` and remember `DIR` |
| `skills --refresh` | copy them again into every remembered directory |
| `update` | check PyPI now and upgrade if there is a newer version; prints the result |

## Updates

At most once an hour, `mep` starts a detached background process that asks `https://pypi.org/pypi/miseenplace-cli/json` for the newest version (3 second timeout; every failure is silent). The check never delays your command. The timestamp and the result are kept in `$XDG_CACHE_HOME/miseenplace/update.json` (default `~/.cache/miseenplace`).

If a newer version exists, the installer is found from the environment's path:

| Installed with | Upgrade command |
|---|---|
| `uv tool install` (path contains `uv/tools`) | `uv tool upgrade miseenplace-cli` |
| `pipx install` (path contains `pipx/venvs`) | `pipx upgrade miseenplace-cli` |
| anything else (a virtual environment, system Python, a source checkout) | not upgraded; one line on stderr, once a day and only when stderr is a terminal: `miseenplace X.Y.Z is available: pip install --upgrade miseenplace-cli` |

The new version applies on the next run. After an auto-update or `mep update`, skills copied with `skills --install` are re-copied. After a manual `uv tool upgrade`, run `mep skills --refresh`.

It is off when `MISEENPLACE_DISABLE_AUTOUPDATE` is set (to anything but `0`), when `auto_update = false` is in `config.toml`, when `CI` is set, and always for `send`. `mep update` still runs when asked.
