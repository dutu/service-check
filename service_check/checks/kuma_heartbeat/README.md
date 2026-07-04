# kuma_heartbeat

Pushes a heartbeat to an Uptime Kuma Push monitor and reports whether that push
succeeded.

Use this check when you want one local section to prove both facts:

- `service-check` is running on the machine.
- The machine can reach the configured Uptime Kuma server and push URL.

If the push fails, this check returns `CRIT`. That means the normal local
notification flow can alert through `notify_cmd`, for example Telegram. This is
different from `kuma_push_url`, which is a runner side effect for normal checks
and does not change the check status when Kuma is unreachable.

## Parameters

Required:

- `heartbeat_url`: Uptime Kuma Push monitor URL used by this check

Optional:

- `heartbeat_message`: message sent to Uptime Kuma; defaults to `{hostname} service-check Kuma heartbeat OK`
- `success_message`: local/recovery/first-success template; use `{pushed_message}` to match the Kuma message
- `failure_message`: alert template used when the push fails
- `interval_minutes`: how often this check runs
- `timeout_seconds`: network timeout for the Kuma push
- `notify_on_first_success`: send one local notification after the first successful run
- `notify_cmd`: local notification command override for this check

Do not set `kuma_push_url` on this check. `heartbeat_url` is the push being
tested. `kuma_push_url` is the generic runner-level side effect used by other
checks and would create a second, independent push after this check completes.

The check preserves existing query parameters in `heartbeat_url`, then adds
`status=up` and `msg=<heartbeat_message>`.

The runner schedules checks by wall-clock interval buckets rather than requiring
a full elapsed interval since the previous completion. With a one-minute systemd
timer and `interval_minutes=1`, this check is due once in each minute bucket, so
normal timer jitter does not cause skipped pushes.

## Details

The check returns these `details` keys for message templates:

- `hostname`
- `pushed_message`
- `timeout_seconds`
- `problem_code`, only on failure or unknown results
- `problem_codes`, only on failure or unknown results
- `error`, only on failure

## Problem Codes

- `missing_heartbeat_url`: required `heartbeat_url` config is missing
- `kuma_push_failed`: the HTTP push to Uptime Kuma failed

## Example

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
