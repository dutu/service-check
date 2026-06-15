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
- one implemented check: [`tcp_port`](service_check/checks/tcp_port/README.md)

## Usage

Run due checks directly from the repo:

```bash
python -m service_check.cli --config examples/tcp.ini --dry-run
```

Run with a main config and drop-in directory:

```bash
python -m service_check.cli --config examples/service-check.ini --dry-run
```

Run all enabled checks, ignoring `interval_minutes`:

```bash
python -m service_check.cli --config examples/tcp.ini --all --dry-run
```

Run one enabled section, ignoring `interval_minutes`:

```bash
python -m service_check.cli --config examples/tcp.ini --check example_tcp_open --dry-run
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
| `--dry-run` | Skip notifications and Kuma pushes. |
| `--no-notify` | Skip local notification command execution. |
| `--verbose` | Enable debug logging. |
| `--version` | Print version and exit. |

The example config uses local state paths under `./.service-check/` so it can be
tested without root. Edit `host` and `port` in `examples/tcp.ini` for a real
service on your machine.

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
now - last_run_at >= interval_minutes
```

Typical intervals:

| Interval | Typical Checks |
| --- | --- |
| `1` | TCP ports, local RPC reachability, wallet RPC |
| `5` | service sync health, WireGuard peers, HTTP JSON checks |
| `30` | full sync checks, Electrs, update checks, less volatile services |

The runner uses one shared state file keyed by section name. It stores
`last_run_at` for each section and takes a state-file lock while running checks
and writing state.

Normal runs hold the lock for the full check cycle, including retries. If a
second `service-check` process starts while another run is active, it waits for
the lock, reloads state after the first run saves, and skips checks that are no
longer due. `--dry-run` does not take the lock or save state.

## Configuration

Configuration uses INI sections.

`[global]` defines defaults and common behavior. Each service section enables one
check module and provides only the inputs that module needs.

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
+-- 10-tcp.ini
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
notify_cmd=/usr/local/bin/telegram-notify infra
default_interval_minutes=5
default_timeout=5
default_retries=2
default_retry_delay=5
default_fail_after=3
default_repeat_after=3600
notify_on_recovery=1

[electrs_tcp]
enabled=1
check=tcp_port
interval_minutes=1
host=127.0.0.1
port=50001
timeout=2
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
| `notify_cmd` | Local command used to send alerts. |
| `default_interval_minutes` | Interval used when a check section omits `interval_minutes`. Defaults to `5`. |
| `default_timeout` | Default network timeout in seconds. Defaults to `5`. |
| `default_retries` | Immediate retries inside one watchdog run. Defaults to `0`. |
| `default_retry_delay` | Delay in seconds between immediate retries. Defaults to `1`. |
| `default_fail_after` | Failed due runs required before alerting. Defaults to `1`. |
| `default_repeat_after` | Seconds before repeating an alert for a still-broken check. Defaults to `3600`. |
| `notify_on_recovery` | Whether to notify when a failed check recovers. Defaults to `1`. |

Per-check sections may define or override:

- `interval_minutes`
- `timeout`
- `retries`
- `retry_delay`
- `fail_after`
- `repeat_after`
- `failure_message`
- `success_message`
- `notify_cmd`
- `kuma_push_url`
- `notify_on_warn`

## Message Templates

`failure_message` and `success_message` may include simple placeholders that are
replaced from the check result before an alert or Kuma push is sent.

Example:

```ini
failure_message=TCP port {host}:{port} is down: {error}
success_message=TCP port {host}:{port} is reachable in {elapsed_ms}ms
```

Built-in placeholders:

| Placeholder | Source |
| --- | --- |
| `{hostname}` | `[global] hostname` |
| `{section}` | INI section name, for example `electrs_tcp` |
| `{check}` | Check function name, for example `tcp_port` |
| `{name}` | Result name |
| `{status}` | Result status |
| `{message}` | Result message |
| `{failure_count}` | Consecutive failed due runs |
| `{details_key}` | Any key returned in `CheckResult.details`, for example `{elapsed_ms}` |
| `{config_key}` | Any key from the check config, for example `{notify_topic}` |

