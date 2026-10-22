---
name: inbox-triage
description: Triage the user's chat inbox into journal items and drafted replies. Use for "check my messages", "what do I owe people", "anything for me", "triage my inbox", "reply to X", or when a watcher exits with new lines.
---

# inbox-triage

The CLI classifies and does the bookkeeping. You decide only what the human decides. Never read raw chat when `mep inbox` will do.

## Loop

1. `mep inbox` prints one line per actionable item: `id · who · where · ask · action`. Silence means nothing to do.
2. For each line, run the command from this table. Override the suggested action only when the ask text clearly says otherwise.

| action | Command |
|---|---|
| todo | `mep triage ID --todo` (add `--eta DATE` only if the ask names a date) |
| waiting | `mep triage ID --waiting --eta +3d` (nudge date; default `+3d`) |
| ignore | `mep triage ID --ignore` |
| reply | `mep style show`, then `mep triage ID --reply-draft "TEXT"` |

3. Every ask the user must act on is now a journal item. Stop there unless a reply is needed.

## Replying

- `--reply-draft` stores the exact text and posts nothing. Show the human the printed draft.
- Send with `mep send DRAFT_ID` only after an explicit yes on that exact text. A yes to something else, or a changed text, is not a yes: make a new draft and ask again.
- `send` takes only a draft id. Never try to send free text.
- Write in the user's voice from `mep style show`. If there is no style file, use `learn-style` first.

## Before calling anything pending

For a daily review ("what happened today", "what is waiting on me"), `mep today [--days N]` lists the threads that involve the user with a state tag per thread (`awaiting you`, `you replied last`, `fyi`); it shows their own replies too. Use it instead of `digest`.

The user's own messages never appear in `inbox`. Before telling the human that something is still pending, re-read the thread before calling anything pending: `mep thread --channel C --topic T` (or `--dm NAME`). The user may have answered already.

## Rules

- Never message anyone other than through a draft the human approved.
- If `mep` prints a one-line error, tell the human the error. Do not retry in a loop and do not work around it.
- More detail: `references/decisions.md`.
