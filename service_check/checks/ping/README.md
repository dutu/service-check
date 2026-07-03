# ping

Checks whether a host responds to ICMP echo requests.

## Parameters

Required:

- `host`: hostname or IP address to ping

Optional:

- `count`: number of echo requests to send, defaults to `2`
- `timeout_seconds`: ping timeout in seconds, defaults to `[default] timeout_seconds`
- `failure_message`: alert template used when the host is unreachable
- `failure_message.<problem_code>`: alert template used for a specific problem code
- `success_message`: message template used for OK status, recovery notifications, and Kuma OK pushes
- `interval_minutes`: how often this check runs
- `retries`: immediate retries before the run is considered failed
- `retry_delay_seconds`: seconds between immediate retries
- `fail_after`: failed runs required before local notification
- `notify_repeat_after_minutes`: minutes before repeating a notification for an unresolved problem
- `notify_cmd`: local notification command override for this check
- `kuma_push_url`: optional per-check Uptime Kuma push URL

`notify_cmd` supports placeholders from result details and config keys, for
example `{notify_level}`, `{section}`, or `{notify_topic}`.

## Details

The check returns these `details` keys for message templates:

- `host`
- `count`
- `timeout_seconds`
- `elapsed_ms`
- `exit_code`, only when the ping command runs
- `problem_code`, only on failure or unknown results
- `problem_codes`, only on failure or unknown results
- `error`, only on failure

## Problem Codes

- `missing_host`: required `host` config is missing
- `invalid_count`: `count` is not a positive integer
- `invalid_timeout`: `timeout_seconds` is not a positive number
- `ping_command_missing`: no `ping` command is available
- `ping_failed`: ping command failed or timed out

## Example

```ini
[gateway_ping]
enabled=1
check=ping
interval_minutes=1
host=192.0.2.1
count=2
timeout_seconds=2
failure_message=Ping target {host} is unreachable: {error}
failure_message.ping_failed=Ping target {host} is unreachable: {error}
success_message=Ping target {host} is reachable in {elapsed_ms}ms
```
