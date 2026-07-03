# Release Notes

## service-check 0.5.0

### Changed

- Consolidated the main example config by making `examples/service-check.ini` production-safe and using it as the installer source.

## service-check 0.4.0

### Added

- Added a generic `ping` check for ICMP reachability monitoring.


## service-check 0.3.1

### Fixed

- Made `pyproject.toml` the effective single source of truth for the runtime version.
- `service-check --version`, `--doctor`, state metadata, and `github_release_update` now use installed package metadata when available.
- Local source-tree runs fall back to reading `[project].version` from `pyproject.toml`.

### Changed

- Bumped package version to `0.3.1`.

### Notes

- This release removes the need to manually keep `service_check/__init__.py` in sync with `pyproject.toml`.

## service-check 0.3.0

### Added

- Added `monerod` health check with systemd status, config discovery, TCP port checks, RPC sync state, stall detection, and peer thresholds.
- Added `public_ip_reputation` check for Tor, VPN, proxy, hosting/datacenter, and abuse classification.
- Added `socks_proxy` check for SOCKS5 proxy functionality, including 3proxy use cases and optional username/password auth.
- Added per-check README files and ordered drop-in example configs under `service_check/checks/*`.
- Added unit tests for public IP reputation verdicts, interface validation, runner notification transitions, and SOCKS5 protocol behavior.

### Changed

- Added `problem_code` / `problem_codes` result details for targeted failure handling.
- Added `failure_message.<problem_code>` templates for code-specific alerts.
- Improved degraded-state notification behavior, especially `CRIT -> WARN -> OK` transitions.
- Improved GitHub release check failure details and alert messages.
- Restructured example check configs into ordered `*.example.ini` drop-ins.
- Expanded check development docs and metadata guidance.

### Migration Notes

- Review copied check examples: old unnumbered example filenames were replaced by ordered `*.example.ini` files.
- If using custom alert templates, consider adding `failure_message.<problem_code>` overrides for more precise notifications.
- Before tagging, update `service_check/__init__.py`; it still reports `__version__ = "0.2.0"` while `pyproject.toml` is `0.3.0`.

**Full Changelog**: https://github.com/dutu/service-check/compare/v0.2.0...v0.3.0

## service-check 0.2.0

### Added

- Added `github_release_update` support for checking installed version against the latest GitHub Release.
- Added `--doctor` diagnostics for config, runtime, Python version, command path, package path, notifications, Kuma, and systemd.
- Added `--validate-config`, `--print-config`, `--results-for`, and `--describe-check`.
- Added check metadata support for documenting result fields, template fields, and statuses.
- Added runtime controls: `max_run_seconds` and `max_lock_hold_minutes`.

### Changed

- Split shared check settings into a dedicated `[default]` section.
- Renamed config keys for clarity:
  - `default_interval_minutes` -> `interval_minutes`
  - `default_timeout` -> `timeout_seconds`
  - `default_retry_delay` -> `retry_delay_seconds`
  - `default_repeat_after` -> `notify_repeat_after_minutes`
  - `notify_on_success_once` -> `notify_on_first_success`
- Moved `notify_on_recovery` from `[global]` to `[default]`.
- Improved runner logging, status tracking, retry logging, and serialized result storage.
- Persisted `service_check_version` in the state file for traceability.
- Expanded example config comments to document all valid `[global]` and `[default]` options.

### Migration Notes

- Review existing configs against `examples/service-check.ini`.
- Move old `default_*` options from `[global]` into `[default]`.
- Use minutes for `interval_minutes` and `notify_repeat_after_minutes`; use seconds for `timeout_seconds` and `retry_delay_seconds`.

## service-check 0.1.0

First release.
