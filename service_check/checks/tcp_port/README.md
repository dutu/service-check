# tcp_port

Checks whether a TCP connection can be established to a host and port.

## Parameters

Required:

- `host`: hostname or IP address to connect to
- `port`: TCP port number

Optional:

- `timeout_seconds`: connection timeout in seconds, defaults to `[default] timeout_seconds`
- `failure_message`: alert template used when the port is unreachable
- `success_message`: message template used for OK status, recovery notifications, and Kuma OK pushes
- `interval_seconds`: how often this check runs
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
- `port`
- `timeout_seconds`
- `elapsed_ms`
- `error`, only on failure

## Example

```ini
[electrs_tcp]
enabled=1
check=tcp_port
interval_seconds=60
host=127.0.0.1
port=50001
timeout_seconds=2
# notify_topic=infra
# notify_cmd=/usr/local/bin/telegram-notify --level {notify_level} {notify_topic}
failure_message=TCP port {host}:{port} is down: {error}
success_message=TCP port {host}:{port} is reachable in {elapsed_ms}ms
```
