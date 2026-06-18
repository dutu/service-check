from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from service_check.models import CRIT, OK, UNKNOWN, CheckConfig, CheckResult

DEFAULT_PUBLIC_IP_PROVIDER = "https://api.ipify.org"
DEFAULT_TOR_EXIT_LIST_URL = "https://check.torproject.org/torbulkexitlist"
DEFAULT_PROVIDERS = ["tor", "ipapi_is", "ip_api"]
ANONYMOUS_VERDICTS = {"tor", "vpn", "proxy", "hosting"}

CHECK_METADATA = {
    "description": "Checks the machine's public IP against Tor, VPN, proxy, and hosting reputation sources.",
    "statuses": {
        OK: "Public IP reputation is acceptable or cached provider data was reused.",
        CRIT: "Public IP matches a configured failing verdict.",
        UNKNOWN: "Public IP detection or reputation classification could not be completed.",
    },
    "details": {
        "problem_code": "Primary machine-readable problem reason.",
        "problem_codes": "List of machine-readable problem reasons.",
        "public_ip": "Current public IP address.",
        "previous_public_ip": "Previous public IP address from check state.",
        "verdict": "Final reputation verdict.",
        "confidence": "Final confidence label.",
        "sources": "Comma-separated providers contributing to the verdict.",
        "cache_hit": "Whether cached reputation data was reused.",
        "cache_age_seconds": "Age of cached reputation data.",
        "stale": "Whether expired cached reputation data was reused after refresh failure.",
        "provider_errors": "Provider errors encountered during refresh.",
        "provider_rate_limited": "Providers currently rate-limited.",
        "tor_cache_age_seconds": "Age of cached Tor exit-node list.",
        "error": "Error text; present on failure or unknown results.",
    },
}


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    verdict: str
    confidence: str
    flags: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    rate_limited_until: str = ""


def run(config: CheckConfig, state: dict[str, Any] | None = None) -> CheckResult:
    previous_state = dict(state or {})
    timeout = config.get_float("timeout_seconds", 5.0)
    now = _utc_now()
    providers = _parse_csv(config.get("providers")) or DEFAULT_PROVIDERS

    try:
        public_ip = _detect_public_ip(config, timeout)
    except ValueError as exc:
        return _result(
            config,
            UNKNOWN,
            "public IP could not be detected",
            {
                "public_ip": "",
                "previous_public_ip": str(previous_state.get("public_ip", "")),
                "verdict": "unknown",
                "confidence": "none",
                "sources": "",
                "cache_hit": False,
                "cache_age_seconds": 0,
                "stale": False,
                "provider_errors": "",
                "provider_rate_limited": "",
                "tor_cache_age_seconds": _tor_cache_age(previous_state, now),
                "error": str(exc),
            },
            previous_state,
            ["public_ip_detection_failed"],
        )

    reputation_cache = previous_state.get("reputation")
    cache_ip = reputation_cache.get("public_ip") if isinstance(reputation_cache, dict) else None
    cache_age = _cache_age(reputation_cache, now)
    reputation_ttl = config.get_int("reputation_cache_ttl_seconds", 86400)
    same_ip = public_ip == previous_state.get("public_ip") == cache_ip
    if same_ip and cache_age is not None and cache_age < reputation_ttl:
        details = _details_from_cache(public_ip, previous_state, reputation_cache, now, cache_hit=True, stale=False)
        return _classified_result(config, details, previous_state)

    refresh = _refresh_reputation(config, previous_state, public_ip, providers, timeout, now)
    new_state = refresh.state
    if refresh.details["verdict"] != "unknown" or not _can_use_stale(config, reputation_cache, public_ip, now):
        return _classified_result(config, refresh.details, new_state)

    stale_details = _details_from_cache(public_ip, previous_state, reputation_cache, now, cache_hit=True, stale=True)
    stale_details["provider_errors"] = refresh.details["provider_errors"]
    stale_details["provider_rate_limited"] = refresh.details["provider_rate_limited"]
    return _classified_result(config, stale_details, new_state)


@dataclass(frozen=True)
class RefreshResult:
    details: dict[str, Any]
    state: dict[str, Any]


