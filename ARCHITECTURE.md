# Architecture

`service-check` is a local watchdog runner, not a daemon and not a full
monitoring framework.

The core idea is to keep the runner generic while keeping health logic explicit.
The runner knows how to load config, execute checks, retry, persist state, and
notify. Individual check functions know how to interrogate one service.

## Runtime Flow

```text
systemd timer for one schedule group
  -> service-check CLI --schedule fast|normal|slow
      -> load INI config
      -> build enabled check list for that schedule
      -> dispatch each check by registered check name
      -> retry failed checks inside the current run
      -> normalize results to OK, WARN, CRIT, or UNKNOWN
      -> load previous JSON state
      -> decide whether to notify
      -> write updated JSON state
      -> optionally push each check result to its Uptime Kuma push URL
      -> exit
```

The process should be short-lived. Scheduling belongs to systemd.

## Scheduling

The runner should not implement its own scheduler.

Use one shared INI config with a `schedule` key per check section:

```ini
[monerod]
enabled=1
schedule=normal
check=monerod_sync

[monero_wallet_rpc]
enabled=1
schedule=fast
check=monero_wallet_rpc

[bitcoind]
enabled=1
schedule=slow
check=bitcoind_sync
```

Then use multiple systemd timers that call the same runner with different
schedule selectors:

```text
service-check-fast.timer   -> service-check@fast.service   -> --schedule fast
service-check-normal.timer -> service-check@normal.service -> --schedule normal
service-check-slow.timer   -> service-check@slow.service   -> --schedule slow
```

Suggested cadence:

| Schedule | Interval | Purpose |
| --- | --- | --- |
| `fast` | 1 minute | Lightweight reachability checks. |
| `normal` | 5 minutes | Normal service health checks. |
| `slow` | 15 to 30 minutes | Heavier or less volatile checks. |

The config layer should default missing `schedule` values to
`default_schedule`, or to `normal` if no default is configured.

Because multiple timers can fire close together, the runner should take a lock
around state read/write and notification decisions. The state file can remain
shared as long as state keys are based on the section name.

## Main Components

Planned package layout:

```text
service_check/
+-- cli.py
+-- config.py
+-- runner.py
+-- state.py
+-- notify.py
+-- kuma.py
+-- checks/
    +-- monero.py
    +-- bitcoin.py
    +-- wireguard.py
    +-- tcp.py
    +-- http.py
    +-- github.py
```

### `cli.py`

Responsibilities:

- parse command-line arguments
- select config path
- initialize logging
- call the runner
- return process exit code

Suggested arguments:

```text
--config /etc/service-check/service-check.ini
--schedule normal
--dry-run
--no-notify
--check monerod
--verbose
```

### `config.py`

Responsibilities:

- read the INI file
- parse `[global]`
- find enabled check sections for the requested schedule
- merge global defaults with per-check overrides
- validate required fields for each check type where practical

The config layer should not implement health logic. It should only transform INI
data into typed values the runner can use.

### `runner.py`

Responsibilities:

- resolve `check=...` through the check registry
- execute checks selected by `--schedule` or `--check`
- apply immediate retries
- convert exceptions to `UNKNOWN`
- aggregate results
- call state and notification logic
- call per-check Kuma push logic

The runner owns generic behavior. Check modules should not decide notification
policy.

### `state.py`

Responsibilities:

- read and write the JSON state file
- track last status per check
- track consecutive failure counts
- track first failure time
- track last notification time
- support recovery detection

The state file should be durable but simple. It is acceptable to rewrite the
whole JSON file each run.

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

Notification transport should stay outside the watchdog. For example, Telegram
delivery can live in `/usr/local/bin/telegram-notify`.

### `kuma.py`

Responsibilities:

- push each check result to its optional Uptime Kuma push URL
- map watchdog status to Kuma status
- include a concise status message

The watchdog should not expose a local HTTP endpoint unless a future use case
requires it.

### `checks/*`

Responsibilities:

- interrogate one service or protocol
- return a normalized result
- avoid notification decisions
- avoid direct state writes

Check modules may shell out to local tools when that is the safest interface,
for example `wg` or `bitcoin-cli`.

## Check Registry

Use a simple registry that maps config names to Python functions.

Example:

```python
CHECKS = {
    "monerod_sync": check_monerod_sync,
    "monero_wallet_rpc": check_monero_wallet_rpc,
    "bitcoind_sync": check_bitcoind_sync,
    "wireguard_peer": check_wireguard_peer,
    "tcp_port": check_tcp_port,
    "http_json": check_http_json,
    "github_release_update": check_github_release_update,
}
```

