# cron

Run the review on weekday evenings with your agent's non-interactive mode. Claude Code shown; any agent that runs a prompt headless works the same way.

```cron
30 21 * * 1-5 MISEENPLACE_DIR=$HOME/work-journal claude -p "Run the eod-review skill" >> $HOME/work-journal/eod.log 2>&1
0 10 * * 0 MISEENPLACE_DIR=$HOME/work-journal mep lessons check >> $HOME/work-journal/eod.log 2>&1
```

An unattended `claude -p` cannot ask for approval, so grant the tools the skill needs (`mep`, `gh`) beforehand in your agent settings. The first line is the daily review, the second is the weekly held-out check. Put the full path to `mep` and the agent in the crontab if cron cannot find them (`command -v mep`). The skill never messages anyone, so an unattended run can only change the journal and the lessons.

## Twice-a-day asks

The assistant queues questions with `mep ask` and you read one list at fixed times (more in `docs/cli.md#asks`):

```cron
0 10,16 * * 1-5 MISEENPLACE_DIR=$HOME/work-journal mep asks >> $HOME/work-journal/asks.log 2>&1
```
