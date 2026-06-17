# monerod

Checks a local Monero daemon managed by systemd.

The check verifies:

- `systemctl is-active <service_name>` reports `active`
- configured P2P, unrestricted RPC, and restricted RPC TCP ports are reachable
- unrestricted RPC `get_info` reports healthy sync state when configured
- sync height continues advancing while behind
- incoming/outgoing peer counts meet configured minimums

## Parameters

Optional:

- `service_name`: systemd service name, defaults to `monerod`
- `config_file`: monerod config path; when omitted, the check reads `--config-file` from the systemd `ExecStart`, then falls back to `/etc/monero/monerod.conf`
- `sync_stall_seconds`: seconds without height progress before pending sync becomes `CRIT`, defaults to `180`
- `min_out_peers`: minimum outgoing peers, defaults to `1`
- `min_in_peers`: minimum incoming peers, defaults to `0`
- `require_rpc`: return `CRIT` if unrestricted RPC is not configured, defaults to `0`
- `timeout_seconds`: timeout for systemctl, TCP, and RPC calls, defaults to `[default] timeout_seconds`
- `failure_message`: alert template used for `WARN`, `CRIT`, or `UNKNOWN`
- `success_message`: message template used for OK status, recovery notifications, and Kuma OK pushes
- `interval_minutes`: how often this check runs
- `retries`: immediate retries before the run is considered failed
- `retry_delay_seconds`: seconds between immediate retries
- `fail_after`: failed runs required before local notification
- `notify_repeat_after_minutes`: minutes before repeating a notification for an unresolved problem
- `notify_cmd`: local notification command override for this check
- `kuma_push_url`: optional per-check Uptime Kuma push URL

The check reads these monerod config keys when present:

- `rpc-bind-ip`
- `rpc-bind-port`
- `rpc-login`
- `rpc-restricted-bind-ip`
- `rpc-restricted-bind-port`
- `p2p-bind-ip`
- `p2p-bind-port`

`rpc-login=user:password` is used as HTTP Basic auth for unrestricted RPC.

## Statuses

- `OK`: service is active, configured ports are reachable, RPC is synced, and peer thresholds pass
- `WARN`: service is usable but sync is pending or optional peer thresholds are degraded
- `CRIT`: service is inactive, a configured port is closed, RPC is unhealthy, sync is stalled, or daemon is behind but not syncing
- `UNKNOWN`: local check setup could not be evaluated, for example unreadable config or unavailable `systemctl`

## Details

The check returns these `details` keys for message templates:

- `service_name`
- `config_file`
- `rpc_host`
- `rpc_port`
- `restricted_rpc_host`
- `restricted_rpc_port`
- `p2p_host`
- `p2p_port`
- `height`
- `target_height`
- `synchronized`
- `busy_syncing`
- `offline`
- `outgoing_connections_count`
- `incoming_connections_count`
- `sync_stalled_for_seconds`
- `error`, only on failure or unknown results

## Example

```ini
[monerod]
enabled=1
check=monerod
interval_minutes=1
service_name=monerod
sync_stall_seconds=180
min_out_peers=1
min_in_peers=0
timeout_seconds=2
failure_message=monerod unhealthy: {message}
success_message=monerod synced at height {height} with {outgoing_connections_count} outgoing peers
```
