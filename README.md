# service-check

`service-check` is a small local watchdog runner for self-hosted infrastructure.

It is designed for machines that need a few service-specific health checks without
running a full monitoring platform locally. The runner reads an INI file, executes
hardcoded check functions, stores previous state, and sends notifications only
when something meaningfully changes.

Typical targets:

- Monero daemon and wallet RPC
- Bitcoin Core
- WireGuard peers
- Electrs or Fulcrum
- TCP ports
- HTTP or JSON endpoints
- Local helper services such as proxies or exporters

The intended monitoring split is:

- `service-check`: local health computation
- systemd timer: scheduling
- state file: anti-spam and recovery detection
- notification helper: alert delivery, for example Telegram
- Uptime Kuma: optional dashboard and external alerting

## Design Goals

- Keep service logic explicit in Python check functions.
- Keep machine-specific values in an INI file.
- Avoid a no-code monitoring framework.
- Avoid alert spam during short restarts.
- Run cleanly from systemd without a resident daemon process.
- Be easy to clone onto multiple machines with different enabled checks.

## Repository Layout

Planned layout:

```text
service-check/
+-- service_check/
|   +-- __init__.py
|   +-- cli.py
|   +-- config.py
|   +-- runner.py
|   +-- state.py
|   +-- notify.py
|   +-- kuma.py
|   +-- checks/
|       +-- __init__.py
|       +-- tcp_port/
|       |   +-- __init__.py
|       |   +-- check.py
|       |   +-- README.md
|       |   +-- example.ini
|       +-- github_release_update/
|           +-- __init__.py
|           +-- check.py
|           +-- README.md
|           +-- example.ini
+-- examples/
|   +-- tcp.ini
|   +-- monero.ini
|   +-- bitcoin.ini
|   +-- wireguard.ini
|   +-- full.ini
+-- systemd/
|   +-- service-check.service
|   +-- service-check.timer
+-- install.sh
+-- uninstall.sh
+-- ARCHITECTURE.md
+-- README.md
```

## How It Works

The normal runtime flow is:

```text
systemd timer every minute
  -> service-check
      -> read /etc/service-check/service-check.ini
      -> read /etc/service-check/service-check.ini.d/*.ini
      -> execute enabled checks whose interval has elapsed
      -> retry transient failures inside the current run
      -> compute OK, WARN, CRIT, or UNKNOWN
      -> update /var/lib/service-check/state.json
      -> notify only on threshold, recovery, or repeat interval
      -> optionally push each check result to its Uptime Kuma push URL
```

The script is not meant to run as a daemon. systemd starts it every minute and
the process exits after one check cycle.

## Current Implementation

The initial implementation supports the core runner and the `tcp_port` check.

Run due checks directly from the repo:

```bash
python -m service_check.cli --config examples/tcp.ini --dry-run
```

Run with a main config and drop-in directory:

```bash
python -m service_check.cli --config examples/service-check.ini --all --dry-run
```

Run all enabled checks, ignoring `interval_minutes`:

```bash
python -m service_check.cli --config examples/tcp.ini --all --dry-run
```

Or run one enabled section, ignoring `interval_minutes`:

```bash
python -m service_check.cli --config examples/tcp.ini --check example_tcp_open --dry-run
```

The example config uses local state paths under `./.service-check/` so it can be
tested without root. Edit `host` and `port` in `examples/tcp.ini` for a real
service on your machine.

## Scheduling Model

Use one config file and one systemd timer.

The systemd timer runs once per minute. Each check section defines its own
`interval_minutes` value:

```ini
[monerod]
enabled=1
check=monerod_sync
interval_minutes=5

[wg_btrad]
enabled=1
check=wireguard_peer
interval_minutes=5

[bitcoind]
enabled=1
check=bitcoind_sync
interval_minutes=30
```

The runner decides whether each check is due from state:

```text
now - last_run_at >= interval_minutes
```

Recommended intervals:

| Interval | Typical Checks |
| --- | --- |
| `1` | TCP ports, local RPC reachability, wallet RPC |
| `5` | Monero sync health, WireGuard peers, HTTP JSON checks |
| `30` | Bitcoin full sync checks, Electrs, update checks, less volatile services |

This keeps scheduling in systemd while avoiding artificial `fast`, `normal`, and
`slow` buckets.

The runner uses one shared state file keyed by section name. It stores
`last_run_at` for each section and takes a process or state-file lock while
running checks and writing state.

## Configuration

Configuration uses INI sections.

`[global]` defines defaults and common behavior. Each service section enables one
check module and provides only the inputs that module needs.

The runner reads the main config first, then optional drop-in files:

```text
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/*.ini
```

Drop-ins load in lexical order. Later files override earlier values for the same
section and key. Missing `.d` directories are ignored. Only `*.ini` files are
loaded.

