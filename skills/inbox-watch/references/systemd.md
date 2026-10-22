# systemd user timer

`~/.config/systemd/user/mep-watch.service`

```ini
[Unit]
Description=miseenplace poll

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'out=$(mep watch --once --cursor systemd); [ -n "$out" ] && notify-send miseenplace "$out" || true'
```

`~/.config/systemd/user/mep-watch.timer`

```ini
[Unit]
Description=miseenplace poll every 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now mep-watch.timer
```

Set `Environment=MISEENPLACE_DIR=...` and the source credentials in the service unit if they are not in your login environment.
