#!/usr/bin/env bash
# Create and configure the public GitHub repository. Idempotent; review before running.
# What it changes on GitHub: creates the repo as PUBLIC if missing; sets description and
# homepage; enables issues AND discussions; adds the topics below; creates or updates the
# Pages site (source: GitHub Actions). It pushes nothing.
# Usage: scripts/publish_github.sh --yes     (or answer y at the prompt)
# Needs: gh (authenticated), a clean commit on main. Not run by the build.
set -euo pipefail

REPO="ankitkpandey1/miseenplace"
HOME_URL="https://ankitkpandey1.github.io/miseenplace/"
ABOUT="Every ask, in its place. Agent-native CLI and Agent Skills that turn the asks, promises and nudges in your chat into a triaged inbox and audit-logged action list. For Claude Code, Codex, Gemini CLI, Cursor. Python, stdlib only."
TOPICS=(ai-agents agent-skills claude-code codex gemini-cli cursor productivity todo
  task-manager standup zulip cli python journal developer-tools inbox-zero llm automation work-log)

if [ "${1:-}" != "--yes" ]; then
  if [ ! -t 0 ]; then echo "refusing: pass --yes to confirm" >&2; exit 1; fi
  read -r -p "Create/configure PUBLIC repo $REPO on GitHub? [y/N] " answer
  [ "$answer" = "y" ] || { echo "aborted"; exit 1; }
fi

if gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo edit "$REPO" --description "$ABOUT" --homepage "$HOME_URL" \
    --enable-issues --enable-discussions
else
  gh repo create "$REPO" --public --source . --description "$ABOUT" --homepage "$HOME_URL"
  gh repo edit "$REPO" --enable-issues --enable-discussions
fi

for topic in "${TOPICS[@]}"; do
  gh repo edit "$REPO" --add-topic "$topic"
done

# Pages source = GitHub Actions (create, or update when it already exists)
gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 \
  || gh api -X PUT "repos/$REPO/pages" -f build_type=workflow >/dev/null

echo "ready: https://github.com/$REPO"
echo "next: push main, then tag v0.1.0 to publish (see docs/launch.md)"