Recommended split:

```text
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/
+-- 10-monero.ini
+-- 20-wireguard.ini
+-- 30-electrs.ini
+-- 90-update-check.ini
```

Keep `[global]` primarily in the main file. Put individual check sections in
drop-in files.

Example:

```ini
[global]
hostname=home-mt
state_file=/var/lib/service-check/state.json
notify_cmd=/usr/local/bin/telegram-notify infra
default_interval_minutes=5
default_retries=2
default_retry_delay=5
default_fail_after=3
default_repeat_after=3600
notify_on_recovery=1

[monerod]
enabled=1
check=monerod_sync
interval_minutes=5
url=http://127.0.0.1:18081/json_rpc
min_outgoing_peers=4
max_height_lag=10
fail_after=2
kuma_push_url=https://kuma.example.com/api/push/monerod-token
failure_message=Monero daemon unhealthy: height={height}, target={target_height}, outgoing_peers={outgoing_peers}

[monero_wallet_rpc]
enabled=1
check=monero_wallet_rpc
interval_minutes=1
url=http://192.168.10.51:38084/json_rpc
rpc_user=monerorpc
rpc_password_file=/etc/service-check/secrets/monero-wallet-rpc.pw
kuma_push_url=https://kuma.example.com/api/push/monero-wallet-rpc-token
failure_message=Monero wallet RPC is not responding

[wg_btrad]
enabled=1
check=wireguard_peer
interval_minutes=5
interface=wg0
peer_name=btrad
max_latest_handshake_age=180
fail_after=3
kuma_push_url=https://kuma.example.com/api/push/wg-btrad-token
failure_message=WireGuard peer btrad handshake is stale

[electrs]
enabled=0
check=tcp_port
interval_minutes=1
host=127.0.0.1
port=50001
failure_message=Electrs TCP port is down
```

## Global Settings

Common global keys:

| Key | Purpose |
| --- | --- |
| `hostname` | Name included in notifications and Kuma messages. |
| `state_file` | JSON state path used for fail counters and recovery detection. |
| `lock_file` | Optional lock path. Defaults to `state_file` plus `.lock`. |
| `notify_cmd` | Local command used to send alerts. |
| `default_interval_minutes` | Interval used when a check section does not define `interval_minutes`. |
| `default_retries` | Immediate retries inside one watchdog run. |
| `default_retry_delay` | Delay in seconds between immediate retries. |
| `default_fail_after` | Failed due runs required before alerting. |
| `default_repeat_after` | Seconds before repeating an alert for a still-broken check. |
| `notify_on_recovery` | Whether to notify when a failed check recovers. |

Per-check sections may define or override:

- `interval_minutes`
- `retries`
- `retry_delay`
- `fail_after`
- `repeat_after`
- `failure_message`
- `success_message`
- `notify_cmd`
- `kuma_push_url`
## Message Templates

`failure_message` and `success_message` may include simple placeholders that are
replaced from the check result before an alert or Kuma push is sent.

Example:

```ini
[monerod]
enabled=1
check=monerod_sync
interval_minutes=5
url=http://127.0.0.1:18081/json_rpc
failure_message=Monero daemon unhealthy: height={height}, target={target_height}, lag={height_lag}, outgoing_peers={outgoing_peers}
success_message=Monero daemon healthy: height={height}, outgoing_peers={outgoing_peers}
```

If the check returns:

```python
CheckResult(
    name="monerod",
    status="CRIT",
    message="Monero daemon is behind target height",
    details={
        "height": 3420000,
        "target_height": 3420020,
        "height_lag": 20,
        "outgoing_peers": 3,
    },
)
```

The alert message can become:

```text
Monero daemon unhealthy: height=3420000, target=3420020, lag=20, outgoing_peers=3
```

Recommended built-in placeholders:

| Placeholder | Source |
| --- | --- |
| `{hostname}` | `[global] hostname` |
| `{section}` | INI section name, for example `monerod` |
| `{check}` | Check function name, for example `monerod_sync` |
| `{name}` | Result name |
| `{status}` | Result status |
| `{message}` | Result message |
| `{failure_count}` | Consecutive failed due runs |
| `{details_key}` | Any key returned in `CheckResult.details`, for example `{height}` |

Keep this deliberately simple:

- use `{key}` placeholders only
- do not support expressions, conditionals, loops, or shell expansion
- leave unknown placeholders unchanged or replace them with a clear marker
- never let a bad template prevent state updates or other checks from running

Use `failure_message` for `WARN`, `CRIT`, and `UNKNOWN`. Use `success_message`
for `OK`, recovery notifications, and Kuma `OK` pushes. If `success_message` is
missing, the runner uses the check's normal `CheckResult.message`.

## Status Levels

Checks should return one of four statuses:

