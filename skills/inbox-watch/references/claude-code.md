# Claude Code and other agent harnesses

Run the watcher as a background shell command and let its exit wake the agent.

```bash
mep watch --interval 300
```

In Claude Code, start it with the Bash tool's `run_in_background` option. When the process exits, the harness re-invokes the agent with its output. Triage the lines, then start it again.

The watcher is quiet on purpose: network and auth errors are appended to `<data root>/state/watch.log`, and the process keeps retrying. Check that file when the watcher seems silent for a long time: `tail state/watch.log`.

Use a separate `--cursor` per agent session, so two sessions never steal each other's items. All cursors share one inbox queue, so an item triaged by one is hidden for all.

Other harnesses (Codex, Gemini CLI, Cursor): any way of running a shell command in the background and being told when it exits works the same way.
