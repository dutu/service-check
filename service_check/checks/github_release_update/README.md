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
- `timeout_seconds`: seconds to wait for the GitHub API request
- `expected_version`: manual available release version, with optional leading `v`;
  when set, the check uses this value instead of calling GitHub
- `current_version`: override detected version, primarily for tests
- `failure_message`: alert template used when the version assertion fails
- `failure_message.<problem_code>`: alert template used for a specific problem code
- `success_message`: message template used for OK status, recovery notifications, and Kuma OK pushes
- `notify_on_first_success`: notify the first successful run once
- `interval_minutes`: how often this check runs
- `retries`: immediate retries before the run is considered failed
- `retry_delay_seconds`: seconds between immediate retries
- `fail_after`: failed runs required before local notification
- `notify_repeat_after_minutes`: minutes before repeating a notification for an unresolved problem
- `notify_cmd`: local notification command override for this check
- `kuma_push_url`: optional per-check Uptime Kuma push URL

If `expected_version` is not set, the check calls:

```text
https://api.github.com/repos/{repository}/releases/latest
```

and compares the installed package version with the response `tag_name` using
PEP 440 version ordering. Final, development, alpha, beta, and release candidate
versions are supported, with or without a leading `v`.

The check returns `OK` when the installed version is equal to or newer than the
latest GitHub Release. A newer local version can happen while running unreleased
development builds.

## Details

The check returns these `details` keys for message templates:

- `current_version`
- `current_version_tag`
- `expected_version`
- `latest_version`
- `available_version`
- `available_version_tag`
- `repository`
- `problem_code`, only on failure or warning
- `problem_codes`, only on failure or warning
- `error`, only for invalid version config

## Problem Codes

- `update_available`: installed version is behind the expected or latest version
- `version_newer`: installed version is newer than the expected or latest version
- `invalid_config`: GitHub repository config is invalid
- `fetch_failed`: latest release could not be fetched
- `invalid_version`: current or latest version is not a valid PEP 440 version

## Example

```ini
[github_release_update]
enabled=1
check=github_release_update
repository=dutu/service-check
interval_minutes=1440
notify_repeat_after_minutes=1440
notify_on_warn=1
notify_on_first_success=1
success_message=service-check {current_version_tag} is up-to-date
failure_message=service-check version check problem: {message}
failure_message.update_available=service-check new version available: current={current_version_tag}, available={available_version_tag}
failure_message.version_newer=service-check local version {current_version_tag} is newer than available {available_version_tag}
failure_message.invalid_config=service-check version check has invalid GitHub config for {repository}: {error}
failure_message.fetch_failed=service-check could not fetch latest release for {repository}: {error}
failure_message.invalid_version=service-check version check got invalid version data: current={current_version}, available={available_version}, error={error}
```