| Status | Meaning |
| --- | --- |
| `OK` | Healthy. |
| `WARN` | Degraded but still usable. |
| `CRIT` | Broken or outside an acceptable threshold. |
| `UNKNOWN` | Check failed unexpectedly, usually bad config, parse failure, or command error. |

Suggested alert behavior:

| Transition | Behavior |
| --- | --- |
| `OK -> CRIT` | Notify after `fail_after` failed runs. |
| `WARN -> CRIT` | Notify after `fail_after` failed runs. |
| `CRIT -> OK` | Notify recovery if enabled. |
| `CRIT -> CRIT` | Do not spam unless `repeat_after` elapsed. |
| `OK -> WARN` | Usually do not alert. |

## Checks

Planned initial check names:

| Check | Purpose |
| --- | --- |
| `monerod_sync` | Verify Monero daemon RPC health, sync lag, offline state, and outgoing peers. |
| `monero_wallet_rpc` | Verify wallet RPC responds and authenticates. |
| `wireguard_peer` | Verify interface, peer presence, and latest handshake age. |
| `tcp_port` | Verify a TCP port accepts connections. |
| `http_json` | Verify an HTTP endpoint responds with valid JSON and optional expected fields. |
| `bitcoind_sync` | Verify Bitcoin Core sync state and peer count. |
| `github_release_update` | Check whether a newer `service-check` GitHub Release is available. |

## Monero Checks

For `monerod_sync`, useful health criteria are:

- RPC responds.
- `offline` is false.
- `target_height` is zero or local height is close to target height.
- outgoing peers are at or above `min_outgoing_peers`.
- optional synchronized flag is true if available.

Recommended config:

```ini
[monerod]
enabled=1
check=monerod_sync
interval_minutes=5
url=http://127.0.0.1:18081/json_rpc
min_outgoing_peers=4
max_height_lag=10
kuma_push_url=https://kuma.example.com/api/push/monerod-token
failure_message=Monero daemon unhealthy: height={height}, target={target_height}, lag={height_lag}, outgoing_peers={outgoing_peers}
```

## WireGuard Checks

For WireGuard, the basic check should inspect:

- interface exists
- peer exists
- latest handshake age is below the configured threshold

Handshake age can be stale on idle tunnels. If the tunnel is expected to be
continuously usable, add an optional ping check through the tunnel.

Example:

```ini
[wg_btrad]
enabled=1
check=wireguard_peer
interval_minutes=5
interface=wg0
peer_name=btrad
max_latest_handshake_age=180
ping_host=10.8.0.2
kuma_push_url=https://kuma.example.com/api/push/wg-btrad-token
failure_message=WireGuard peer btrad is stale: latest_handshake_age={latest_handshake_age}s
```

## Bitcoin Checks

For Bitcoin Core, useful health criteria are:

- `bitcoin-cli` can reach the node.
- `initialblockdownload` is false.
- `blocks` is close to `headers`.
- peer count is above the configured minimum.
- `verificationprogress` is close to 1.

Example:

```ini
[bitcoind]
enabled=1
check=bitcoind_sync
interval_minutes=30
bitcoin_cli=/usr/bin/bitcoin-cli
min_peers=6
max_block_lag=2
kuma_push_url=https://kuma.example.com/api/push/bitcoind-token
failure_message=Bitcoin Core unhealthy: blocks={blocks}, headers={headers}, peers={peers}
```

## Notification Command

`service-check` should delegate alert delivery to an existing local command.

Example:

```ini
[global]
notify_cmd=/usr/local/bin/telegram-notify infra
```

The runner can call it with a single composed message:

```text
/usr/local/bin/telegram-notify infra "[home-mt] monerod CRIT: Monero daemon is unhealthy or not synced"
```

This keeps notification transport separate from health-check logic.

## Uptime Kuma Push

Kuma integration should be optional and per check.

Create one Kuma push monitor for each watchdog section you want on the dashboard,
then put that monitor's push URL in the same INI section.

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

Recommended per-check mapping:

| Check Status | Kuma Push |
| --- | --- |
| `OK` | up |
| `WARN` | up with warning message |
| `CRIT` | down |
| `UNKNOWN` | down |

The Kuma message should use the rendered failure or recovery message, including
any placeholders.

If a section has no `kuma_push_url`, the runner should simply skip Kuma for that
check.

An optional aggregate Kuma monitor may be added later, but it should not replace
per-check push URLs. Per-check monitors give a clearer dashboard and better
history.

Optional aggregate example:

```ini
[global]
aggregate_kuma_push_url=https://kuma.example.com/api/push/all-service-checks-token
```

## Versioning And Updates

`service-check` should be versioned and able to check GitHub for newer releases.

Recommended versioning model:

