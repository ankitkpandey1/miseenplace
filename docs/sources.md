---
description: The source contract for miseenplace and how the Zulip and demo sources are configured.
---

# Sources

A source implements this Protocol (`miseenplace.sources.base.Source`):

| Method | Meaning |
|---|---|
| `new(cursor, init=False)` | messages for me since this consumer's position; excludes my own |
| `thread(target, limit)` | a conversation, including my messages |
| `draft(target, text)` | store the exact text, return a draft |
| `send(draft_id)` | post a stored draft |
| `users(query)` | find people |

A source may also implement `digest(days, include_noise, only_mine)`; `style learn` needs it, and `edit(message_id, text)` (check the message is mine, store a draft whose target has `edit_of` set and return it; `_deliver` then applies the edit instead of posting, raising `EditRefused` when it definitely did not apply); `mep edit` needs it.

Because `new` excludes your own messages, anything that reads it must re-read the thread before calling something pending.

## Zulip

Credentials, in order: environment (`ZULIP_EMAIL`, `ZULIP_API_KEY`, `ZULIP_SITE`), then a zuliprc file (`$MISEENPLACE_ZULIP_RC` or `~/.zuliprc`). Use your own personal API key. The key never appears in output or in `repr`.

```toml
[sources.zulip]
noise_streams = ["alerts"]       # skipped unless a message mentions you
noise_topics = ["Build Failure"]
participation_days = 14          # threads you took part in stay visible
```

Network failures raise a one-line error ("zulip unreachable; will retry"). The watcher logs and retries them.

## Demo

File-backed JSON under `<root>/demo/`. It is the reference implementation: read `sources/demo.py` first when writing your own.

## Your own

[Writing a source](writing-a-source.md).
