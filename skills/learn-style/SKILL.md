---
name: learn-style
description: Learn how the user writes from their own sent chat messages and save a style file that drafting reads. Use for "learn my style", "write like me", "my drafts do not sound like me", or before drafting when `mep style show` says there is no style yet.
---

# learn-style

The style file lives in the data root at `style/<source>.md`. The CLI archives the user's sent messages; you read them and write the file.

## Steps

1. `mep style learn --days 90` fetches the user's own messages into the archive (deduplicated by message id). Use `mep --source NAME style learn ...` for a non-default source.
2. `mep style sample` prints the most recent sent messages. `mep style show` prints the existing file: read it first and keep corrections the user gave earlier.
3. Write the file: describe patterns, each with one short real quote.
   - Acks: how short, the exact words
   - Asks and nudges: shape, greeting or none
   - Mentions: where they go, how people are addressed
   - Explanations and pushback: length, reasons or not
   - Register: case, punctuation, emoji, sign-offs
   - Never: assistant phrases ("I hope this helps"), and typos that are not style
4. Save it: `mep style set -` with the text on stdin. Keep it under 60 lines. Start with: `Write as the user, not as an assistant. Shortest form that carries the point.`
5. Show the user the file and ask what is wrong. Only the user's corrections beat your reading.

## Rules

- Quote only the user's own messages, never other people's.
- The file and archive are personal data. Never copy them into a code repository or any public place.
- Before every draft, run `mep style show` and write in that voice. Showing the draft and waiting for the human's yes still applies.
