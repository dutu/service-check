# heartbeat

Reports `OK` when the `service-check` runner executes on this machine.

This check is intended for Uptime Kuma Push monitors. The check does not probe a
local service; if the machine, timer, network, Python runtime, or runner fails,
pushes stop and Kuma marks the monitor down after its configured grace period.

## Parameters

Required:

- none

Optional:

- `success_message`: message template used for OK status, first-success notifications, and Kuma OK pushes
- `interval_minutes`: how often this check runs
- `notify_on_first_success`: send one local notification after the first successful run
- `notify_cmd`: local notification command override for this check
- `kuma_push_url`: optional per-check Uptime Kuma push URL

The runner preserves existing query parameters in `kuma_push_url`, then adds
`status=up` and `msg=<rendered message>`. For this check, `msg` uses
`success_message` when configured; otherwise it uses the built-in heartbeat
message.

## Details

The check returns these `details` keys for message templates:

- `heartbeat`

## Problem Codes

This check has no problem codes. Failure is represented by missing Kuma pushes.

## Example

```ini
[machine_heartbeat]
enabled=1
check=heartbeat
interval_minutes=1
success_message={hostname} service-check heartbeat OK
notify_on_first_success=1
kuma_push_url=https://kuma.example.com/api/push/service-check-heartbeat-token
```
