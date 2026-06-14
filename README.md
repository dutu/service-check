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
|       +-- monero.py
|       +-- bitcoin.py
|       +-- wireguard.py
|       +-- tcp.py
|       +-- http.py
+-- examples/
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
systemd timer
  -> service-check
      -> read /etc/service-check/service-check.ini
      -> execute enabled checks
      -> retry transient failures inside the current run
      -> compute OK, WARN, CRIT, or UNKNOWN
      -> update /var/lib/service-check/state.json
      -> notify only on threshold, recovery, or repeat interval
      -> optionally push aggregate status to Uptime Kuma
```

The script is not meant to run as a daemon. systemd starts it on a schedule and
the process exits after one check cycle.

## Configuration

Configuration uses INI sections.

`[global]` defines defaults and common behavior. Each service section enables one
hardcoded check function and provides only the inputs that function needs.

Example:

```ini
[global]
hostname=home-mt
state_file=/var/lib/service-check/state.json
notify_cmd=/usr/local/bin/telegram-notify infra
default_retries=2
default_retry_delay=5
default_fail_after=3
default_repeat_after=3600
notify_on_recovery=1

[monerod]
enabled=1
check=monerod_sync
url=http://127.0.0.1:18081/json_rpc
min_outgoing_peers=4
max_height_lag=10
fail_after=2
failure_message=Monero daemon unhealthy: height={height}, target={target_height}, outgoing_peers={outgoing_peers}

[monero_wallet_rpc]
enabled=1
check=monero_wallet_rpc
url=http://192.168.10.51:38084/json_rpc
rpc_user=monerorpc
rpc_password_file=/etc/service-check/secrets/monero-wallet-rpc.pw
failure_message=Monero wallet RPC is not responding

[wg_btrad]
enabled=1
check=wireguard_peer
interface=wg0
peer_name=btrad
max_latest_handshake_age=180
fail_after=3
failure_message=WireGuard peer btrad handshake is stale

[electrs]
enabled=0
check=tcp_port
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
| `notify_cmd` | Local command used to send alerts. |
| `default_retries` | Immediate retries inside one watchdog run. |
| `default_retry_delay` | Delay in seconds between immediate retries. |
| `default_fail_after` | Failed scheduled runs required before alerting. |
| `default_repeat_after` | Seconds before repeating an alert for a still-broken check. |
| `notify_on_recovery` | Whether to notify when a failed check recovers. |
| `kuma_push_url` | Optional Uptime Kuma push monitor URL. |

Per-check sections may override:

- `retries`
- `retry_delay`
- `fail_after`
- `repeat_after`
- `failure_message`

## Message Templates

`failure_message` may include simple placeholders that are replaced from the
check result before an alert is sent.

Example:

```ini
[monerod]
enabled=1
check=monerod_sync
url=http://127.0.0.1:18081/json_rpc
failure_message=Monero daemon unhealthy: height={height}, target={target_height}, lag={height_lag}, outgoing_peers={outgoing_peers}
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
| `{failure_count}` | Consecutive failed scheduled runs |
| `{details_key}` | Any key returned in `CheckResult.details`, for example `{height}` |

Keep this deliberately simple:

- use `{key}` placeholders only
- do not support expressions, conditionals, loops, or shell expansion
- leave unknown placeholders unchanged or replace them with a clear marker
- never let a bad template prevent state updates or other checks from running

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
url=http://127.0.0.1:18081/json_rpc
min_outgoing_peers=4
max_height_lag=10
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
interface=wg0
peer_name=btrad
max_latest_handshake_age=180
ping_host=10.8.0.2
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
bitcoin_cli=/usr/bin/bitcoin-cli
min_peers=6
max_block_lag=2
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

Kuma integration should be optional.

Recommended behavior:

- all checks `OK`: push up
- any check `CRIT`: push down with the most important failure message
- only `WARN`: either push up with warning text or use a separate Kuma monitor
- `UNKNOWN`: treat as down unless explicitly configured otherwise

Example:

```ini
[global]
kuma_push_url=https://kuma.example.com/api/push/abc123
```

## Installation

Planned installation targets:

```text
/opt/service-check
/usr/local/bin/service-check
/etc/service-check/service-check.ini
/var/lib/service-check/state.json
```

Planned install flow:

```bash
git clone https://github.com/you/service-check.git
cd service-check
sudo ./install.sh
sudo cp examples/monero.ini /etc/service-check/service-check.ini
sudo systemctl enable --now service-check.timer
```

The installer should:

- create `/opt/service-check`
- copy the application there
- create `/usr/local/bin/service-check`
- create `/etc/service-check`
- copy an example config only if no config exists
- create `/var/lib/service-check`
- install systemd service and timer units
- enable the timer only when explicitly requested or documented

## systemd

The service should execute one run and exit:

```ini
[Unit]
Description=Run service-check watchdog

[Service]
Type=oneshot
ExecStart=/usr/local/bin/service-check --config /etc/service-check/service-check.ini
```

The timer controls frequency:

```ini
[Unit]
Description=Run service-check watchdog periodically

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Unit=service-check.service

[Install]
WantedBy=timers.target
```

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