The INI references the stable check name:

```ini
[monerod]
enabled=1
check=monerod_sync
url=http://127.0.0.1:18081/json_rpc
```

Adding a new check should require:

- adding or editing one check module
- registering the function
- adding example config
- adding README documentation

## Result Contract

Every check returns the same result shape.

Suggested dataclass:

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
```

The rendering context should include:

- runner fields such as `hostname`, `section`, and `check`
- result fields such as `name`, `status`, and `message`
- state fields such as `failure_count`
- all keys from `CheckResult.details`

Template rendering should be intentionally limited:

- support only `{key}` replacement
- do not support expressions, function calls, conditionals, or loops
- do not invoke a shell while rendering
- handle missing placeholders without crashing the watchdog run

The configured `failure_message` should be treated as an alert presentation
template. The original `CheckResult.message` should still be stored in details or
state when useful for debugging.

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

`fail_after` means the number of failed scheduled runs required before sending a
new failure alert.

Example with `fail_after=3`:

```text
12:00 CRIT, failure count 1, no alert
12:05 CRIT, failure count 2, no alert
12:10 CRIT, failure count 3, alert
```

This protects against short restarts and brief network blips.

## Notification Policy

Suggested policy:

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

Checks may opt into `WARN` notifications with `notify_on_warn=1`. This should be
used sparingly, for cases where `WARN` is the desired alert status, such as
`github_release_update` reporting that a new version is available.

## Aggregation

Per-check results should be aggregated for process exit code and optional
aggregate reporting.

Suggested aggregate rules:

- any `CRIT`: aggregate `CRIT`
- else any `UNKNOWN`: aggregate `UNKNOWN`
- else any `WARN`: aggregate `WARN`
- otherwise `OK`

Suggested process exit codes:

| Exit Code | Meaning |
| --- | --- |
| `0` | All checks healthy or only warnings, depending on policy. |
| `1` | At least one check is `CRIT` or `UNKNOWN`. |
| `2` | Configuration error or runner error. |

## Uptime Kuma Mapping

Kuma push URLs should be configured per check section.

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

If a section has no `kuma_push_url`, Kuma push should be skipped for that check.

Suggested mapping:

| Check Status | Kuma |
| --- | --- |
| `OK` | up |
| `WARN` | up with warning message |
| `CRIT` | down |
| `UNKNOWN` | down |

The Kuma message should use the same message rendering path as local alerts, so
placeholders from `CheckResult.details` are available.

An optional aggregate Kuma monitor can be supported with
`aggregate_kuma_push_url`, but it should be secondary. Per-check push URLs are
the preferred model because they provide clearer dashboard rows and separate
history per service.

## Versioning And Updates

The installed application should expose a local version. Update detection should
be implemented as a normal check function that uses the same runner, state,
message rendering, notification, scheduling, and Kuma push behavior as service
health checks.

Recommended local version source:

```python
# service_check/__init__.py
__version__ = "0.1.0"
```

Recommended release tag format:

```text
v0.1.0
v0.2.0
v1.0.0
```

The update checker should compare the local version against the GitHub release
`tag_name`, after stripping an optional leading `v`.

Suggested CLI:

```text
--version
--check service_check_update
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

Recommended config:

```ini
[service_check_update]
enabled=1
schedule=slow
check=github_release_update
repo=your-github-user/service-check
check_prereleases=0
fail_after=1
repeat_after=86400
notify_on_warn=1
kuma_push_url=https://kuma.example.com/api/push/service-check-update-token
failure_message=service-check update available: {current_version} -> {latest_version} ({release_url})
```

The result details should include:

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

Automatic installation should not be enabled by default. A watchdog that can
self-modify as root is more operationally risky than one that only reports that
an update is available.

## Secrets

Secrets should not live in the main config or repo examples.

Use file references:

```ini
rpc_password_file=/etc/service-check/secrets/monero-wallet-rpc.pw
```

Check functions should read secret files only when needed.

## Error Handling

Expected failures should return `CRIT` or `WARN`.

Examples:

- service responds but is not synced: `CRIT`
- peer count below threshold: `WARN` or `CRIT`, depending on check policy
- TCP port refused: `CRIT`

Unexpected failures should return `UNKNOWN`.

Examples:

- invalid config value
- malformed JSON
- missing local command
- command output cannot be parsed

The runner should catch uncaught check exceptions and convert them to `UNKNOWN`
so one bad check does not prevent the rest from running.

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
