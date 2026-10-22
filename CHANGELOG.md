# Changelog

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
