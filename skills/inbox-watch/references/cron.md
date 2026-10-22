# cron

`mep watch --once` polls one time and exits 0 whether or not anything arrived. Pipe the output to a notifier; empty output means nothing happened.

```cron
*/10 * * * * out=$(mep watch --once --cursor cron); [ -n "$out" ] && notify-send "miseenplace" "$out"
```

On macOS replace `notify-send` with `osascript -e 'display notification "..." with title "miseenplace"'`. Put the full path to `mep` in the crontab if cron cannot find it (`command -v mep`).

A cron run does not triage anything: it only tells the human. Triage happens when an agent runs the `inbox-triage` skill.
