# socks_proxy

Checks whether a SOCKS5 proxy can actually open a CONNECT tunnel to a target.

This is the right check for 3proxy when the useful health signal is proxy
functionality, not whether the `3proxy` process or systemd unit exists.

## Parameters

Required:

- `proxy_host`: SOCKS proxy hostname or IP address; `host` is accepted as an alias
- `proxy_port`: SOCKS proxy TCP port; `port` is accepted as an alias
- `target_host`: hostname or IP address to connect to through the proxy
- `target_port`: TCP port to connect to through the proxy

By default, the example uses `1.1.1.1:443` as a reliable third-party TCP target.
Using an IP avoids DNS as an extra failure source and Cloudflare's public
resolver endpoint is highly available. For stricter monitoring, override
`target_host` and `target_port` with a stable endpoint you control, such as your
own VPS on `443` or a known internal service. Any third-party target can still
create false alerts if the remote endpoint, route, or firewall policy changes.

Optional:

- `username`: SOCKS5 username, when the proxy requires username/password auth
- `password`: SOCKS5 password, when the proxy requires username/password auth
- `timeout_seconds`: network timeout in seconds, defaults to `[default] timeout_seconds`
- `failure_message`: alert template used when the SOCKS check fails
- `failure_message.<problem_code>`: alert template used for a specific problem code
- `success_message`: message template used for OK status, recovery notifications, and Kuma OK pushes
- `interval_minutes`: how often this check runs
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

- `proxy_host`
- `proxy_port`
- `target_host`
- `target_port`
- `timeout_seconds`
- `elapsed_ms`
- `auth_method`
- `bound_address`
- `bound_port`
- `problem_code`, only on failure or unknown results
- `problem_codes`, only on failure or unknown results
- `error`, only on failure

## Problem Codes

- `missing_config`: one or more required config values are missing
- `invalid_port`: proxy or target port is not numeric or outside `1..65535`
- `socks_connect_failed`: TCP connection, SOCKS5 handshake, authentication, or CONNECT failed

## Example

```ini
[threeproxy_socks]
enabled=1
check=socks_proxy
interval_minutes=1
proxy_host=127.0.0.1
proxy_port=1080
target_host=1.1.1.1
target_port=443
timeout_seconds=5
# username=proxy-user
# password=proxy-password
failure_message=SOCKS proxy {proxy_host}:{proxy_port} failed to connect to {target_host}:{target_port}: {error}
failure_message.socks_connect_failed=SOCKS proxy {proxy_host}:{proxy_port} failed to connect to {target_host}:{target_port}: {error}
success_message=SOCKS proxy {proxy_host}:{proxy_port} connected to {target_host}:{target_port} in {elapsed_ms}ms
```
