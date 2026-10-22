# miseenplace

**Every ask, in its place.** An agent-native CLI and set of skills that lay out the asks, promises and nudges from your chat before the day starts: a chef's mise en place for your workday, for Claude Code, Codex, Gemini CLI and Cursor.

[![PyPI](https://img.shields.io/pypi/v/miseenplace-cli)](https://pypi.org/project/miseenplace-cli/)
[![CI](https://github.com/ankitkpandey1/miseenplace/actions/workflows/ci.yml/badge.svg)](https://github.com/ankitkpandey1/miseenplace/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/miseenplace-cli)](https://pypi.org/project/miseenplace-cli/)

## Paste this into your agent

```text
Install miseenplace with `uv tool install miseenplace-cli`, run `miseenplace skills` to find the
bundled agent skills, copy them into your skills directory, then run the setup skill and
follow it. Ask me only for things you cannot find out yourself.
```

No account? `uvx --from miseenplace-cli mep demo` runs a two-minute tour on fictional data.

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

## Why

Chat is where work is asked for and promised, and it scrolls away. To-do apps start empty and trust you to copy things in. miseenplace reads your chat, keeps only what needs you, and turns it into an audit-logged action list your agent can drive.

- **One line per ask.** `mep inbox` shows `id · who · where · ask · action`, nothing else. Your own earlier answers hide the asks you already handled.
- **One call per decision.** `mep triage 12 --todo` files the item with link, topic and context filled in.
- **Agents cannot post free text.** `draft` stores the exact words; `send` takes only a draft id, after you said yes to that text.
- **Cheap to run.** The CLI does the deterministic work, so a small model can drive it. A tested output budget keeps a ten-message loop near 500 tokens.
- **Your data, your storage.** Local files, a private git repository, or a backend you write in 30 minutes.
- **Stdlib only.** No runtime dependencies. Python 3.11 to 3.14.

| | Plain to-do app | miseenplace |
|---|---|---|
| Captures asks from chat | you copy them in | reads the chat |
| Remembers you already answered | no | hides answered asks |
| History | edits overwrite | append-only audit log, replay-validated |
| Agent can drive it | via a UI or an API | terse CLI plus short skills |
| Agent can post for you | n/a | only a stored draft you approved |
| Where data lives | the vendor | files you own, optionally in git |

## Daily use

```bash
mep inbox                               # only what needs you
mep triage 12 --todo --eta fri          # journal it
mep triage 13 --reply-draft "Thursday"  # stores the text, posts nothing
mep send d1a2b3c4e                      # after your yes on that exact text
mep top                                 # what first
mep standup --since yesterday           # Did / Next / Waiting on
mep watch                               # block until something needs you
```

## Skills

`skills/` follows the Agent Skills standard and also installs as a Claude Code plugin (`.claude-plugin/`).

| Skill | Does |
|---|---|
| `setup` | install, connect a source, verify |
| `journal` | keep items and the work diary |
| `inbox-triage` | inbox into journal items and drafted replies |
| `inbox-watch` | background watcher; wakes the agent on new asks |
| `learn-style` | learns how you write so drafts sound like you |

## Storage

```toml
# config.toml in the data root
[storage]
backend = "git"      # local (default) | git | "package.module:ClassName"
remote = "git@host:me/my-data.git"
```

The git backend pulls before reads and commits and pushes after writes; a failed push is an error. See [writing a storage backend](docs/writing-a-storage-backend.md).

## Sources

Zulip and a demo source ship today. A new one takes about 30 minutes: [writing a source](docs/writing-a-source.md).

## Your data is personal

The journal, your sent-message archive, your style file and a verbatim log of your requests live in your data root, never in this repository. Read [security](docs/security.md), especially what the consent gate does not guarantee.

## Docs

[Quickstart](docs/quickstart.md) · [Concepts](docs/concepts.md) · [CLI](docs/cli.md) · [Data model](docs/data-model.md) · [Sources](docs/sources.md) · [Agent cost](docs/agent-cost.md) · [Security](docs/security.md) · [Contributing](CONTRIBUTING.md)

Apache-2.0.
