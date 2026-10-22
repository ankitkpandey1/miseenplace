---
name: inbox-watch
description: Watch the user's chat in the background and surface new asks, promises and nudges as they arrive. Use for "watch my inbox", "tell me when someone asks me something", "keep an eye on chat", or to re-arm after a watcher exits.
---

# inbox-watch

`mep watch` blocks until at least one new actionable item exists, prints one compact line per item, and exits 0. The exit wakes you. Errors never print: they go to `state/watch.log` in the data root and are retried.

## Steps

1. Start it as a background shell command: `mep watch` (add `--cursor NAME` to keep your position separate from other consumers; default `watcher`).
2. When it exits, its output is the lines to triage. Run the `inbox-triage` skill on them.
3. Notify the human only if a reply or a decision is needed. Journal items need no interruption.
4. Start `mep watch` again. Always re-arm, even after an error.

## Rules

- Never poll by hand and never read raw chat to find new items.
- Empty output on exit means nothing needs you: re-arm silently.
- Exit code 3 only appears with `--max-cycles`; treat it like empty output.
- The first run on a new cursor sets a baseline and reports nothing from before.
- For machines without an agent loop, schedule `mep watch --once`: see `references/`.

## References

- `references/claude-code.md` for background shells in Claude Code
- `references/cron.md`, `references/systemd.md`, `references/launchd.md` for scheduled runs
