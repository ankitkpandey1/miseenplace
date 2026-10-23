# Changelog

## Unreleased

- `mep recall TEXT [--max-chars N] [--kind lesson|fact|recipe]`: one relevance-ranked read across current lessons, current facts and approved recipes (BM25, pure Python, no dependency; names and tags weigh 2x a body word; a small recency/votes boost of at most 1.2x). A query word with no exact hit maps to the closest known word by trigram similarity (`challnger` finds `challenger`, `deploys` finds `deploy`). One line per item, best first, cut at `--max-chars` (default 1200), footer `N of M items, C chars` plus `K left out`. Each query is appended to `recall.jsonl` (query, returned keys) to check later whether recall found what was needed. The bundled `journal` skill runs it at the start of a task, after `note-request`, so the agent starts from what is already known. `eod-review` is unchanged: it needs every lesson with its id to dedupe and vote, not the relevant few. A query word with a digit is never fuzzy-matched; a blank query is refused (exit 2).
- `mep recipe add|approve|list|show|ran|forget`: named procedures (description + steps with `{placeholders}`) in `recipes.jsonl`. `add` makes a recipe `proposed`; adding an existing name proposes a new version while the approved one stays in use until `approve`. `list` shows approved recipes by last `ran` (never-run last), `--all` adds proposed ones tagged `(proposed)`. `show NAME --set k=v` prints numbered steps; an unfilled `{x}` is listed on stderr and the exit code is 1, never guessed. `forget --why` retires it with a retraction record; steps that look like secrets are refused. mep only prints steps, it never runs them. The issue's `recall` has no command yet, so `list` is the only default filter.
- `mep today`: a bare acknowledgement ("ok", "thanks 🙏", "np") after your reply no longer marks the thread `awaiting you`; it is `fyi`. An ack that answers your own question ("should I merge?" -> "sure") still waits on you.
- `mep fact add|list|get|find|verify|check|forget`: facts with history in `facts.jsonl`. Adding an existing key supersedes the old value and keeps it (`get KEY --history` shows valid dates); `forget --why` records a retraction, so adding the same value again warns; secret-looking values are refused; the source defaults to `unknown` and a chat-sourced fact is marked `untrusted`; `check [--days N]` (default 30) lists facts not verified in N days.

## 0.3.0

- `mep ask DECISION --recommend X --default Y --confidence verified|read|guessed [--by HH:MM] [--blocks-someone|--destructive] [--kind TYPE]` queues a decision in `asks.jsonl`; `mep ask --where TEXT` sets the note the digest ends with. `mep asks` prints the ranked digest (blocking or destructive first, then the earliest deadline), capped at 7 asks and 1800 characters (about 2 minutes), with `+N more` and `Where you were: ...`. `mep asks answer ID accepted|edited|rejected` closes one; `mep asks report [--days N]` gives the top 3 routine types by minutes (`mep log --tag routine:TYPE --spent`) and the share of drafts accepted unchanged per `--kind`. Twice-a-day cron example in `docs/cli.md`. The `eod-review` skill reports both and states the 70%-for-a-week bar for proposing (never enabling) auto-send. The minimum of 5 answered drafts for that bar (`(n<5)` in the report) is an addition beyond the issue. `pings answered/day` in `stats --delta` is a proxy for interruptions, not a measure.
- `mep stats [--days N] [--delta] [--record [--focus 1-5] [--stress 1-5]]`: how long people wait on you. Reply latency (median, p90) from mentions and DMs to your reply, review turnaround and review queue via `gh`, items closed per day, pings answered per day, focus and stress from one CSV row a day (`stats.csv`; `--record` replaces that day's row). Missing data is `n/a`, never estimated; no `gh` means those rows are skipped with one stderr note. `--delta` compares the last 7 days with the 14 before. The `eod-review` skill records the day and shows the delta on Fridays.
- `eod-review` skill (bundled): closes the day with `today` and `reconcile`, learns only from real corrections, never messages anyone. Cron example in `docs/quickstart.md`.
- `mep lessons list|add|vote|case|check`: lessons as `when -> do, not <wrong>` with evidence and votes (2 at the start, 0 deletes), plus a weekly held-out replay of recorded corrections that restores the last good lesson set if the new one catches fewer. One line per lesson, `--width` caps it.

- `mep today [--days N] [--width W]`: the threads of the day that involve you (an @-mention, a DM or a post of yours), one compact block each with the last 3 messages and a state tag: `awaiting you`, `you replied last` or `fyi`. Awaiting-you comes first; answered threads show 2 messages, awaiting ones 3, links and markdown are stripped. About a tenth of the size of `digest`. `compact` also drops a quote-reply's "said" header, which helps `inbox` too.
- `mep reconcile [--apply] [--sync]`: checks every open item against its sources and prints one line per proposed change (`#12 todo -> waiting: you posted 1043; no reply since`; `0 changes` when none). Chat refs: you posted and they did not answer -> `waiting`; they answered after you -> `todo`. GitHub PR refs (needs `gh`; skipped with one note without it): merged or closed -> `done`. Dry run by default; `--apply` writes through `set`/`done` with a note `reconcile <date>: <reason>`. Sources gain an optional `message(id)` lookup (Zulip, demo).

## 0.2.0

- Auto-update, like Claude Code: at most once a day `mep` checks PyPI in a detached background process and, when it was installed with `uv tool` or `pipx`, upgrades itself. The new version applies on the next run. Any other install gets one line on stderr, once a day. Opt out with `MISEENPLACE_DISABLE_AUTOUPDATE=1` or `auto_update = false` in `config.toml`; it also stays off in CI, without a terminal and for `send`.
- `mep update`: check and upgrade now.
- `mep skills --install DIR` copies the bundled skills into `DIR` and remembers it; an upgrade refreshes the copies (`mep skills --refresh`). `mep skills` still prints the path.
- README: demo GIF (`docs/assets/demo.tape`, `scripts/record_demo.sh`), sample inbox output, a how-it-works diagram, status and roadmap, updates and uninstall. `scripts/build_llms_full.py` rebuilds `llms-full.txt`.

## 0.1.1

- Published on PyPI as `miseenplace-cli` (the name `miseenplace` is too close to an existing project). Commands stay `miseenplace` and `mep`.

## 0.1.0

First release.

- Journal: add, set, done, drop, log, list, top, brief, standup, sync; append-only audit log with replay validation; rendered page.
- Inbox: `inbox`, `triage`, `watch` with per-consumer cursors and one compact line per actionable item.
- Sources: Protocol contract; Zulip and a file-backed demo source.
- Consent gate: `draft` stores exact text, `send` takes only a draft id and delivers once.
- Pluggable storage: `local`, `git` and custom backends via `module:Class` or the `miseenplace.storage` entry point.
- Your words as data: `note-request` log, `style` file and sent-message corpus.
- Agent Skills: setup, journal, inbox-triage, inbox-watch, learn-style; Claude Code plugin manifest.
- `mep demo` guided tour; docs site; stdlib-only runtime, Python 3.11 to 3.14.
