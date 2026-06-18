# public_ip_reputation

Checks the machine's current public IP address against Tor, VPN, proxy, and
hosting/datacenter reputation sources.

The check is intended for services where the public egress IP should not be a
Tor exit node, VPN endpoint, public proxy, or hosting provider address.

## Behavior

The check runs on a fixed interval, usually every minute:

1. Detect the current public IP address.
2. If the public IP changed, query enabled reputation providers immediately.
3. If the public IP is unchanged, reuse the cached reputation result.
4. If the cached result is older than `reputation_cache_ttl_seconds`, refresh
   provider lookups even when the public IP has not changed.

This avoids burning free API quota on every run while still allowing provider
database updates to be picked up. A default reputation cache TTL of 24 hours is
recommended.

Tor should be checked from a periodically refreshed local Tor exit-node cache
before using external reputation APIs. This avoids API quota use and gives a
clear `tor` verdict when the public IP is a known Tor exit address.

## Verdicts

The check classifies the public IP into one of these verdicts:

- `tor`: IP is a known Tor exit node
- `vpn`: IP is reported as a VPN endpoint
- `proxy`: IP is reported as a public proxy
- `hosting`: IP is reported as hosting, cloud, or datacenter infrastructure
- `clean`: at least one reliable provider responded and no enabled provider flagged the IP
- `inconclusive`: providers responded, but only weak or conflicting evidence was available
- `unknown`: no reliable classification could be made because providers failed, timed out, or were rate-limited

`inconclusive` and `unknown` are intentionally separate:

- `inconclusive` means data exists, but the decision is uncertain.
- `unknown` means data was missing or unavailable.

## Parameters

Optional:

- `public_ip_provider`: HTTP endpoint used to detect the current public IP address
- `public_ip`: explicit public IP override; when set, public IP detection is skipped
- `public_ip_interface`: optional network interface name, such as `eth0` or `wg0`, used only for public IP detection. When set, the interface must exist on the host or the check fails before making the detection request. This uses Linux `SO_BINDTODEVICE`; leave it unset on systems that do not support interface-bound sockets.
- `public_ip_timeout_seconds`: timeout for public IP detection, defaults to `timeout_seconds`
- `providers`: comma-separated reputation providers to use, defaults to `tor,ipapi_is,ip_api`; see Provider Sources below
- `timeout_seconds`: timeout for provider requests, defaults to `[default] timeout_seconds`
- `reputation_cache_ttl_seconds`: seconds before refreshing reputation data for an unchanged public IP, defaults to `86400`
- `tor_cache_ttl_seconds`: seconds before refreshing the local Tor exit-node list, defaults to `3600`
- `use_stale_on_provider_failure`: reuse expired cached reputation data when refresh fails, defaults to `1`
- `max_stale_ttl_seconds`: maximum age for stale cached reputation data, defaults to `172800`
- `fail_on_verdicts`: comma-separated verdicts that fail the check, defaults to `tor,vpn,proxy`. Any verdict can be listed, including `clean`, `inconclusive`, or `unknown`.
- `ipapi_is_api_key`: optional API key for ipapi.is. The default `ipapi_is` provider can run without this key, subject to the provider's free/anonymous limits.
- `iphub_api_key`: required API key for IPHub when the `iphub` provider is enabled. Without it, `iphub` returns a provider error.
- `abuseipdb_api_key`: required API key for AbuseIPDB when the `abuseipdb` provider is enabled. Without it, `abuseipdb` returns a provider error.
- `failure_message`: alert template used when the public IP reputation check fails
- `failure_message.<problem_code>`: alert template used for a specific problem code
- `success_message`: message template used for OK status, recovery notifications, and Kuma OK pushes
- `interval_minutes`: how often this check runs
- `retries`: immediate retries before the run is considered failed
- `retry_delay_seconds`: seconds between immediate retries
- `fail_after`: failed runs required before local notification
- `notify_repeat_after_minutes`: minutes before repeating a notification for an unresolved problem
- `notify_cmd`: local notification command override for this check
- `kuma_push_url`: optional per-check Uptime Kuma push URL

## Provider Sources

Provider names in `providers=` are short IDs. Available providers:

- `tor`: downloads the Tor Project bulk exit-node list from `https://check.torproject.org/torbulkexitlist`, caches it locally, and checks whether the current public IP is a known Tor exit node.
- `ipapi_is`: queries `https://api.ipapi.is/` for Tor, VPN, proxy, datacenter/hosting, and abuse flags. No API key is required for the default configuration; `ipapi_is_api_key` is optional if you have one.
- `ip_api`: queries the free `http://ip-api.com/json/...` endpoint for proxy and hosting flags. No API key is used. It is HTTP-only, rate-limited, and restricted by ip-api.com terms.
- `iphub`: optional second-opinion provider at `https://v2.api.iphub.info/`; requires `iphub_api_key` and can identify proxy, Tor, relay, residential proxy, and hosting signals.
- `abuseipdb`: optional abuse-context provider at `https://api.abuseipdb.com/api/v2/check`; requires `abuseipdb_api_key` and contributes Tor, hosting, or inconclusive abuse signals.

