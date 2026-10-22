#!/usr/bin/env bash
# Record docs/assets/demo.gif from `mep demo`. Needs vhs (https://github.com/charmbracelet/vhs)
# or asciinema plus agg. Run from the repository root.
set -euo pipefail

OUT="docs/assets/demo.gif"
mkdir -p docs/assets

if command -v vhs >/dev/null 2>&1; then
  tape="$(mktemp --suffix=.tape)"
  cat >"$tape" <<'TAPE'
Output docs/assets/demo.gif
Set FontSize 16
Set Width 1100
Set Height 700
Type "mep demo"
Enter
Sleep 12s
TAPE
  vhs "$tape"
elif command -v asciinema >/dev/null 2>&1 && command -v agg >/dev/null 2>&1; then
  cast="$(mktemp --suffix=.cast)"
  asciinema rec --command "mep demo" --overwrite "$cast"
  agg "$cast" "$OUT"
else
  echo "install vhs, or asciinema plus agg, then run again" >&2
  exit 1
fi
echo "wrote $OUT"
