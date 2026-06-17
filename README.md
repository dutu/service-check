# service-check

`service-check` is a small local watchdog runner for self-hosted infrastructure.

It is designed for machines that need a few service-specific health checks without
running a full monitoring platform locally. The runner reads INI config, executes
registered check functions, stores previous state, and sends notifications only
when something meaningfully changes.

Typical targets include local TCP ports, service RPC endpoints, WireGuard peers,
HTTP or JSON endpoints, and helper services such as proxies or exporters.

The intended monitoring split is:

- `service-check`: local health computation
- systemd timer: scheduling
- state file: anti-spam and recovery detection
- notification helper: alert delivery, for example Telegram
- Uptime Kuma: optional dashboard and external alerting

## Table Of Contents

- [How It Works](#how-it-works)
- [Current Implementation](#current-implementation)
- [Usage](#usage)
- [Scheduling Model](#scheduling-model)
- [Configuration](#configuration)
- [Global Settings](#global-settings)
- [Message Templates](#message-templates)
- [Status Levels](#status-levels)
- [Checks](#checks)
- [Notification Command](#notification-command)
- [Uptime Kuma Push](#uptime-kuma-push)
- [Versioning And Updates](#versioning-and-updates)
- [Installation](#installation)
- [systemd](#systemd)
- [Journal Logs](#journal-logs)
- [Secrets](#secrets)
- [Architecture And Check Development](#architecture-and-check-development)

## How It Works

The normal runtime flow is:

```text
systemd timer every minute
  -> service-check
      -> read /etc/service-check/service-check.ini
      -> read /etc/service-check/service-check.ini.d/*.ini
      -> execute enabled checks whose interval has elapsed
      -> retry transient failures inside the current run
      -> compute OK, WARN, CRIT, or UNKNOWN
      -> update /var/lib/service-check/state.json
      -> notify only on threshold, recovery, or repeat interval
      -> optionally push each check result to its Uptime Kuma push URL
```

The script is not a daemon. systemd starts it every minute and the process exits
after one check cycle.

## Current Implementation

The current package includes:

- CLI entry point: `service-check` / `python -m service_check.cli`
- INI config loading with optional drop-in files
- interval-based check selection
- immediate retries
- JSON state tracking
- local notification command execution
- optional per-check Uptime Kuma push
- implemented checks live under [`service_check/checks`](service_check/checks)

## Usage

Run due checks directly from the repo:

```bash
python -m service_check.cli --config examples/service-check.ini --dry-run
```

Run with a main config and drop-in directory:

```bash
python -m service_check.cli --config examples/service-check.ini --dry-run
```

Run all enabled checks, ignoring `interval_minutes`:

```bash
python -m service_check.cli --config examples/service-check.ini --all --dry-run
```

Run one enabled section, ignoring `interval_minutes`:

```bash
python -m service_check.cli --config examples/service-check.ini --check github_release_update --dry-run
```

Show results for one enabled section or all sections with one option:

```bash
python -m service_check.cli --config examples/service-check.ini --results-for github_release_update --dry-run
python -m service_check.cli --config examples/service-check.ini --results-for all --dry-run
```

List enabled checks and their schedule state without running them:

```bash
python -m service_check.cli --config examples/service-check.ini --list-scheduled
```

Validate the merged config without running checks:

```bash
python -m service_check.cli --config examples/service-check.ini --validate-config
```

Print the effective config after drop-ins, defaults, overrides, and disabled
sections are applied:

```bash
python -m service_check.cli --config examples/service-check.ini --print-config
```

Show available result/template fields for a check type:

```bash
python -m service_check.cli --describe-check tcp_port
python -m service_check.cli --describe-check all
```

Run installation/runtime diagnostics without running checks:

```bash
python -m service_check.cli --config examples/service-check.ini --doctor
```

Show the installed version:

```bash
service-check --version
```

Useful CLI options:

| Option | Purpose |
| --- | --- |
| `--config PATH` | Main INI config path. Defaults to `/etc/service-check/service-check.ini`. |
| `--config-dir PATH` | Optional drop-in directory. Defaults to `<config>.d`. |
| `--all` | Run all enabled checks regardless of interval. |
| `--check SECTION` | Run one enabled section regardless of interval. |
| `--results-for SECTION\|all` | Run one enabled section, or all enabled sections with `all`, regardless of interval. |
| `--list-scheduled` | List enabled checks, local last run time, next due time, and last status. |
| `--validate-config` | Validate known section names, check modules, required keys, value types, and unknown keys. |
| `--print-config` | Print the effective enabled config after defaults and drop-in overrides are applied. |
| `--describe-check CHECK\|all` | Show common template fields, check-specific result detail fields, and possible statuses. |
| `--doctor` | Run static installation/runtime checks for Python, command path, package path, config, state, checks, notifications, Kuma, and systemd. |
| `--dry-run` | Skip notifications and Kuma pushes. |
| `--no-notify` | Skip local notification command execution. |
| `--verbose` | Enable debug logging. |
| `--version` | Print version and exit. |

The default example config uses local state paths under `./.service-check/` and
an enabled `github_release_update` check, so it can be tested without root and
without relying on any local TCP service.

## Scheduling Model

Use one config file and one systemd timer.

The systemd timer runs once per minute. Each check section defines its own
`interval_minutes` value:

```ini
[electrs_tcp]
enabled=1
check=tcp_port
interval_minutes=1
host=127.0.0.1
port=50001
```

The runner decides whether each check is due from state:

```text
now - last_run_at >= interval_minutes * 60
```

Typical intervals:

| Interval | Typical Checks |
| --- | --- |
| `1` | TCP ports, local RPC reachability, wallet RPC |
| `5` | service sync health, WireGuard peers, HTTP JSON checks |
| `30` | full sync checks, Electrs, update checks, less volatile services |

The runner uses one shared state file with top-level `service_check_version`
and `checks` keys. `checks` is keyed by section name and stores `last_run_at`
for each section. The runner takes a state-file lock while running checks and
writing state.

Normal runs hold the lock while checks execute, including retries. Due checks
are sorted by oldest `last_run_at`, so a slow early section cannot permanently
starve later sections. State is saved after each completed check, so progress is
checkpointed even when `max_run_seconds` stops the current run before all due
checks finish. If a second `service-check` process starts while another run is
active, it waits up to `max_lock_hold_minutes` for the lock, reloads state after
the first run saves, and skips checks that are no longer due. `--dry-run` does
not take the lock or save state.

## Configuration

Configuration uses INI sections.

`[global]` defines runner-wide behavior. `[default]` defines check defaults that
service sections may override. Each service section enables one check module and
provides only the inputs that module needs.

The runner reads the main config first, then optional drop-in files:

```text
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/*.ini
```

Drop-ins load in lexical order. Later files override earlier values for the same
section and key. Missing `.d` directories are ignored. Only `*.ini` files are
loaded.

Common split:

```text
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/
+-- 10-version.ini
+-- 20-wallet-rpc.ini
+-- 30-electrs.ini
```

Keep `[global]` primarily in the main file. Put individual check sections in
drop-in files.

Example:

```ini
[global]
hostname=home-mt
state_file=/var/lib/service-check/state.json

[default]
notify_cmd=/usr/local/bin/telegram-notify infra
interval_minutes=5
timeout_seconds=5
retries=2
retry_delay_seconds=5
fail_after=3
notify_repeat_after_minutes=60
notify_on_recovery=1

[electrs_tcp]
enabled=1
check=tcp_port
interval_minutes=1
host=127.0.0.1
port=50001
timeout_seconds=2
failure_message=Electrs TCP port {host}:{port} is down: {error}
success_message=Electrs TCP port {host}:{port} is reachable in {elapsed_ms}ms
```

## Global Settings

Common global keys:

| Key | Purpose |
| --- | --- |
| `hostname` | Name included in notifications and Kuma messages. Defaults to the machine hostname. |
| `state_file` | JSON state path. Defaults to `/var/lib/service-check/state.json`. |
| `lock_file` | Lock path. Defaults to `state_file` plus `.lock`. |
| `max_run_seconds` | Maximum time budget for one runner invocation before starting another check. Defaults to `50`; `0` disables the budget. |
| `max_lock_hold_minutes` | Maximum time to wait for another runner process to release the state lock. Defaults to `2`; `0` waits indefinitely. |

Common default keys in `[default]`:

| Key | Purpose |
| --- | --- |
| `notify_cmd` | Local command used to send alerts. |
| `interval_minutes` | Interval used when a check section omits `interval_minutes`. Defaults to `5`. |
| `timeout_seconds` | Default network timeout in seconds. Defaults to `5`. |
| `retries` | Immediate retries inside one watchdog run. Defaults to `0`. |
| `retry_delay_seconds` | Delay in seconds between immediate retries. Defaults to `1`. |
| `fail_after` | Failed due runs required before alerting. Defaults to `1`. |
| `notify_repeat_after_minutes` | Minutes before repeating a notification for an unresolved problem. Defaults to `60`. |
| `notify_on_recovery` | Whether to notify when a failed check recovers. Defaults to `1`. |

Per-check sections may define or override `[default]` keys plus:

- `interval_minutes`
- `timeout_seconds`
- `retries`
- `retry_delay_seconds`
- `fail_after`
- `notify_repeat_after_minutes`
- `notify_on_recovery`
- `failure_message`
- `success_message`
- `notify_cmd`
- `kuma_push_url`
- `notify_on_warn`
- `notify_on_first_success`

## Message Templates

`failure_message` and `success_message` may include simple placeholders that are
replaced from the check result before an alert or Kuma push is sent.

Example:

```ini
failure_message=TCP port {host}:{port} is down: {error}
success_message=TCP port {host}:{port} is reachable in {elapsed_ms}ms
```

Failure templates may also be specialized by problem code:

```ini
failure_message=Service problem: {message}
failure_message.port_unreachable=TCP port {host}:{port} is down: {error}
```

When a failing result includes `{problem_code}`, the runner first tries
`failure_message.<problem_code>`, then falls back to `failure_message`, then to
the check's raw message.

Built-in placeholders:

| Placeholder | Source |
| --- | --- |
| `{hostname}` | `[global] hostname` |
| `{section}` | INI section name, for example `electrs_tcp` |
| `{check}` | Check function name, for example `tcp_port` |
| `{name}` | Result name |
| `{status}` | Result status |
| `{notify_level}` | Syslog-compatible notification level derived from status |
| `{message}` | Result message |
| `{failure_count}` | Consecutive failed due runs |
| `{details_key}` | Any key returned in `CheckResult.details`, for example `{elapsed_ms}` |
| `{config_key}` | Any key from the check config, for example `{notify_topic}` |

Checks may return `{problem_code}` for the primary failure reason and
`{problem_codes}` for all current failure reasons. A problem-code change triggers
a notification immediately after `fail_after` is satisfied, without waiting for
`notify_repeat_after_minutes`.

Template rendering is deliberately simple:

- use `{key}` placeholders only
- no expressions, conditionals, loops, or shell expansion
- unknown placeholders stay visible in the rendered message
- a bad template does not prevent state updates or other checks from running

`notify_cmd` also supports placeholders and is rendered before execution. Use
`{status}` for the service-check monitoring status and `{notify_level}` for
syslog-compatible notification severity. The command is executed without shell
evaluation.

Default notification level mapping:

| Status | Recovery? | `{notify_level}` |
| --- | --- | --- |
| `OK` | no | `info` |
| `OK` | yes | `notice` |
| `WARN` | no | `warning` |
| `CRIT` | no | `crit` |
| `UNKNOWN` | no | `err` |

## Status Levels

Checks return one of four statuses:

| Status | Meaning |
| --- | --- |
| `OK` | Healthy. |
| `WARN` | Degraded but still usable. |
| `CRIT` | Broken or outside an acceptable threshold. |
| `UNKNOWN` | Check failed unexpectedly, usually bad config, parse failure, or command error. |

Alert behavior:

| Transition | Behavior |
| --- | --- |
| `OK -> CRIT` | Notify after `fail_after` failed runs. |
| `WARN -> CRIT` | Notify after `fail_after` failed runs. |
| `CRIT -> OK` | Notify recovery if enabled. |
| `CRIT -> CRIT` | Do not repeat unless `notify_repeat_after_minutes` elapsed. |
| problem code changes | Notify immediately after `fail_after` is satisfied. |
| `OK -> WARN` | No local alert unless `notify_on_warn=1`. |
| first `OK` | Notify once only if `notify_on_first_success=1`. |

## Checks

Check modules live under [`service_check/checks`](service_check/checks).
Each check directory owns its own README and example config. Check docs cover
required config keys, optional config keys, returned template placeholders, and
local dependencies.

## Notification Command

`service-check` delegates alert delivery to an existing local command.

Example:

```ini
[default]
notify_cmd=/usr/local/bin/telegram-notify --level {notify_level} infra
```

The runner appends one argument containing the rendered `failure_message` or
`success_message`:

```text
/usr/local/bin/telegram-notify --level crit infra "Electrs TCP port 127.0.0.1:50001 is down"
```

This keeps notification transport separate from health-check logic.

## Uptime Kuma Push

Kuma integration is optional and configured per check.

Create one Kuma push monitor for each watchdog section you want on the dashboard,
then put that monitor's push URL in the same INI section.

Example:

```ini
[electrs_tcp]
enabled=1
check=tcp_port
host=127.0.0.1
port=50001
kuma_push_url=https://kuma.example.com/api/push/electrs-token
failure_message=Electrs TCP port {host}:{port} is down: {error}
```

Per-check mapping:

| Check Status | Kuma Push |
| --- | --- |
| `OK` | up |
| `WARN` | up with warning message |
| `CRIT` | down |
| `UNKNOWN` | down |

If a section has no `kuma_push_url`, the runner skips Kuma for that check.

## Versioning And Updates

`service-check` exposes the installed version:

```bash
service-check --version
```

The Python package version is stored in `service_check/__init__.py` and
`pyproject.toml`. The current version is also written to the state file as
`service_check_version` whenever state is saved.

Release notes are published through GitHub Releases when a stable release is
prepared and the package version is bumped.

The `github_release_update` check calls the GitHub latest-release endpoint and
compares the installed version with the latest release tag. `expected_version`
can still be configured as a manual override for offline tests.

## Installation

Deployment targets:

```text
/opt/service-check-venv/bin/service-check
/usr/local/bin/service-check
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/
/etc/systemd/system/service-check.service
/etc/systemd/system/service-check.timer
/var/lib/service-check/state.json
```

Installer flow:

```bash
sudo git clone https://github.com/dutu/service-check.git /opt/service-check-src
cd /opt/service-check-src
sudo bash install.sh
sudo service-check --doctor
```

The installer performs the normal deployment flow:

- installs OS prerequisites on apt, dnf, or yum based systems
- uses `/opt/service-check-src` as the stable source checkout
- creates or updates `/opt/service-check-venv`
- installs the package into the virtual environment
- links `/usr/local/bin/service-check` to the virtualenv command
- creates `/etc/service-check`, `/etc/service-check/service-check.ini.d`, and `/var/lib/service-check`
- copies production config with non-overwrite behavior
- installs check examples as inactive `.ini.skip` drop-ins with non-overwrite behavior
- repairs the old local-dev relative state paths if found in `/etc/service-check/service-check.ini`
- disables the obsolete default `example_tcp_open` drop-in if it still exists unchanged
- installs the systemd service and timer
- enables `service-check.timer`
- runs version, dry-run, and timer status checks

Existing files under `/etc/service-check` are not overwritten. Check examples
are installed as `/etc/service-check/service-check.ini.d/*.ini.skip`; copy an
example to the same name without `.skip`, edit it, and validate config to enable
that check.

Manual fallback flow:

```bash 
sudo apt update
sudo apt install -y git python3 python3-venv rsync
sudo git clone https://github.com/dutu/service-check.git /opt/service-check-src
cd /opt/service-check-src
sudo python3 -m venv /opt/service-check-venv
sudo /opt/service-check-venv/bin/python -m pip install .
sudo ln -sfn /opt/service-check-venv/bin/service-check /usr/local/bin/service-check
sudo mkdir -p /etc/service-check/service-check.ini.d /var/lib/service-check
sudo cp -n examples/service-check.production.ini /etc/service-check/service-check.ini
sudo cp -n examples/service-check.ini.d/10-version.ini /etc/service-check/service-check.ini.d/10-version.ini
for file in service_check/checks/*/*.example.ini; do sudo cp -n "$file" "/etc/service-check/service-check.ini.d/$(basename "$file" .example.ini).ini.skip"; done
sudo cp systemd/service-check.service /etc/systemd/system/service-check.service
sudo cp systemd/service-check.timer /etc/systemd/system/service-check.timer
sudo systemctl daemon-reload
sudo systemctl enable --now service-check.timer
```

Post-install check:

```bash
sudo service-check --doctor
sudo service-check --config /etc/service-check/service-check.ini --all --dry-run
sudo service-check --config /etc/service-check/service-check.ini --all --no-notify
sudo test -f /var/lib/service-check/state.json
systemctl is-enabled service-check.timer
systemctl is-active service-check.timer
systemctl status --no-pager service-check.timer service-check.service
systemctl list-timers --all --no-pager service-check.timer
sudo journalctl -u service-check.service -n 20 --no-pager
```

The doctor confirms the installed version, Python runtime, command path, package
path, config sources, config validation, state/lock paths, state JSON shape,
check module imports, notification command shape, Kuma URL shape, and systemd
unit/timer status without running checks. The dry run executes all enabled checks
without sending notifications, without pushing to Kuma, and without writing
state. The `--all --no-notify` run executes due checks and writes
`/var/lib/service-check/state.json` without local notifications. The systemd
commands confirm that the timer is enabled, active, and scheduled. The service is
`oneshot`, so it is normally inactive between timer runs; use `journalctl` to
inspect recent run output after the timer has fired.

The current package install provides the `service-check` command from
`pyproject.toml` inside `/opt/service-check-venv`, and the installer links it to
`/usr/local/bin/service-check` for normal shell use. Production deployment also
needs `/etc/service-check`, `/var/lib/service-check`, and the systemd unit
files.

Use `/opt/service-check-src` as the stable source checkout. The installer syncs
the checkout you run it from into that path. Keep local runtime configuration in
`/etc/service-check`, not in the repository checkout. The installer and manual
example config copy commands use non-overwrite behavior, so existing config is
not overwritten during first install or later reruns.

Minimal server images often have `python3` without `pip`. Do not use
`sudo python -m pip ...` unless your distribution provides a `python` command.
Using a virtual environment avoids requiring system `pip` and avoids modifying
distribution-managed Python packages.

Update flow:

```bash
cd /opt/service-check-src
sudo git pull --ff-only
sudo bash install.sh
sudo service-check --doctor
```

The update flow does not overwrite files in `/etc/service-check`, so existing
configuration and secrets are left untouched. It does update the virtual
environment and systemd units from the current checkout. Only review and copy
example config changes manually when the release notes or diff indicate that you
need them:

```bash
cd /opt/service-check-src
git diff HEAD@{1} -- examples/ service_check/checks/ systemd/
sudo cp -n examples/service-check.ini.d/10-version.ini /etc/service-check/service-check.ini.d/10-version.ini
for file in service_check/checks/*/*.example.ini; do sudo cp -n "$file" "/etc/service-check/service-check.ini.d/$(basename "$file" .example.ini).ini.skip"; done
```

## systemd

Use one service and one timer. The timer runs once per minute; the runner decides
which checks are due from each section's `interval_minutes` and `last_run_at`.

`service-check.service`:

```ini
[Unit]
Description=Run service-check watchdog

[Service]
Type=oneshot
ExecStart=/opt/service-check-venv/bin/service-check --config /etc/service-check/service-check.ini
```

`service-check.timer`:

```ini
[Unit]
Description=Run service-check watchdog every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=service-check.service

[Install]
WantedBy=timers.target
```

## Journal Logs

systemd captures stdout and Python logging from each oneshot run. Useful entries
include:

- `run_start` / `run_end`: selected mode, enabled checks, state file, final status, exit code, and duration.
- `config_loaded`: main config and drop-in files used by the run.
- `checks_selected`: due or explicitly selected sections.
- `check_retry`: retry attempts before a final result.
- `check_result`: section, check type, status, duration, consecutive failures, notification decision, Kuma decision, and rendered message.
- `state_load` / `state_save`: state file path and tracked check count.

The runner does not log full notification commands or Kuma push URLs because
they may contain secrets. Inspect recent logs with:

```bash
sudo journalctl -u service-check.service -n 50 --no-pager
```

## Secrets

Do not put secrets in the repo or example configs.

Use file references where a check supports them:

```ini
rpc_password_file=/etc/service-check/secrets/monero-wallet-rpc.pw
```

Use restrictive permissions for secret files:

```bash
sudo install -d -m 0700 /etc/service-check/secrets
sudo install -m 0600 monero-wallet-rpc.pw /etc/service-check/secrets/monero-wallet-rpc.pw
```

## Architecture And Check Development

Internal design and extension rules live in [ARCHITECTURE.md](ARCHITECTURE.md).

For new checks, follow the boundary documented in
[ARCHITECTURE.md#extension-rules](ARCHITECTURE.md#extension-rules): INI config
decides what to check and with which thresholds; Python code decides how the
service is interrogated and interpreted. Use `service_check/checks/tcp_port` as
the reference model for new check modules.
