# service-check

`service-check` is a small local watchdog runner for self-hosted machines.

It runs from a systemd timer, reads INI configuration, executes enabled health
checks, stores state, sends local notifications only when needed, and can push
results to Uptime Kuma.

Detailed design and extension notes live in [ARCHITECTURE.md](ARCHITECTURE.md).

## Table Of Contents

- [Overview](#overview)
- [Usage](#usage)
- [Installation](#installation)
  - [Installer Flow](#installer-flow)
  - [Enable Checks](#enable-checks)
  - [Manual Installation](#manual-installation)
  - [systemd Timer](#systemd-timer)
- [Configuration](#configuration)
  - [Global Settings](#global-settings)
  - [Default Check Settings](#default-check-settings)
  - [Per-Check Common Settings](#per-check-common-settings)
  - [Scheduling](#scheduling)
  - [Notification Command](#notification-command)
  - [Message Templates](#message-templates)
  - [Uptime Kuma](#uptime-kuma)
- [Healthchecks & Troubleshooting](#healthchecks--troubleshooting)
  - [Post-Install Healthcheck](#post-install-healthcheck)
  - [Runtime Logs](#runtime-logs)
  - [Common Issues](#common-issues)
  - [Secrets](#secrets)
- [Update](#update)
  - [Update Flow](#update-flow)
  - [Review Config Example Changes](#review-config-example-changes)

## Overview

Use `service-check` when a host needs lightweight local checks without running a
full monitoring stack locally.

Typical checks include:

- TCP port reachability
- ICMP ping reachability
- SOCKS proxy functionality
- service-specific checks such as `monerod`
- public IP reputation checks
- GitHub release update checks
- Uptime Kuma heartbeat push checks

Runtime split:

- `service-check`: runs checks and updates local state
- systemd timer: starts `service-check` every minute
- state file: tracks last status, alert thresholds, repeat suppression, and recovery
- notification command: sends local alerts, for example Telegram
- Uptime Kuma: optional dashboard and external alerting

Normal production paths:

```text
/opt/service-check-src
/opt/service-check-venv/bin/service-check
/usr/local/bin/service-check
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/
/var/lib/service-check/state.json
/etc/systemd/system/service-check.service
/etc/systemd/system/service-check.timer
```

## Usage

Most installed usage should point at `/etc/service-check/service-check.ini`.

Run due checks:

```bash
service-check --config /etc/service-check/service-check.ini
```

Run all enabled checks, ignoring `interval_minutes`:

```bash
service-check --config /etc/service-check/service-check.ini --all
```

Run all checks without notifications or Kuma pushes:

```bash
service-check --config /etc/service-check/service-check.ini --all --dry-run
```

Run all checks and write state, but suppress local notifications:

```bash
service-check --config /etc/service-check/service-check.ini --all --no-notify
```

Run one enabled section:

```bash
service-check --config /etc/service-check/service-check.ini --check machine_kuma_heartbeat
```

List enabled checks and schedule state:

```bash
service-check --config /etc/service-check/service-check.ini --list-scheduled
```

Validate config without running checks:

```bash
service-check --config /etc/service-check/service-check.ini --validate-config
```

Print the effective enabled config after defaults and drop-ins:

```bash
service-check --config /etc/service-check/service-check.ini --print-config
```

Show fields and statuses for checks:

```bash
service-check --describe-check all
service-check --describe-check tcp_port
service-check --describe-check kuma_heartbeat
```

Run installation/runtime diagnostics:

```bash
sudo service-check --doctor
```

Useful CLI options:

| Option | Purpose |
| --- | --- |
| `--config PATH` | Main INI config path. Defaults to `/etc/service-check/service-check.ini`. |
| `--config-dir PATH` | Optional drop-in directory. Defaults to `<config>.d`. |
| `--all` | Run all enabled checks regardless of interval. |
| `--check SECTION` | Run one enabled section regardless of interval. |
| `--results-for SECTION\|all` | Run one enabled section, or all enabled sections with `all`, regardless of interval. |
| `--list-scheduled` | Show enabled checks, last run time, next due time, and last status. |
| `--validate-config` | Validate section names, check modules, required keys, value types, and unknown keys. |
| `--print-config` | Print the effective enabled config. |
| `--describe-check CHECK\|all` | Show check-specific fields and statuses. |
| `--doctor` | Check installation, config, state paths, check imports, notification command, Kuma URLs, and systemd status. |
| `--dry-run` | Skip notifications, Kuma pushes, and state writes. |
| `--no-notify` | Skip local notification command execution. |
| `--verbose` | Enable debug logging. |
| `--version` | Print version and exit. |

## Installation

### Installer Flow

Use the installer on Linux hosts with systemd:

```bash
sudo git clone https://github.com/dutu/service-check.git /opt/service-check-src
cd /opt/service-check-src
sudo bash install.sh
sudo service-check --doctor
```

The installer:

- installs OS prerequisites on apt, dnf, or yum based systems
- creates or updates `/opt/service-check-venv`
- installs the Python package into the virtual environment
- links `/usr/local/bin/service-check`
- creates `/etc/service-check`, `/etc/service-check/service-check.ini.d`, and `/var/lib/service-check`
- copies the reference config without overwriting existing config
- installs check examples as inactive `.ini.skip` drop-ins
- installs and reloads systemd units
- enables and restarts `service-check.timer`
- runs version, dry-run, state, and timer checks

Existing files under `/etc/service-check` are not overwritten. Runtime
configuration belongs under `/etc/service-check`, not in the source checkout.

### Enable Checks

Check examples are installed as inactive files:

```text
/etc/service-check/service-check.ini.d/*.ini.skip
```

To enable one:

```bash
cd /etc/service-check/service-check.ini.d
sudo cp 20-tcp-port.ini.skip 20-tcp-port.ini
sudo nano 20-tcp-port.ini
sudo service-check --validate-config
sudo service-check --all --dry-run
```

### Manual Installation

Use this only when you cannot run `install.sh`:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv rsync
sudo git clone https://github.com/dutu/service-check.git /opt/service-check-src
cd /opt/service-check-src
sudo python3 -m venv /opt/service-check-venv
sudo /opt/service-check-venv/bin/python -m pip install .
sudo ln -sfn /opt/service-check-venv/bin/service-check /usr/local/bin/service-check
sudo mkdir -p /etc/service-check/service-check.ini.d /var/lib/service-check
sudo cp -n examples/service-check.ini /etc/service-check/service-check.ini
for file in service_check/checks/*/*.example.ini; do sudo cp -n "$file" "/etc/service-check/service-check.ini.d/$(basename "$file" .example.ini).ini.skip"; done
sudo cp systemd/service-check.service /etc/systemd/system/service-check.service
sudo cp systemd/service-check.timer /etc/systemd/system/service-check.timer
sudo systemctl daemon-reload
sudo systemctl enable --now service-check.timer
sudo systemctl restart service-check.timer
sudo service-check --doctor
```

### systemd Timer

Installed timer:

```ini
[Unit]
Description=Run service-check watchdog every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=1s
RandomizedDelaySec=0
Unit=service-check.service

[Install]
WantedBy=timers.target
```

`AccuracySec=1s` is intentional. systemd timers otherwise allow coalescing for
power efficiency, which can delay a nominal one-minute timer enough for strict
Kuma Push monitors to report missed heartbeats.

Check timer status:

```bash
systemctl is-enabled service-check.timer
systemctl is-active service-check.timer
systemctl list-timers --all --no-pager service-check.timer
systemctl status --no-pager service-check.timer service-check.service
```

## Configuration

Configuration uses INI files:

```text
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/*.ini
```

The main config loads first. Drop-ins load in lexical order. Later values
override earlier values for the same section/key.

Recommended layout:

```text
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/
+-- 05-kuma-heartbeat.ini
+-- 20-wallet-rpc.ini
+-- 30-electrs.ini
```

Use:

- `[global]` for runner-wide settings
- `[default]` for defaults shared by checks
- one section per enabled check

Example:

```ini
[global]
hostname=home-rpi
state_file=/var/lib/service-check/state.json
lock_file=/var/lib/service-check/state.lock
max_run_seconds=50
max_lock_hold_minutes=2
log_level=INFO
show_results=0

[default]
notify_cmd=/usr/local/bin/telegram-notify --level {notify_level} infra
interval_minutes=5
timeout_seconds=5
retries=1
retry_delay_seconds=1
fail_after=2
notify_repeat_after_minutes=60
notify_on_recovery=1
notify_on_first_success=0

[machine_kuma_heartbeat]
enabled=1
check=kuma_heartbeat
interval_minutes=1
heartbeat_url=https://kuma.example.com/api/push/service-check-heartbeat-token
heartbeat_message={hostname} service-check Kuma heartbeat OK
success_message={pushed_message}
failure_message={hostname} service-check cannot push to Kuma: {error}
notify_on_first_success=1

[electrs_tcp]
enabled=1
check=tcp_port
interval_minutes=1
host=127.0.0.1
port=50001
failure_message=Electrs TCP port {host}:{port} is down: {error}
success_message=Electrs TCP port {host}:{port} is reachable in {elapsed_ms}ms
kuma_push_url=https://kuma.example.com/api/push/electrs-token
```

### Global Settings

| Key | Purpose |
| --- | --- |
| `hostname` | Name used in notifications and Kuma messages. Defaults to the machine hostname. |
| `state_file` | JSON state path. Defaults to `/var/lib/service-check/state.json`. |
| `lock_file` | Lock file path. Defaults to `state_file` plus `.lock`. |
| `max_run_seconds` | Maximum wall-clock budget for one runner invocation before starting another check. `0` disables the budget. |
| `max_lock_hold_minutes` | Maximum time to wait for another process to release the state lock. `0` waits indefinitely. |
| `log_level` | Runtime log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. CLI `--verbose` forces `DEBUG`. |
| `show_results` | Print per-check result summaries to stdout during normal runs. Defaults to `0`; `--dry-run` and `--results-for` still show results. |

### Default Check Settings

| Key | Purpose |
| --- | --- |
| `notify_cmd` | Local command used to send notifications. The rendered message is appended as the final argument. |
| `interval_minutes` | Check interval when a section does not override it. |
| `timeout_seconds` | Default network/API timeout. |
| `retries` | Immediate retries after `CRIT` or `UNKNOWN` inside one runner invocation. |
| `retry_delay_seconds` | Seconds to wait between immediate retries. |
| `fail_after` | Consecutive failed due runs required before sending a problem notification. |
| `notify_repeat_after_minutes` | Minutes before repeating an unresolved problem notification. |
| `notify_on_recovery` | Notify when a problem recovers to `OK`. |
| `notify_on_first_success` | Notify once when a check first returns `OK`. Usually enabled only per check. |

### Per-Check Common Settings

Every check section supports the default settings above plus:

| Key | Purpose |
| --- | --- |
| `enabled` | `1` to enable the section. Disabled sections are ignored. |
| `check` | Check module name, for example `tcp_port` or `kuma_heartbeat`. |
| `failure_message` | Template used for problem notifications and Kuma down messages. |
| `failure_message.<problem_code>` | Template for one specific problem code. |
| `success_message` | Template used for OK status, recovery notifications, and Kuma OK messages. |
| `notify_on_warn` | Treat `WARN` as locally notifiable. Use only for checks where degraded state should alert. |
| `notify_topic` | Optional custom value usable in templates and notification commands. |
| `kuma_push_url` | Optional generic Uptime Kuma Push URL for normal check result pushes. |

Check-specific keys are documented in each check directory under
[`service_check/checks`](service_check/checks). Use:

```bash
service-check --describe-check all
```

### Scheduling

The systemd timer starts the runner once per minute. Each check decides its own
cadence with `interval_minutes`.

Due checks are selected by wall-clock interval bucket:

```text
floor(now / interval) > floor(last_run_at / interval)
```

This means `interval_minutes=1` is due once per minute bucket. It is not delayed
just because the previous run finished 59 seconds ago.

### Notification Command

`notify_cmd` is an existing local command. `service-check` appends the rendered
message as the final argument and executes the command without shell evaluation.

Example:

```ini
[default]
notify_cmd=/usr/local/bin/telegram-notify --level {notify_level} infra
```

Effective execution:

```text
/usr/local/bin/telegram-notify --level crit infra "Electrs TCP port 127.0.0.1:50001 is down"
```

### Message Templates

`failure_message`, `success_message`, and `notify_cmd` support simple `{key}`
placeholders.

Common placeholders:

| Placeholder | Source |
| --- | --- |
| `{hostname}` | `[global] hostname` |
| `{section}` | INI section name |
| `{check}` | Check module name |
| `{status}` | `OK`, `WARN`, `CRIT`, or `UNKNOWN` |
| `{notify_level}` | Syslog-style level: `info`, `notice`, `warning`, `crit`, or `err` |
| `{message}` | Raw check result message |
| `{failure_count}` | Consecutive failed due runs |
| `{problem_code}` | Primary machine-readable problem reason, when present |
| `{details_key}` | Any detail returned by the check, for example `{elapsed_ms}` or `{error}` |
| `{config_key}` | Any key from the check config, for example `{notify_topic}` |

Unknown placeholders stay visible in the rendered message.

### Uptime Kuma

There are two Kuma-related patterns.

Use `kuma_push_url` on normal checks when Kuma should show that check's status:

```ini
[electrs_tcp]
enabled=1
check=tcp_port
host=127.0.0.1
port=50001
kuma_push_url=https://kuma.example.com/api/push/electrs-token
```

The runner preserves existing query parameters in `kuma_push_url`, then adds:

- `status=up` for `OK` and `WARN`
- `status=down` for `CRIT` and `UNKNOWN`
- `msg=<rendered message>`

Use `kuma_heartbeat` when the check itself should fail if Kuma cannot be reached:

```ini
[machine_kuma_heartbeat]
enabled=1
check=kuma_heartbeat
interval_minutes=1
heartbeat_url=https://kuma.example.com/api/push/service-check-heartbeat-token
heartbeat_message={hostname} service-check Kuma heartbeat OK
success_message={pushed_message}
failure_message={hostname} service-check cannot push to Kuma: {error}
notify_on_first_success=1
```

Do not set `kuma_push_url` on `kuma_heartbeat`. `heartbeat_url` is the push being
tested; `kuma_push_url` is the generic runner side effect for other checks.

## Healthchecks & Troubleshooting

### Post-Install Healthcheck

Run:

```bash
sudo service-check --doctor
sudo service-check --config /etc/service-check/service-check.ini --all --dry-run
sudo service-check --config /etc/service-check/service-check.ini --all --no-notify
sudo test -f /var/lib/service-check/state.json
systemctl list-timers --all --no-pager service-check.timer
sudo journalctl -u service-check.service -n 50 --no-pager
```

Expected:

- `--doctor` reports no `ERROR` entries
- `--dry-run` runs checks without notifications, Kuma pushes, or state writes
- `--all --no-notify` runs checks and writes state without local notifications
- `service-check.timer` is active and scheduled
- `service-check.service` is usually inactive between timer runs because it is `oneshot`

### Runtime Logs

systemd captures stdout and Python logs:

```bash
sudo journalctl -u service-check.service -n 50 --no-pager
```

Default systemd runs are intentionally quiet when `[global] log_level=INFO` and
`show_results=0`. Routine lifecycle, successful check results, state load/save,
and selected-check logs are emitted at `DEBUG` and appear when setting
`log_level=DEBUG` or running `service-check --verbose`. Default `INFO` output is
reserved for semantic per-check transitions such as `OK -> CRIT`, `CRIT -> OK`,
or problem-code changes. Set `show_results=1` only if you want per-check summary
lines in `journalctl`.

Useful verbose/debug log markers:

- `run_start` / `run_end`: mode, enabled checks, state file, final status, exit code, duration
- `config_loaded`: main config and drop-ins used
- `checks_selected`: due or explicitly selected sections
- `check_retry`: retry attempts
- `check_result`: result, duration, notification decision, Kuma decision, rendered message
- `state_load` / `state_save`: state path and tracked check count

Useful default log marker:

- `check_state_change`: semantic per-check transitions only: status, problem
  state, or problem code changes. Includes failure count, previous/current run
  time, seconds since the previous run, notification timestamps, and whether
  check-specific state changed.

The runner does not log full notification commands or Kuma push URLs because
they may contain secrets.

### Common Issues

| Symptom | Check |
| --- | --- |
| Timer does not run | `systemctl status service-check.timer service-check.service` |
| Timer fires late | `systemctl cat service-check.timer` and confirm `AccuracySec=1s` |
| Check never runs | Confirm `enabled=1`, correct `check=...`, and `--list-scheduled` output |
| Config rejected | Run `service-check --validate-config` |
| Notification not sent | Check `notify_cmd`, `fail_after`, `notify_repeat_after_minutes`, and `--no-notify` usage |
| Kuma shows missed heartbeats | Check timer accuracy, runner logs, `kuma_heartbeat` result, and network reachability |
| Kuma push gets `403` | Compare the configured URL/token, proxy rules, and journal error body |
| State looks wrong | Inspect `/var/lib/service-check/state.json` and run `sudo service-check --doctor` |

### Secrets

Do not put secrets in the repository or example config.

Use restrictive permissions:

```bash
sudo install -d -m 0700 /etc/service-check/secrets
sudo install -m 0600 secret.txt /etc/service-check/secrets/secret.txt
```

Use file-based secret settings where a check supports them.

## Update

### Update Flow

Use the same installer for updates:

```bash
cd /opt/service-check-src
sudo git pull --ff-only
sudo bash install.sh
sudo service-check --doctor
```

The update flow:

- updates the source checkout
- rebuilds/updates the virtual environment package install
- updates `/usr/local/bin/service-check`
- updates systemd units
- reloads systemd and restarts `service-check.timer`
- leaves `/etc/service-check` configuration untouched

### Review Config Example Changes

The installer does not overwrite active config. Review example changes manually:

```bash
cd /opt/service-check-src
git diff HEAD@{1} -- examples/ service_check/checks/ systemd/
```

Copy new examples only when needed:

```bash
for file in service_check/checks/*/*.example.ini; do sudo cp -n "$file" "/etc/service-check/service-check.ini.d/$(basename "$file" .example.ini).ini.skip"; done
```

Then validate:

```bash
sudo service-check --validate-config
sudo service-check --all --dry-run
```