def _detect_public_ip(config: CheckConfig, timeout: float) -> str:
    override = config.get("public_ip")
    if override:
        return _validate_ip(override)

    provider = config.get("public_ip_provider", DEFAULT_PUBLIC_IP_PROVIDER) or DEFAULT_PUBLIC_IP_PROVIDER
    public_ip_timeout = config.get_float("public_ip_timeout_seconds", timeout)
    body = _fetch_text(provider, timeout=public_ip_timeout)
    return _validate_ip(body.strip())


def _refresh_reputation(
    config: CheckConfig,
    previous_state: dict[str, Any],
    public_ip: str,
    providers: list[str],
    timeout: float,
    now: str,
) -> RefreshResult:
    new_state = dict(previous_state)
    new_state["public_ip"] = public_ip
    new_state.setdefault("provider_disabled_until", {})
    results: list[ProviderResult] = []

    for provider in providers:
        disabled_until = _provider_disabled_until(new_state, provider)
        if disabled_until and _seconds_until(disabled_until, now) > 0:
            results.append(ProviderResult(provider, "unknown", "none", error="provider rate-limited", rate_limited_until=disabled_until))
            continue
        result = _run_provider(config, new_state, provider, public_ip, timeout, now)
        results.append(result)
        if result.rate_limited_until:
            new_state.setdefault("provider_disabled_until", {})[provider] = result.rate_limited_until

    verdict, confidence, sources = _classify(results)
    provider_errors = [f"{result.provider}: {result.error}" for result in results if result.error]
    rate_limited = [
        result.provider
        for result in results
        if result.rate_limited_until or result.error == "provider rate-limited"
    ]
    details = {
        "public_ip": public_ip,
        "previous_public_ip": str(previous_state.get("public_ip", "")),
        "verdict": verdict,
        "confidence": confidence,
        "sources": ",".join(sources),
        "cache_hit": False,
        "cache_age_seconds": 0,
        "stale": False,
        "provider_errors": "; ".join(provider_errors),
        "provider_rate_limited": ",".join(rate_limited),
        "tor_cache_age_seconds": _tor_cache_age(new_state, now),
    }
    new_state["reputation"] = {
        "public_ip": public_ip,
        "checked_at": now,
        "verdict": verdict,
        "confidence": confidence,
        "sources": sources,
        "provider_errors": provider_errors,
        "provider_rate_limited": rate_limited,
    }
    return RefreshResult(details=details, state=new_state)