Template rendering is deliberately simple:

- use `{key}` placeholders only
- no expressions, conditionals, loops, or shell expansion
- unknown placeholders stay visible in the rendered message
- a bad template does not prevent state updates or other checks from running

`notify_cmd` also supports placeholders and is rendered before execution. The
command is executed without shell evaluation.

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
| `CRIT -> CRIT` | Do not repeat unless `repeat_after` elapsed. |
| `OK -> WARN` | No local alert unless `notify_on_warn=1`. |

## Checks

Implemented checks:

| Check | Documentation | Purpose |
| --- | --- | --- |
| `tcp_port` | [`service_check/checks/tcp_port/README.md`](service_check/checks/tcp_port/README.md) | Verify a TCP port accepts connections. |

Each check directory owns its own README and example config. Check docs cover
required config keys, optional config keys, returned template placeholders, and
local dependencies.

Future check designs live in [ARCHITECTURE.md](ARCHITECTURE.md#future-check-designs),
not in this README's implemented checks table.

## Notification Command

`service-check` delegates alert delivery to an existing local command.

Example:

```ini
[global]
notify_cmd=/usr/local/bin/telegram-notify infra
```

The runner calls it with a single composed message:

```text
/usr/local/bin/telegram-notify infra "[home-mt] electrs_tcp CRIT: Electrs TCP port 127.0.0.1:50001 is down"
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
`pyproject.toml`.

Release notes are kept in [CHANGELOG.md](CHANGELOG.md) when a stable release is
prepared and the package version is bumped.

Update checking and self-update behavior are design targets documented in
[ARCHITECTURE.md](ARCHITECTURE.md#versioning-and-updates). They are not part of
the current implemented checks table.

## Installation

Deployment targets:

```text
/usr/local/bin/service-check
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/
/var/lib/service-check/state.json
```

Manual install flow:

```bash
sudo git clone https://github.com/dutu/service-check.git /opt/service-check-src
cd /opt/service-check-src
sudo python -m pip install .
sudo mkdir -p /etc/service-check/service-check.ini.d /var/lib/service-check
sudo cp -n examples/service-check.ini /etc/service-check/service-check.ini
sudo cp -n examples/service-check.ini.d/10-tcp.ini /etc/service-check/service-check.ini.d/10-tcp.ini
sudo cp systemd/service-check.service /etc/systemd/system/service-check.service
sudo cp systemd/service-check.timer /etc/systemd/system/service-check.timer
sudo systemctl daemon-reload
sudo systemctl enable --now service-check.timer
```

The current package install provides the `service-check` command from
`pyproject.toml`. Production deployment also needs `/etc/service-check`,
`/var/lib/service-check`, and the systemd unit files.

Use `/opt/service-check-src` as the stable source checkout. Keep local runtime
configuration in `/etc/service-check`, not in the repository checkout. The
example config copy commands use `cp -n` so an existing config file is not
overwritten during first install or later manual reruns.

Update flow:

```bash
cd /opt/service-check-src
sudo git pull --ff-only
sudo python -m pip install --upgrade .
service-check --version
sudo systemctl restart service-check.timer
```

The update flow does not copy files into `/etc/service-check`, so existing
configuration and secrets are left untouched. Only review and copy example
config or systemd unit changes manually when the release notes or diff indicate
that you need them:

```bash
cd /opt/service-check-src
git diff HEAD@{1} -- examples/ systemd/
sudo cp -n examples/service-check.ini.d/10-tcp.ini /etc/service-check/service-check.ini.d/10-tcp.ini
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
ExecStart=/usr/local/bin/service-check --config /etc/service-check/service-check.ini
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
service is interrogated and interpreted.
