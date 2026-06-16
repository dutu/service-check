# Architecture

`service-check` is a local watchdog runner, not a daemon and not a full
monitoring framework.

The core idea is to keep the runner generic while keeping health logic explicit.
The runner knows how to load config, execute checks, retry, persist state, and
notify. Individual check functions know how to interrogate one service.

## Table Of Contents

- [Runtime Flow](#runtime-flow)
- [Design Goals](#design-goals)
- [Scheduling](#scheduling)
- [Main Components](#main-components)
  - [`cli.py`](#clipy)
  - [`config.py`](#configpy)
  - [`runner.py`](#runnerpy)
  - [`state.py`](#statepy)
  - [`notify.py`](#notifypy)
  - [`kuma.py`](#kumapy)
  - [`checks/*`](#checks)
- [Check Registry](#check-registry)
- [Result Contract](#result-contract)
- [Message Rendering](#message-rendering)
- [Retry and Alert Thresholds](#retry-and-alert-thresholds)
- [Notification Policy](#notification-policy)
- [Aggregation](#aggregation)
- [Uptime Kuma Mapping](#uptime-kuma-mapping)
- [Versioning And Updates](#versioning-and-updates)
- [Secrets](#secrets)
- [Error Handling](#error-handling)
- [Future Check Designs](#future-check-designs)
- [Extension Rules](#extension-rules)

## Runtime Flow

```text
systemd timer every minute
  -> service-check CLI
      -> load INI config
      -> load INI drop-ins
      -> load previous JSON state
      -> select enabled checks whose interval has elapsed
      -> dispatch each check by configured check module name
      -> retry failed checks inside the current run
      -> normalize results to OK, WARN, CRIT, or UNKNOWN
      -> decide whether to notify
      -> write updated JSON state
      -> optionally push each check result to its Uptime Kuma push URL
      -> exit
```

The process is short-lived. systemd provides the one-minute tick; the runner
decides which checks are due.

## Design Goals

- Keep service logic explicit in Python check functions.
- Keep machine-specific values in INI files.
- Avoid a no-code monitoring framework.
- Avoid alert spam during short restarts.
- Run cleanly from systemd without a resident daemon process.
- Support multiple machines with different enabled checks.

## Scheduling

The runner does not implement a resident scheduler.

Use one shared INI config with `interval_minutes` per check section:

```ini
[monerod]
enabled=1
check=monerod_sync
interval_minutes=5

[monero_wallet_rpc]
enabled=1
check=monero_wallet_rpc
interval_minutes=1

[bitcoind]
enabled=1
check=bitcoind_sync
interval_minutes=30
```

The single systemd timer runs every minute:

```text
service-check.timer -> service-check.service -> service-check
```

A check is due when:

```text
now - last_run_at >= interval_minutes
```

The config layer defaults missing `interval_minutes` values to
`default_interval_minutes`, or to `5` when no default is configured.

The runner takes a lock around selection, execution, state updates, and
notification decisions. The state file remains shared because state keys are
based on the section name.

The lock covers the full check cycle, including retry delays. Concurrent normal
runs serialize on the lock; the waiting process reloads state after acquiring
the lock and skips checks that the previous process just completed. Dry runs
load state without locking and do not save state.

## Main Components

Package layout:

```text
service_check/
+-- __init__.py
+-- cli.py
+-- config.py
+-- models.py
+-- runner.py
+-- state.py
+-- notify.py
+-- kuma.py
+-- templates.py
+-- checks/
    +-- __init__.py
    +-- tcp_port/
    |   +-- __init__.py
    |   +-- check.py
    |   +-- README.md
    |   +-- example.ini
```

### `cli.py`

Responsibilities:

- parse command-line arguments
- select config path
- initialize logging
- call the runner
- return process exit code

CLI arguments:

```text
--config /etc/service-check/service-check.ini
--config-dir /etc/service-check/service-check.ini.d
--all
--dry-run
--no-notify
--check monerod
--verbose
```

### `config.py`

Responsibilities:

- read the INI file
- read optional `*.ini` drop-ins from `<config>.d` or `--config-dir`
- parse `[global]`
- find enabled check sections and merge global defaults
- merge global defaults with per-check overrides
- validate required fields for each check type where practical

The config layer does not implement health logic. It transforms INI data into
typed values the runner can use.

### `runner.py`

Responsibilities:

- resolve `check=...` through the check registry
- execute checks selected by interval due state, `--all`, or `--check`
- apply immediate retries
- convert exceptions to `UNKNOWN`
- aggregate results
- call state and notification logic
- call per-check Kuma push logic

The runner owns generic behavior. Check modules do not decide notification
policy.

### `state.py`

Responsibilities:

- read and write the JSON state file
- track last status per check
- track consecutive failure counts
- track first failure time
- track last notification time
- support recovery detection

The state file is durable but simple. The runner rewrites the whole JSON file
each run.

Example shape:

```json
{
  "checks": {
    "monerod": {
      "last_status": "CRIT",
      "consecutive_failures": 4,
      "first_failure_at": "2026-06-14T18:25:00Z",
      "last_seen_at": "2026-06-14T18:40:00Z",
      "last_notification_at": "2026-06-14T18:30:00Z",
      "last_message": "Monero daemon is unhealthy or not synced"
    }
  }
}
```

### `notify.py`

Responsibilities:

- format local alert messages
- execute configured notification command
- handle notification command failures without crashing the whole run

Notification transport stays outside the watchdog. For example, Telegram
delivery lives in a local command such as `/usr/local/bin/telegram-notify`.

Each check may define `notify_cmd` to override `[global] notify_cmd`. This
supports different Telegram topics, email aliases, or local handlers per
service.

`notify_cmd` is rendered with the same placeholder context as messages before it
is split into arguments. The command is executed without a
shell.

### `kuma.py`

Responsibilities:

- push each check result to its optional Uptime Kuma push URL
- map watchdog status to Kuma status
- include a concise status message

The watchdog does not expose a local HTTP endpoint.

### `checks/*`

Responsibilities:

- interrogate one service or protocol
- return a normalized result
- avoid notification decisions
- avoid direct state writes

Check modules may shell out to local tools when that is the safest interface,
for example `wg` or `bitcoin-cli`.

## Check Registry

The check name in config maps to a module directory:

```text
check=tcp_port -> service_check.checks.tcp_port.check:run
```

Each check module exposes the same callable:

```python
def run(config: CheckConfig) -> CheckResult:
    ...
```

The INI references the stable module name:

```ini
[electrs_tcp]
enabled=1
check=tcp_port
interval_minutes=1
host=127.0.0.1
port=50001
```

Adding a new check requires:

- adding a new `service_check/checks/<check_name>/` directory
- adding `check.py` with `run(config)`
- adding a module `README.md`
- adding a module `example.ini`
- documenting returned `details` keys for message placeholders

## Result Contract

Every check returns the same result shape.

Result dataclass:

```python
from dataclasses import dataclass, field

@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    details: dict = field(default_factory=dict)
```

Allowed statuses:

```text
OK
WARN
CRIT
UNKNOWN
```

Status semantics:

- `OK`: healthy
- `WARN`: degraded but still usable
- `CRIT`: broken or outside threshold
- `UNKNOWN`: check could not be evaluated correctly

## Message Rendering

Configured messages may include simple placeholders populated from the check
result and runner context.

Example:

```ini
failure_message=Monero daemon unhealthy: height={height}, target={target_height}, lag={height_lag}
success_message=Monero daemon healthy: height={height}
```

The rendering context includes:

- runner fields such as `hostname`, `section`, and `check`
- check config keys such as `notify_topic`
- result fields such as `name`, `status`, and `message`
- state fields such as `failure_count`
- all keys from `CheckResult.details`

Template rendering is intentionally limited:

- support only `{key}` replacement
- do not support expressions, function calls, conditionals, or loops
- do not invoke a shell while rendering
- handle missing placeholders without crashing the watchdog run

The configured `failure_message` is treated as a problem-state presentation
template. The configured `success_message` is used for `OK` status, recovery
notifications, and Kuma `OK` pushes. The original `CheckResult.message` remains
available in details or state when useful for debugging.

## Retry and Alert Thresholds

Retries and notification thresholds are separate concepts.

`retries` means immediate retries inside the same watchdog run.

Example:

```text
Run starts
  check fails
  wait retry_delay
  retry 1 fails
  wait retry_delay
  retry 2 fails
  result for this run is CRIT
```

`fail_after` means the number of failed due runs required before sending a
new failure alert.

Example with `fail_after=3`:

```text
12:00 CRIT, failure count 1, no alert
12:05 CRIT, failure count 2, no alert
12:10 CRIT, failure count 3, alert
```

This protects against short restarts and brief network blips.

## Notification Policy

Notification policy:

| Previous State | Current State | Action |
| --- | --- | --- |
| `OK` | `OK` | No notification. |
| `OK` | `WARN` | Usually no notification. |
| `OK` | `CRIT` | Notify after `fail_after`. |
| `WARN` | `CRIT` | Notify after `fail_after`. |
| `CRIT` | `CRIT` | Notify only after `repeat_after`. |
| `CRIT` | `OK` | Notify recovery if enabled. |
| any | `UNKNOWN` | Treat like failure unless configured otherwise. |

This policy belongs in the runner/state layer, not in individual check functions.

Checks may opt into `WARN` notifications with `notify_on_warn=1`. Use this only
for cases where `WARN` is the desired alert status, such as
`github_release_update` reporting that a new version is available.

## Aggregation

Per-check results are aggregated for process exit code and optional aggregate
reporting.

Aggregate rules:

- any `CRIT`: aggregate `CRIT`
- else any `UNKNOWN`: aggregate `UNKNOWN`
- else any `WARN`: aggregate `WARN`
- otherwise `OK`

Process exit codes:

| Exit Code | Meaning |
| --- | --- |
| `0` | All checks healthy or only warnings, depending on policy. |
| `1` | At least one check is `CRIT` or `UNKNOWN`. |
| `2` | Configuration error or runner error. |

## Uptime Kuma Mapping

Kuma push URLs are configured per check section.

Example:

```ini
[monerod]
enabled=1
check=monerod_sync
kuma_push_url=https://kuma.example.com/api/push/monerod-token
failure_message=Monero daemon unhealthy: height={height}, target={target_height}, lag={height_lag}

[wg_btrad]
enabled=1
check=wireguard_peer
kuma_push_url=https://kuma.example.com/api/push/wg-btrad-token
failure_message=WireGuard peer btrad is stale: latest_handshake_age={latest_handshake_age}s
```

If a section has no `kuma_push_url`, Kuma push is skipped for that check.

Kuma mapping:

| Check Status | Kuma |
| --- | --- |
| `OK` | up |
| `WARN` | up with warning message |
| `CRIT` | down |
| `UNKNOWN` | down |

The Kuma message uses the same message rendering path as local alerts, so
placeholders from `CheckResult.details` are available.

An optional aggregate Kuma monitor can be supported with
`aggregate_kuma_push_url`, but it is secondary. Per-check push URLs are
the preferred model because they provide clearer dashboard rows and separate
history per service.

## Versioning And Updates

The installed application exposes a local version. Update detection is future
work and uses the same runner, state, message rendering, notification,
scheduling, and Kuma push behavior as service health checks.

Local version source:

```python
# service_check/__init__.py
__version__ = "0.2.0"
```

Release tag format:

```text
v0.1.0
v0.2.0
v1.0.0
```

The update checker compares the local version against the GitHub release
`tag_name`, after stripping an optional leading `v`.

Update CLI:

```text
--version
--check github_release_update
--self-update
```

`github_release_update` responsibilities:

- read a normal check section
- call the GitHub latest release endpoint for stable releases
- compare local version to latest release tag
- return `OK` when the installed version is current
- return `WARN` when a newer release exists
- return `UNKNOWN` when the update check cannot be completed
- never modify installed files

Example config:

```ini
[github_release_update]
enabled=1
interval_minutes=1440
check=github_release_update
repository=dutu/service-check
fail_after=1
repeat_after=86400
notify_on_warn=1
notify_on_success_once=1
kuma_push_url=https://kuma.example.com/api/push/service-check-update-token
success_message=service-check {current_version} is up-to-date
failure_message=service-check new version available: current={current_version}, available={available_version}
```

The result details include:

- `current_version`
- `latest_version`
- `release_url`
- `release_name`
- `published_at`

`--self-update` responsibilities:

- require explicit user action
- require root when modifying `/opt/service-check` or systemd files
- download the selected release
- install using the same layout as `install.sh`
- preserve `/etc/service-check` and `/var/lib/service-check`
- run `systemctl daemon-reload` if unit files changed

Automatic installation is not enabled by default. A watchdog that can
self-modify as root is more operationally risky than one that only reports an
available update.

## Secrets

Secrets do not live in the main config or repo examples.

Use file references:

```ini
rpc_password_file=/etc/service-check/secrets/monero-wallet-rpc.pw
```

Check functions read secret files only when needed.

## Error Handling

Expected failures return `CRIT` or `WARN`.

Examples:

- service responds but is not synced: `CRIT`
- peer count below threshold: `WARN` or `CRIT`, depending on check policy
- TCP port refused: `CRIT`

Unexpected failures return `UNKNOWN`.

Examples:

- invalid config value
- malformed JSON
- missing local command
- command output cannot be parsed

The runner catches uncaught check exceptions and converts them to `UNKNOWN` so
one bad check does not prevent the rest from running.

## Future Check Designs

These checks are design targets, not implemented checks in the current package.

| Check | Purpose |
| --- | --- |
| `monerod_sync` | Verify Monero daemon RPC health, sync lag, offline state, and outgoing peers. |
| `monero_wallet_rpc` | Verify wallet RPC responds and authenticates. |
| `wireguard_peer` | Verify interface, peer presence, and latest handshake age. |
| `http_json` | Verify an HTTP endpoint responds with valid JSON and optional expected fields. |
| `bitcoind_sync` | Verify Bitcoin Core sync state and peer count. |
| `github_release_update` | Check whether a newer `service-check` GitHub Release is available. |

For `monerod_sync`, useful health criteria are:

- RPC responds.
- `offline` is false.
- `target_height` is zero or local height is close to target height.
- outgoing peers are at or above `min_outgoing_peers`.
- optional synchronized flag is true if available.

For `wireguard_peer`, useful health criteria are:

- interface exists
- peer exists
- latest handshake age is below the configured threshold

Handshake age can be stale on idle tunnels. If the tunnel is expected to be
continuously usable, add an optional ping check through the tunnel.

For `bitcoind_sync`, useful health criteria are:

- `bitcoin-cli` can reach the node.
- `initialblockdownload` is false.
- `blocks` is close to `headers`.
- peer count is above the configured minimum.
- `verificationprogress` is close to 1.

## Extension Rules

When adding checks:

- keep check logic service-specific and explicit
- keep config inputs minimal
- avoid making generic expression languages or no-code rule systems
- return the standard result shape
- add example config
- document required local dependencies

The important boundary is:

```text
INI config decides what to check and with which thresholds.
Python code decides how the service is interrogated and interpreted.
```