- use semantic versions such as `0.1.0`, `0.2.0`, and `1.0.0`
- tag releases in Git as `v0.1.0`, `v0.2.0`, and `v1.0.0`
- expose the installed version with `service-check --version`
- store the Python package version in `service_check/__init__.py`
- treat GitHub Releases as the update source, not arbitrary branch heads

Recommended commands:

```bash
service-check --version
service-check --check service_check_update
sudo service-check --self-update
```

Default behavior should be conservative:

- update detection should run as a normal configured check
- the update check must not modify files
- `--self-update` must require explicit user action
- automatic update checks are acceptable
- automatic update installation should be opt-in, not default

Recommended update check config:

```ini
[service_check_update]
enabled=1
check=github_release_update
interval_minutes=1440
repo=your-github-user/service-check
check_prereleases=0
fail_after=1
repeat_after=86400
kuma_push_url=https://kuma.example.com/api/push/service-check-update-token
failure_message=service-check update available: {current_version} -> {latest_version} ({release_url})
```

For GitHub, the update checker should call the latest release endpoint:

```text
https://api.github.com/repos/OWNER/REPO/releases/latest
```

Then compare the returned `tag_name` against the local version.

If `check_prereleases=0`, only stable GitHub Releases should be considered.
Prereleases are useful later for testing, but stable machines should ignore them
by default.

The update checker should use the normal notification command if a newer version
is available:

```text
[home-mt] service-check update available: 0.1.0 -> 0.2.0
```

The update check should return:

| Situation | Suggested Status |
| --- | --- |
| installed version is latest | `OK` |
| newer release exists | `WARN` |
| GitHub cannot be reached or response cannot be parsed | `UNKNOWN` |

For local notifications, this is the main exception to the usual `WARN` policy:
the update check should support alerting on `WARN`, because `WARN` is the correct
status for "new version available".

Suggested per-check option:

```ini
notify_on_warn=1
```

With `repeat_after=86400`, the machine can remind you once per day while an
update remains available.

Installing updates is different from checking updates. Installation needs write
access to `/opt/service-check`, `/usr/local/bin`, and possibly systemd unit
files, so it should run under `sudo`.

Recommended install strategy:

- download the release tarball or zipball into a temporary directory
- verify the release version matches the expected tag
- stop or avoid concurrent watchdog runs with the normal lock
- replace `/opt/service-check`
- preserve `/etc/service-check/service-check.ini`
- preserve `/var/lib/service-check/state.json`
- reinstall or refresh systemd units if they changed
- run `systemctl daemon-reload`
- run `service-check --version`

Do not update directly from the `main` branch on production machines. Releases
make rollback and debugging much cleaner.

## Installation

Planned installation targets:

```text
/opt/service-check
/usr/local/bin/service-check
/etc/service-check/service-check.ini
/etc/service-check/service-check.ini.d/
/var/lib/service-check/state.json
```

Planned install flow:

```bash
git clone https://github.com/you/service-check.git
cd service-check
sudo ./install.sh
sudo cp examples/service-check.ini /etc/service-check/service-check.ini
sudo mkdir -p /etc/service-check/service-check.ini.d
sudo cp examples/service-check.ini.d/10-tcp.ini /etc/service-check/service-check.ini.d/10-tcp.ini
sudo systemctl enable --now service-check.timer
```

The installer should:

- create `/opt/service-check`
- copy the application there
- create `/usr/local/bin/service-check`
- create `/etc/service-check`
- create `/etc/service-check/service-check.ini.d`
- copy an example config only if no config exists
- create `/var/lib/service-check`
- install systemd service and timer units
- enable the timer only when explicitly requested or documented

## systemd

Use one service and one timer. The timer runs once per minute; the runner decides
which checks are due from each section's `interval_minutes` and `last_run_at`.

`service-check.service`:

```ini
[Unit]
Description=Run service-check watchdog

[Service]
Type=oneshot
ExecStart=/usr/local/bin/service-check --config /etc/service-check/service-check.ini
```

`service-check.timer`:

```ini
[Unit]
Description=Run service-check watchdog every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=service-check.service

[Install]
WantedBy=timers.target
```

This gives per-check intervals without duplicating config files or systemd units.

## Secrets

Do not put secrets in the repo or example configs.

Use file references:

```ini
rpc_password_file=/etc/service-check/secrets/monero-wallet-rpc.pw
```

Recommended permissions:

```bash
sudo install -d -m 0700 /etc/service-check/secrets
sudo install -m 0600 monero-wallet-rpc.pw /etc/service-check/secrets/monero-wallet-rpc.pw
```

## Development Notes

Keep the extension model simple:

- Add service-specific logic as a Python function.
- Register the function under a stable check name.
- Add only service inputs and thresholds to the INI file.
- Return the standard check result shape.
- Let the runner handle retries, state, notifications, and Kuma push.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the internal design.




