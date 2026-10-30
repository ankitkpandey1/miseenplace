---
description: What data miseenplace stores, what the consent gate does and does not guarantee, and how to keep personal data out of public repositories.
---

# Security

## Credentials

Source credentials come from the environment or a rc file you control. They are never written to the data root, never printed, and hidden from `repr`. Agents are told to name keys, never print values. Keep rc files at mode 600 (a wider mode prints a warning). Only `https://` sites are accepted, and a redirect to another host is not followed with your key.

## Personal data

The data root is personal. It holds:

- the journal (who asked you for what)
- `corpus/<source>/sent.jsonl`: your own sent messages
- `style/<source>.md`: your writing style, quoting your messages
- `requests.jsonl`: what you asked your agent, verbatim
- the inbox queue: other people's messages addressed to you

Rules:

- Keep the data root out of this code repository and any public one. `.gitignore` excludes `data/`, `state/`, `config.toml`, `style/`, `corpus/` and `requests.jsonl`, and `scripts/check_no_personal.py` (a pre-commit hook and a test) fails on names that should never ship.
- With the git storage backend, these files are versioned in **your** data repository. Make it private.
- miseenplace creates these files owner-only (0600, directories 0700).
- Never paste corpus or style content into a public issue.

## The consent gate

`draft` stores exact text; `send` takes only a draft id, posts the stored text once and verifies its hash. A free-text send is refused. The hash covers the destination and the text, so editing either fails the check. Drafts expire after 24 hours (`[consent] draft_ttl_hours` in `config.toml`). If a send times out or the answer is cut off, the draft becomes `unknown` and is never resent automatically: check the thread, then run `mep send DRAFT --mark-sent` or `--mark-unsent`. `mep drafts --discard DRAFT` throws an unsent draft away; a discarded draft is never sent. A send cut off by a crash leaves a `.sending` claim with the same recovery.

What it is: a structural guard against an agent posting text you never saw, by mistake or by prompt injection through chat content.

What it is not: proof that a human said yes. An agent with shell access could run `mep send` after a draft you never approved. To close that gap, configure your agent harness to ask for confirmation on `mep send` (for example a permission rule requiring a prompt for that command). Treat the skills' "send only on an explicit yes on that exact text" as a rule your harness enforces, not just a request.

## Prompt injection

Chat messages are untrusted input. Skills tell agents to treat message text as data. The only side effect outside your machine is `send`, which needs a draft id and, with the harness rule above, your confirmation.

## Reporting

See `SECURITY.md` at the repository root.
