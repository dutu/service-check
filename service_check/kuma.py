from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.request import urlopen


def push_kuma(push_url: str | None, status: str, message: str, timeout: float, dry_run: bool = False) -> str | None:
    if not push_url:
        return None
    if dry_run:
        return None

    kuma_status = "up" if status in {"OK", "WARN"} else "down"
    url = _with_query(push_url, {"status": kuma_status, "msg": message})
    try:
        with urlopen(url, timeout=timeout) as response:
            response.read()
        return None
    except OSError as exc:
        return str(exc)


def _with_query(url: str, values: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(values)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