The default `providers=tor,ipapi_is,ip_api` requires no API keys. It means: check the local Tor exit-node cache first, then query ipapi.is for broad VPN/proxy/hosting signals, then use ip-api.com as a lightweight fallback.

## Provider Notes

- `tor`: local Tor exit-node list. This should be checked first and does not consume reputation API quota after the list is cached.
- `ipapi_is`: primary free reputation API. It can report Tor, VPN, proxy, hosting/datacenter, and abuse signals.
- `iphub`: second-opinion provider. Treat `block == 1` as high confidence and `block == 2` as `inconclusive`.
- `ip_api`: lightweight fallback. The free endpoint is HTTP-only, limited to 45 requests per minute, and not allowed for commercial use.
- `abuseipdb`: abuse reputation provider. Use `abuseConfidenceScore`, `usageType`, and `isTor` as additional context, not as the primary VPN classifier.

When a provider returns HTTP `429`, the check should disable that provider until
the provider's reset time when available. Providers that only return weak
signals should contribute to `inconclusive`, not directly to `clean`.

## Details

The check returns these `details` keys for message templates:

- `public_ip`
- `previous_public_ip`
- `public_ip_interface`
- `verdict`
- `confidence`
- `sources`
- `cache_hit`
- `cache_age_seconds`
- `stale`
- `provider_errors`
- `provider_rate_limited`
- `tor_cache_age_seconds`
- `problem_code`, only on failure or unknown results
- `problem_codes`, only on failure or unknown results
- `error`, only on failure or unknown results

## Problem Codes

- `public_ip_detection_failed`: current public IP address could not be detected
- `public_ip_interface_missing`: configured `public_ip_interface` is not present on the host
- `tor_detected`: public IP is a known Tor exit node
- `vpn_detected`: public IP is reported as a VPN endpoint
- `proxy_detected`: public IP is reported as a public proxy
- `hosting_detected`: public IP is reported as hosting, cloud, or datacenter infrastructure
- `clean_detected`: public IP is clean/direct and `clean` is configured in `fail_on_verdicts`
- `inconclusive_detected`: providers returned weak or conflicting evidence and `inconclusive` is configured in `fail_on_verdicts`
- `unknown_detected`: reputation providers could not produce a reliable classification and `unknown` is configured in `fail_on_verdicts`
- `provider_rate_limited`: one or more enabled providers were rate-limited
- `provider_failed`: one or more enabled providers failed or timed out
- `stale_cache_expired`: cached reputation data exists but is older than `max_stale_ttl_seconds`
- `invalid_config`: check configuration is invalid

## Example

```ini
[public_ip_reputation]
enabled=1
check=public_ip_reputation
interval_minutes=1
providers=tor,ipapi_is,ip_api
# Bind only the public IP detection request to a specific interface:
# public_ip_interface=eth0
timeout_seconds=5
reputation_cache_ttl_seconds=86400
tor_cache_ttl_seconds=3600
use_stale_on_provider_failure=1
max_stale_ttl_seconds=172800
fail_on_verdicts=tor,vpn,proxy
# To require VPN/proxy/Tor and fail on direct public egress, use:
# fail_on_verdicts=clean
# To fail when providers cannot classify reliably, include:
# fail_on_verdicts=tor,vpn,proxy,inconclusive,unknown
# ipapi_is_api_key=
# iphub_api_key=
# abuseipdb_api_key=
failure_message=Public IP {public_ip} reputation problem: {verdict}
failure_message.public_ip_interface_missing=Configured public IP interface is missing: {public_ip_interface}
failure_message.tor_detected=Public IP {public_ip} is a Tor exit node
failure_message.vpn_detected=Public IP {public_ip} is reported as VPN by {sources}
failure_message.proxy_detected=Public IP {public_ip} is reported as proxy by {sources}
failure_message.hosting_detected=Public IP {public_ip} is hosting/datacenter infrastructure according to {sources}
failure_message.clean_detected=Public IP {public_ip} is clean/direct, not VPN/proxy/Tor, according to {sources}
failure_message.inconclusive_detected=Public IP {public_ip} reputation is inconclusive: {sources}
failure_message.unknown_detected=Public IP {public_ip} reputation could not be determined: {provider_errors}
success_message=Public IP {public_ip} reputation is {verdict}
```
