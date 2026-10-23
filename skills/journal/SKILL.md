---
name: journal
description: Keep the user's action items and work diary with the miseenplace CLI. Use for "add to my list", "remind me", "what's on my plate", "what next", "mark X done", "log what I did", "standup", or when someone asks the user for something, the user promises something, or the user waits on someone.
---

# journal

One tool: `mep` (alias of `miseenplace`). The CLI does the bookkeeping; you only choose the command.

## At the start of any task from the user

```bash
mep note-request "<the user's exact words>"
```

Verbatim, never a summary: summaries drift, the user's words do not.

```bash
mep recall "<the user's words>"
```

Reads back the few lessons, facts and recipes that match this task, so you start from what is already known instead of loading every file.

## Which command

| Situation | Command |
|---|---|
| Someone asked the user for X | `mep add "X" --who NAME --eta DATE --ref LINK --topic T --context "why, who asked" --next "first step"` |
| User waits on someone | `mep add "X" --kind waiting --who NAME --eta NUDGE_DATE` |
| User did something | `mep log "what" --topic T --spent 30m` |
| Find an item id | `mep list` (add `--all` for closed) |
| Remind the user of something | `mep add "X" --kind reminder --eta DATE` |
| Progress on an item | `mep set ID --state doing --note "..." --spent 30m` |
| User says it is done, or you see the merge or approval | `mep done ID --note "..."` |
| User drops it | `mep drop ID` |
| What first? | `mep top` |
| Everything about one item or topic | `mep brief ID_OR_TOPIC` |
| Which open items went stale (replied in chat, PR merged) | `mep reconcile` (dry run, one line per change); show it, then `mep reconcile --apply` only on the user's yes; add `--sync` to publish |
| Stand-up | `mep standup --since yesterday` |

## Rules

- Always fill `--topic`, `--context` and `--next` when adding. Set `--priority p0..p3` only when the user says so.
- ETA: `YYYY-MM-DD`, `MM-DD`, `today`, `tomorrow`, `+3d`, `mon`..`sun`, `none`.
- An ID may be a few words of the title instead (`mep done "release notes"`) when exactly one open item matches; otherwise the error asks for the id.
- Never close an item on a guess. Only on the user's word or evidence you can see.
- A failed write writes nothing; the error names the fix. Never edit files in the data root by hand.
- A stand-up is raw material. To post it in the user's voice, use the `learn-style` skill first.
