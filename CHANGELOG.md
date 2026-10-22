# Changelog

## Unreleased

- `mep today [--days N] [--width W]`: the threads of the day that involve you (an @-mention, a DM or a post of yours), one compact block each with the last 3 messages and a state tag: `awaiting you`, `you replied last` or `fyi`. Awaiting-you comes first; answered threads show 2 messages, awaiting ones 3, links and markdown are stripped. About a tenth of the size of `digest`. `compact` also drops a quote-reply's "said" header, which helps `inbox` too.

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
