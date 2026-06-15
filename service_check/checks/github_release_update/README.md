# github_release_update

Checks whether the installed `service-check` package version is current.

This is intended as a default smoke check for new installations. It verifies
that the runner can load config, resolve a check module, execute it, update
state, and optionally push to Uptime Kuma without depending on a local TCP
service being open.

## Parameters

Optional:

- `expected_version`: available release version, with optional leading `v`
- `current_version`: override detected version, primarily for tests
- `failure_message`: alert template used when the version assertion fails
- `success_message`: message template used for OK status, recovery notifications, and Kuma OK pushes
- `notify_on_success_once`: notify the first successful run once
- `interval_minutes`: how often this check runs
- `retries`: immediate retries before the run is considered failed
- `retry_delay`: seconds between immediate retries
- `fail_after`: failed runs required before local notification
- `repeat_after`: seconds before repeating an unresolved alert
- `notify_cmd`: local notification command override for this check
- `kuma_push_url`: optional per-check Uptime Kuma push URL

If `expected_version` is not set, the check returns `OK` with the installed
package version.

## Details

The check returns these `details` keys for message templates:

- `current_version`
- `expected_version`
- `latest_version`
- `available_version`
- `error`, only for invalid version config

## Example

```ini
[github_release_update]
enabled=1
check=github_release_update
interval_minutes=1440
repeat_after=86400
notify_on_warn=1
notify_on_success_once=1
success_message=service-check {current_version} is up-to-date
failure_message=service-check new version available: current={current_version}, available={expected_version}
```