def _run_provider(
    config: CheckConfig,
    state: dict[str, Any],
    provider: str,
    public_ip: str,
    timeout: float,
    now: str,
) -> ProviderResult:
    try:
        if provider == "tor":
            return _check_tor(config, state, public_ip, timeout, now)
        if provider == "ipapi_is":
            return _check_ipapi_is(config, public_ip, timeout)
        if provider == "iphub":
            return _check_iphub(config, public_ip, timeout, now)
        if provider == "ip_api":
            return _check_ip_api(public_ip, timeout, now)
        if provider == "abuseipdb":
            return _check_abuseipdb(config, public_ip, timeout, now)
        return ProviderResult(provider, "unknown", "none", error=f"unknown provider {provider!r}")
    except HTTPError as exc:
        if exc.code == 429:
            return ProviderResult(provider, "unknown", "none", error="rate limit exceeded", rate_limited_until=_retry_after_until(exc, now))
        return ProviderResult(provider, "unknown", "none", error=f"HTTP {exc.code}: {exc.reason}")
    except (URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return ProviderResult(provider, "unknown", "none", error=str(exc))


def _check_tor(
    config: CheckConfig,
    state: dict[str, Any],
    public_ip: str,
    timeout: float,
    now: str,
) -> ProviderResult:
    tor_state = state.get("tor")
    exit_ips = set(tor_state.get("exit_ips", [])) if isinstance(tor_state, dict) else set()
    fetched_at = tor_state.get("fetched_at", "") if isinstance(tor_state, dict) else ""
    ttl = config.get_int("tor_cache_ttl_seconds", 3600)
    age = _seconds_since(fetched_at, now) if fetched_at else None
    if age is None or age >= ttl:
        url = config.get("tor_exit_list_url", DEFAULT_TOR_EXIT_LIST_URL) or DEFAULT_TOR_EXIT_LIST_URL
        body = _fetch_text(url, timeout=timeout)
        exit_ips = {line.strip() for line in body.splitlines() if _is_ip(line.strip())}
        state["tor"] = {"exit_ips": sorted(exit_ips), "fetched_at": now}
    if public_ip in exit_ips:
        return ProviderResult("tor", "tor", "high", {"is_tor": True})
    return ProviderResult("tor", "normal", "high", {"is_tor": False})


def _check_ipapi_is(config: CheckConfig, public_ip: str, timeout: float) -> ProviderResult:
    query = {"q": public_ip}
    api_key = config.get("ipapi_is_api_key")
    if api_key:
        query["key"] = api_key
    payload = _fetch_json(f"https://api.ipapi.is/?{urlencode(query)}", timeout=timeout)
    flags = {
        "is_tor": _find_bool(payload, "is_tor"),
        "is_vpn": _find_bool(payload, "is_vpn"),
        "is_proxy": _find_bool(payload, "is_proxy"),
        "is_datacenter": _find_bool(payload, "is_datacenter"),
        "is_hosting": _find_bool(payload, "is_hosting"),
        "is_abuser": _find_bool(payload, "is_abuser"),
    }
    if flags["is_tor"]:
        return ProviderResult("ipapi_is", "tor", "high", flags)
    if flags["is_vpn"]:
        return ProviderResult("ipapi_is", "vpn", "high", flags)
    if flags["is_proxy"]:
        return ProviderResult("ipapi_is", "proxy", "high", flags)
    if flags["is_datacenter"] or flags["is_hosting"]:
        return ProviderResult("ipapi_is", "hosting", "medium", flags)
    if flags["is_abuser"]:
        return ProviderResult("ipapi_is", "inconclusive", "low", flags)
    return ProviderResult("ipapi_is", "normal", "medium", flags)


def _check_iphub(config: CheckConfig, public_ip: str, timeout: float, now: str) -> ProviderResult:
    api_key = config.get("iphub_api_key")
    if not api_key:
        return ProviderResult("iphub", "unknown", "none", error="iphub_api_key is required")
    headers = {"X-Key": api_key, "Accept-Version": "2.2", "User-Agent": "service-check"}
    payload = _fetch_json(f"https://v2.api.iphub.info/ip/{quote(public_ip)}", timeout=timeout, headers=headers)
    block = payload.get("block")
    proxy_type = payload.get("proxyType") if isinstance(payload.get("proxyType"), dict) else {}
    flags = {"block": block, "proxyType": proxy_type}
    if proxy_type.get("tor"):
        return ProviderResult("iphub", "tor", "high", flags)
    if proxy_type.get("proxy") or proxy_type.get("relay") or proxy_type.get("residentialProxy"):
        return ProviderResult("iphub", "proxy", "high", flags)
    if proxy_type.get("hosting"):
        return ProviderResult("iphub", "hosting", "high", flags)
    if block == 1:
        return ProviderResult("iphub", "proxy", "high", flags)
    if block == 2:
        return ProviderResult("iphub", "inconclusive", "low", flags)
    if block == 0:
        return ProviderResult("iphub", "normal", "medium", flags)
    return ProviderResult("iphub", "unknown", "none", flags, error="IPHub response did not include block")


def _check_ip_api(public_ip: str, timeout: float, now: str) -> ProviderResult:
    fields = "status,message,query,proxy,hosting"
    url = f"http://ip-api.com/json/{quote(public_ip)}?{urlencode({'fields': fields})}"
    payload, headers = _fetch_json_with_headers(url, timeout=timeout)
    reset_seconds = headers.get("X-Ttl")
    remaining = headers.get("X-Rl")
    rate_limited_until = _seconds_from_now(reset_seconds, now) if remaining == "0" and reset_seconds else ""
    if payload.get("status") != "success":
        return ProviderResult("ip_api", "unknown", "none", error=str(payload.get("message", "lookup failed")), rate_limited_until=rate_limited_until)
    flags = {"proxy": payload.get("proxy"), "hosting": payload.get("hosting")}
    if payload.get("proxy"):
        return ProviderResult("ip_api", "proxy", "medium", flags, rate_limited_until=rate_limited_until)
    if payload.get("hosting"):
        return ProviderResult("ip_api", "hosting", "medium", flags, rate_limited_until=rate_limited_until)
    return ProviderResult("ip_api", "normal", "low", flags, rate_limited_until=rate_limited_until)


def _check_abuseipdb(config: CheckConfig, public_ip: str, timeout: float, now: str) -> ProviderResult:
    api_key = config.get("abuseipdb_api_key")
    if not api_key:
        return ProviderResult("abuseipdb", "unknown", "none", error="abuseipdb_api_key is required")
    url = "https://api.abuseipdb.com/api/v2/check?" + urlencode({"ipAddress": public_ip, "maxAgeInDays": "30"})
    payload = _fetch_json(url, timeout=timeout, headers={"Key": api_key, "Accept": "application/json", "User-Agent": "service-check"})
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    flags = {
        "isTor": data.get("isTor"),
        "usageType": data.get("usageType"),
        "abuseConfidenceScore": data.get("abuseConfidenceScore"),
    }
    if data.get("isTor"):
        return ProviderResult("abuseipdb", "tor", "high", flags)
    usage_type = str(data.get("usageType", ""))
    if "Data Center" in usage_type or "Hosting" in usage_type:
        return ProviderResult("abuseipdb", "hosting", "low", flags)
    abuse_score = data.get("abuseConfidenceScore")
    if isinstance(abuse_score, int) and abuse_score > 0:
        return ProviderResult("abuseipdb", "inconclusive", "low", flags)
    return ProviderResult("abuseipdb", "normal", "low", flags)


def _classify(results: list[ProviderResult]) -> tuple[str, str, list[str]]:
    usable = [result for result in results if not result.error]
    for verdict in ("tor", "vpn", "proxy", "hosting"):
        sources = [result.provider for result in usable if result.verdict == verdict]
        if sources:
            confidence = _best_confidence(result.confidence for result in usable if result.verdict == verdict)
            return verdict, confidence, sources
    inconclusive_sources = [result.provider for result in usable if result.verdict == "inconclusive"]
    normal_sources = [result.provider for result in usable if result.verdict == "normal" and result.provider != "tor"]
    if inconclusive_sources:
        return "inconclusive", "low", inconclusive_sources
    if normal_sources:
        return "normal", _best_confidence(result.confidence for result in usable if result.provider in normal_sources), normal_sources
    if usable and all(result.provider == "tor" and result.verdict == "normal" for result in usable) and not any(result.error for result in results):
        return "normal", "low", ["tor"]
    return "unknown", "none", []


def _best_confidence(values: Any) -> str:
    ordered = {"high": 3, "medium": 2, "low": 1, "none": 0}
    best = "none"
    for value in values:
        if ordered.get(str(value), 0) > ordered[best]:
            best = str(value)
    return best


def _classified_result(config: CheckConfig, details: dict[str, Any], state: dict[str, Any]) -> CheckResult:
    verdict = str(details.get("verdict", "unknown"))
    fail_on_verdicts = set(_parse_csv(config.get("fail_on_verdicts")) or ["tor", "vpn", "proxy"])
    if verdict == "inconclusive" and config.get_bool("fail_on_inconclusive", False):
        return _result(config, CRIT, f"public IP reputation is inconclusive: {details.get('public_ip')}", details, state, ["reputation_inconclusive"])
    if verdict == "unknown":
        if config.get_bool("fail_on_unknown", False):
            return _result(config, CRIT, f"public IP reputation is unknown: {details.get('public_ip')}", details, state, ["reputation_unknown"])
        return CheckResult(
            name=config.section,
            status=OK,
            message=f"public IP {details.get('public_ip')} reputation verdict is unknown",
            details=details,
            state=state,
        )
    if verdict in fail_on_verdicts:
        return _result(config, CRIT, f"public IP {details.get('public_ip')} reputation verdict is {verdict}", details, state, [f"{verdict}_detected"])
    return CheckResult(
        name=config.section,
        status=OK,
        message=f"public IP {details.get('public_ip')} reputation verdict is {verdict}",
        details=details,
        state=state,
    )


def _details_from_cache(
    public_ip: str,
    previous_state: dict[str, Any],
    reputation_cache: dict[str, Any],
    now: str,
    cache_hit: bool,
    stale: bool,
) -> dict[str, Any]:
    sources = reputation_cache.get("sources", [])
    return {
        "public_ip": public_ip,
        "previous_public_ip": str(previous_state.get("public_ip", "")),
        "verdict": str(reputation_cache.get("verdict", "unknown")),
        "confidence": str(reputation_cache.get("confidence", "none")),
        "sources": ",".join(str(source) for source in sources) if isinstance(sources, list) else str(sources),
        "cache_hit": cache_hit,
        "cache_age_seconds": _cache_age(reputation_cache, now) or 0,
        "stale": stale,
        "provider_errors": "; ".join(reputation_cache.get("provider_errors", [])),
        "provider_rate_limited": ",".join(reputation_cache.get("provider_rate_limited", [])),
        "tor_cache_age_seconds": _tor_cache_age(previous_state, now),
    }


def _result(
    config: CheckConfig,
    status: str,
    message: str,
    details: dict[str, Any],
    state: dict[str, Any],
    problem_codes: list[str],
) -> CheckResult:
    result_details = dict(details)
    result_details["problem_code"] = problem_codes[0]
    result_details["problem_codes"] = problem_codes
    if "error" not in result_details and result_details.get("provider_errors"):
        result_details["error"] = result_details["provider_errors"]
    return CheckResult(name=config.section, status=status, message=message, details=result_details, state=state)


def _fetch_text(url: str, timeout: float, headers: dict[str, str] | None = None) -> str:
    request = Request(url, headers={"User-Agent": "service-check", **(headers or {})})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _fetch_json(url: str, timeout: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    payload, _headers = _fetch_json_with_headers(url, timeout, headers)
    return payload


def _fetch_json_with_headers(
    url: str,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    request = Request(url, headers={"User-Agent": "service-check", **(headers or {})})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("expected JSON object response")
        return payload, dict(response.headers.items())


def _find_bool(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            if item_key == key and isinstance(item_value, bool):
                return item_value
            if isinstance(item_value, dict | list) and _find_bool(item_value, key):
                return True
    if isinstance(value, list):
        return any(_find_bool(item, key) for item in value)
    return False


def _validate_ip(value: str) -> str:
    try:
        parsed = ip_address(value.strip())
    except ValueError as exc:
        raise ValueError(f"invalid public IP address: {value!r}") from exc
    if not parsed.is_global:
        raise ValueError(f"public IP address is not globally routable: {value!r}")
    return str(parsed)


def _is_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _can_use_stale(config: CheckConfig, reputation_cache: Any, public_ip: str, now: str) -> bool:
    if not config.get_bool("use_stale_on_provider_failure", True):
        return False
    if not isinstance(reputation_cache, dict) or reputation_cache.get("public_ip") != public_ip:
        return False
    age = _cache_age(reputation_cache, now)
    return age is not None and age <= config.get_int("max_stale_ttl_seconds", 172800)


def _cache_age(cache: Any, now: str) -> int | None:
    if not isinstance(cache, dict):
        return None
    checked_at = cache.get("checked_at")
    if not isinstance(checked_at, str) or not checked_at:
        return None
    return _seconds_since(checked_at, now)


def _tor_cache_age(state: dict[str, Any], now: str) -> int:
    tor_state = state.get("tor")
    if not isinstance(tor_state, dict):
        return 0
    fetched_at = tor_state.get("fetched_at")
    if not isinstance(fetched_at, str) or not fetched_at:
        return 0
    return _seconds_since(fetched_at, now)


def _provider_disabled_until(state: dict[str, Any], provider: str) -> str:
    disabled = state.get("provider_disabled_until")
    if not isinstance(disabled, dict):
        return ""
    value = disabled.get(provider)
    return value if isinstance(value, str) else ""


def _retry_after_until(exc: HTTPError, now: str) -> str:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    return _seconds_from_now(retry_after, now) if retry_after else _seconds_from_now("3600", now)


def _seconds_from_now(seconds: str | None, now: str) -> str:
    try:
        delta = int(float(seconds or "0"))
    except ValueError:
        delta = 0
    base = _parse_iso(now)
    return datetime.fromtimestamp(base.timestamp() + max(delta, 0), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _seconds_until(iso_value: str, now: str) -> int:
    return max(0, int((_parse_iso(iso_value) - _parse_iso(now)).total_seconds()))


def _seconds_since(previous_iso: str, now_iso: str) -> int:
    try:
        return max(0, int((_parse_iso(now_iso) - _parse_iso(previous_iso)).total_seconds()))
    except ValueError:
        return 0


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _monotonic_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
