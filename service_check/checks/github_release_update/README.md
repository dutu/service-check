# github_release_update

Checks whether the installed `service-check` package version is current by
comparing it with the latest GitHub Release tag.

This is intended as a default smoke check for new installations. It verifies
that the runner can load config, resolve a check module, execute it, update
state, and optionally push to Uptime Kuma without depending on a local TCP
service being open.

## Parameters

Optional:

- `repository`: GitHub repository in `owner/name` form; defaults to `dutu/service-check`
- `repo`: alias for `repository`
- `api_url`: override GitHub latest-release API URL, primarily for tests
- `timeout`: seconds to wait for the GitHub API request
- `expected_version`: manual available release version, with optional leading `v`;
  when set, the check uses this value instead of calling GitHub
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

If `expected_version` is not set, the check calls:

```text
https://api.github.com/repos/{repository}/releases/latest
```

and compares the installed package version with the response `tag_name`.

The check returns `OK` when the installed version is equal to or newer than the
latest GitHub Release. A newer local version can happen while running unreleased
development builds.

## Details

The check returns these `details` keys for message templates:

- `current_version`
- `expected_version`
- `latest_version`
- `available_version`
- `repository`
- `error`, only for invalid version config

## Example

```ini
[github_release_update]
enabled=1
check=github_release_update
repository=dutu/service-check
interval_minutes=1440
repeat_after=86400
notify_on_warn=1
notify_on_success_once=1
success_message=service-check {current_version} is up-to-date
failure_message=service-check new version available: current={current_version}, available={available_version}
```
