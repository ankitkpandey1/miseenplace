---
description: Launch checklist and drafts for announcing miseenplace.
---

# Launch checklist

Nothing here is automated. Do it by hand when ready.

## Before

- [ ] `scripts/publish_github.sh` run, repository public, Pages enabled
- [ ] Tag `v0.1.0` pushed; release and PyPI publish green
- [ ] Record a demo with `scripts/record_demo.sh` (writes the gif into `docs/assets/`), then add an image link to it under the badges in README.md
- [ ] `docs/assets/social-preview.png` uploaded in repository settings
- [ ] `uvx --from miseenplace-cli mep demo` works from PyPI on a clean machine

## Lists to submit to

- [ ] awesome-claude-code
- [ ] awesome-agent-skills
- [ ] awesome-cli-apps
- [ ] The Agent Skills and Claude Code plugin directories

## Show HN draft

Title: Show HN: miseenplace - the asks and promises from your chat, laid out before the day starts

Body: I kept losing track of what people asked me for in chat and what I promised. miseenplace is a small Python CLI (stdlib only) that turns chat into a triaged inbox and an audit-logged action list. It is built for agents: the CLI does the deterministic work, so a small model can drive it, and a consent gate means an agent can only send a draft a human has seen. Zulip and a demo source ship today; sources and storage backends are plugins. `uvx --from miseenplace-cli mep demo` runs it with no account.

## Reddit draft (r/ClaudeAI, r/commandline)

I built a CLI plus agent skills that triage chat asks into a todo list and drafted replies. Agents never post free text: they store a draft and a human approves its exact text. Stdlib only, Apache-2.0. Looking for feedback on the source and storage plugin contracts.
