from __future__ import annotations

from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.request import Request, urlopen

from service_check import __version__

USER_AGENT = f"service-check/{__version__}"


def push_kuma(push_url: str | None, status: str, message: str, timeout: float, dry_run: bool = False) -> str | None:
    if not push_url:
        return None
    if dry_run:
        return None

    kuma_status = "up" if status in {"OK", "WARN"} else "down"
    url = _with_query(push_url, {"status": kuma_status, "msg": message})
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
        return None
    except HTTPError as exc:
        return _http_error_message(exc)
    except OSError as exc:
        return str(exc)


def _with_query(url: str, values: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(values)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _http_error_message(exc: HTTPError) -> str:
    body = ""
    try:
        body = exc.read(300).decode("utf-8", errors="replace").strip()
    except OSError:
        body = ""
    suffix = f": {body}" if body else ""
    return f"HTTP Error {exc.code}: {exc.reason}{suffix}"
