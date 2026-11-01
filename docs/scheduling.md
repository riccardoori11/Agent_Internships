# Daily schedule

The installer prepares a **systemd user timer on the Linux machine containing this checkout**. It is independent of the agent session once installed and verified. It updates this checkout's `internship.md`, using `config.toml`; it does not run in a cloud service. The account must retain access to the checkout. The machine must be powered on, awake, and have working internet access to collect at the scheduled time. Network failures are reported by the collector.

The timer calendar is `*-*-* 08:00:00 Europe/Paris`, with `AccuracySec=1s` and no randomized delay. The IANA timezone handles CET/CEST changes: 08:00 is 07:00 UTC in winter and 06:00 UTC in summer. This is a best-effort OS schedule; load, sleep, networking, or service failures may delay it. Changing the report location scope does not change this timezone.

`Persistent=true` stores the last timer activation. If the machine or user manager was offline for one or more scheduled runs, systemd triggers **one catch-up run** when the timer becomes active again; it does not replay every missed day. It does not wake a suspended machine. A service already running is not started twice by systemd. A failed collection is logged and handled by the collector; it does not cause an extra systemd run later that day. The following scheduled run occurs the next day.

From a normal host terminal, outside the agent sandbox:

```sh
cd /home/riccardo/agent
loginctl enable-linger "$(id -un)"
./scripts/install-schedule.sh
./scripts/verify-schedule.sh
systemctl --user list-timers --all internship-finder.timer
```

`loginctl enable-linger` allows the user manager to survive logout and start at boot. On hosts where enabling linger requires administrative authorization, have the administrator run `sudo loginctl enable-linger "$(id -un)"`. The installer requires a reachable user systemd manager and permission to write `$XDG_CONFIG_HOME/systemd/user` (normally `~/.config/systemd/user`). Python 3.11+ and timezone data must be available to the user manager. If `.venv/bin/python` exists it is used, otherwise the runner uses `python3` from the service's PATH. Keep this checkout at the same absolute path, or rerun installation after moving it.

Review files without installing:

```sh
python3 scripts/schedule.py generate
cat deploy/generated/internship-finder.service
cat deploy/generated/internship-finder.timer
```

The installer copies generated units into the user unit directory and enables the timer immediately. `deploy/schedule-status.json` records the latest installation/verification attempt; only an actual successful verification reports `active: true`. Verify again after reboot if changing host configuration. Runtime logging is available through the collector's `logs/` directory and systemd:

For a nonstandard user unit directory, pass `./scripts/install-schedule.sh --unit-dir /absolute/path/to/systemd/user`; the user manager must search that directory. The normal installation path is recommended. Collector exit statuses 2 (incomplete search) and 3 (another run holds the lock) are accepted by systemd; report status and logs still expose incomplete searches.

```sh
journalctl --user -u internship-finder.service --since today
systemctl --user status internship-finder.timer internship-finder.service
systemctl --user start internship-finder.service
```

Disable the timer with `systemctl --user disable --now internship-finder.timer`. Installation does not disable lingering on removal because other user services may rely on it.

## Activation in this agent environment

The installation was attempted here. The sandbox rejects the user D-Bus connection with `Failed to connect to user scope bus via local transport: Operation not permitted`. The user crontab also rejects access because of PAM configuration. Home configuration directories outside the checkout are not writable under this session's filesystem policy. **The schedule is not active from this session.** Generated units remain in `deploy/generated/`; the exact remaining activation commands are above. No background process or session-only loop is used as a substitute for persistent scheduling.

Unit-template tests and `systemd-analyze calendar` confirm the Paris calendar and both DST transitions. Full `systemd-analyze verify` also encounters sandbox socket restrictions and exits nonzero here; it is not counted as a successful installed-schedule check. Run it on the normal host if desired: `systemd-analyze verify deploy/generated/internship-finder.service deploy/generated/internship-finder.timer`.
