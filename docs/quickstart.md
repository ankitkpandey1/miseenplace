---
description: Install miseenplace, try the two-minute demo, connect your chat and triage your first inbox with an AI agent or by hand.
---

# Quickstart

## 1. Try it without an account

```bash
uv tool install miseenplace      # or: pipx install miseenplace
mep demo
```

`mep demo` runs a guided tour on fictional data in a temporary directory: an inbox with a few asks, triage into journal items, a drafted reply, a refused send of free text, and a stand-up. Nothing leaves your machine.

## 2. Let your agent set it up

Paste this into Claude Code, Codex, Gemini CLI or Cursor:

```text
Install miseenplace with `uv tool install miseenplace`, run `miseenplace skills` to find the
bundled agent skills, copy them into your skills directory, then run the setup skill and
follow it. Ask me only for things you cannot find out yourself.
```

### Where the skills go

| Tool | Skills directory (user level) | Project level |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| Codex | `~/.agents/skills/` | `.agents/skills/` |
| Gemini CLI | `~/.gemini/skills/` (alias `~/.agents/skills/`) | `.gemini/skills/` |
| Cursor | `~/.cursor/skills/` (also reads `~/.agents/skills/`) | `.cursor/skills/` |

Paths are from each tool's documentation at the time of writing; if your version differs, check your tool's docs.
Copy the bundled skills with the directory for your tool (Claude Code shown):

```bash
mkdir -p ~/.claude/skills && cp -r "$(mep skills)"/* ~/.claude/skills/
```

Claude Code can also install it as a plugin, with no copying:

```text
/plugin marketplace add ankitkpandey1/miseenplace
/plugin install miseenplace@miseenplace
```

## 3. Or set it up by hand

```bash
mep init                    # creates the data root and a config.toml template
```

Edit `config.toml` in the data root (`$MISEENPLACE_DIR`, default `~/.local/share/miseenplace`):

```toml
default_source = "zulip"

[sources.zulip]
noise_streams = ["alerts"]   # skipped unless a message mentions you
```

Zulip credentials come from `ZULIP_EMAIL`, `ZULIP_API_KEY`, `ZULIP_SITE`, or a zuliprc file. See [Sources](sources.md).

## 4. Daily use

```bash
mep inbox                     # only what needs you, one line each
mep triage 12 --todo          # journal it
mep triage 13 --reply-draft "Thursday"   # stores the text, posts nothing
mep send d1a2b3c4e            # only after you said yes to that exact text
mep top                       # what first?
mep standup --since yesterday
```

The first `mep inbox` on a new cursor sets a baseline and prints nothing. Newer messages show from then on.

## 5. Keep it running

`mep watch` blocks until something needs you, prints it and exits. See the `inbox-watch` skill, or schedule `mep watch --once` with cron, systemd or launchd (recipes in `skills/inbox-watch/references/`).

## 6. Sync across machines

Add a private git remote with the git storage backend: [Concepts](concepts.md#storage).
