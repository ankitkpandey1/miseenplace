---
name: setup
description: Install and configure miseenplace, connect a chat source and verify it. Use right after installing, or for "set up miseenplace", "connect my chat", "get me started".
---

# setup

Goal: `mep inbox` works against the user's own chat. Ask only what you cannot find out. Never print, echo or store a credential in the conversation: name keys only.

## Steps

1. Install if missing: `command -v mep || uv tool install miseenplace-cli` (or `pipx install miseenplace-cli`), then `mep --version`.
2. Skills directory: Claude Code `~/.claude/skills/`, Codex `~/.agents/skills/`, Gemini CLI `~/.gemini/skills/`, Cursor `~/.cursor/skills/` (else check your tool's docs). Copy: `mep skills --install DIR` (it remembers DIR and refreshes the copies on update). Claude Code plugin: `/plugin marketplace add ankitkpandey1/miseenplace`, then `/plugin install miseenplace@miseenplace`.
3. Show what it does with no account: `mep demo`. To keep the sandbox: `mep demo --keep-dir DIR`, then `export MISEENPLACE_DIR=DIR` and use `mep --source demo inbox --cursor demo`. No Zulip? Stop here: the demo is a full source.
4. Create the data root: `mep init`. The root is `$MISEENPLACE_DIR` or `~/.local/share/miseenplace`.
5. Storage. Default is local files. To sync, ask for a **private** git remote, then in `config.toml`:
   ```toml
   [storage]
   backend = "git"
   remote = "git@host:me/my-miseenplace-data.git"
   ```
6. Source `zulip`: env `ZULIP_EMAIL`, `ZULIP_API_KEY`, `ZULIP_SITE` (https), or a zuliprc at `~/.zuliprc` or `$MISEENPLACE_ZULIP_RC`:
   ```ini
   [api]
   email=you@example.com
   key=YOUR_PERSONAL_API_KEY
   site=https://yourorg.zulipchat.com
   ```
   Write the file yourself with the key the user pasted, run `chmod 600` on it, never echo the key back. Then `default_source = "zulip"` in `config.toml`, and under `[sources.zulip]` set `noise_streams` (ask which channels are alerts or feeds).
7. Verify: `mep inbox` (first run sets a baseline, prints nothing), then `mep users PART_OF_NAME`. If it fails, the error is one line: a missing key means fix the env or zuliprc; "unreachable" means check the site URL and network; a permissions warning means `chmod 600`. Fix, rerun, and tell the user plainly if it still fails.
8. Finish: run `learn-style`; offer `inbox-watch`.

## Tell the user two rules you follow

- You re-read a thread before calling anything pending.
- You never post anything without showing the exact draft and getting a yes on it.
