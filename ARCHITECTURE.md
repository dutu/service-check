# Architecture

`service-check` is a local watchdog runner, not a daemon and not a full
monitoring framework.

The core idea is to keep the runner generic while keeping health logic explicit.
The runner knows how to load config, execute checks, retry, persist state, and
notify. Individual check functions know how to interrogate one service.

## Runtime Flow

```text
systemd timer
  -> service-check CLI
      -> load INI config
      -> build enabled check list
      -> dispatch each check by registered check name
      -> retry failed checks inside the current run
      -> normalize results to OK, WARN, CRIT, or UNKNOWN
      -> load previous JSON state
      -> decide whether to notify
      -> write updated JSON state
      -> optionally push aggregate status to Uptime Kuma
      -> exit
```

The process should be short-lived. Scheduling belongs to systemd.

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
--dry-run
--no-notify
--check monerod
--verbose
```

### `config.py`

Responsibilities:

- read the INI file
- parse `[global]`
- find enabled check sections
- merge global defaults with per-check overrides
- validate required fields for each check type where practical

The config layer should not implement health logic. It should only transform INI
data into typed values the runner can use.

### `runner.py`

Responsibilities:

- resolve `check=...` through the check registry
- execute checks
- apply immediate retries
- convert exceptions to `UNKNOWN`
- aggregate results
- call state and notification logic
- call Kuma push logic

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

- push aggregate status to an optional Uptime Kuma push URL
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

## Aggregation

Per-check results should be aggregated for process exit code and Kuma push.

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

If configured, push one aggregate result after each run.

Suggested mapping:

| Aggregate Status | Kuma |
| --- | --- |
| `OK` | up |
| `WARN` | up with warning message |
| `CRIT` | down |
| `UNKNOWN` | down |

For more detailed dashboards, use multiple Kuma push URLs in the future. The MVP
should start with one aggregate push URL.

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
