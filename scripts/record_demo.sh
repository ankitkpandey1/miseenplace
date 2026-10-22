#!/usr/bin/env bash
# Re-record docs/assets/demo.gif from the real `mep demo` (fictional data, no account).
# Needs vhs (https://github.com/charmbracelet/vhs) with ttyd, ffmpeg and a Chrome/Chromium on PATH.
# Run from the repository root with the project installed (uv sync).
set -euo pipefail

command -v vhs >/dev/null 2>&1 || { echo "install vhs (plus ttyd and ffmpeg), then run again" >&2; exit 1; }
PATH="$PWD/.venv/bin:$PATH" vhs docs/assets/demo.tape
echo "wrote docs/assets/demo.gif"
