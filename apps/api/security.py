"""Request checks that keep the API reachable only from this machine."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback_address(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def require_local_request(request: Request) -> None:
    """Reject non-loopback clients, foreign Host headers and cross-origin calls.

    The API has no authentication. Binding to 127.0.0.1 keeps remote
    machines out, but a web page open in the local browser can still
    reach it: the Host check stops DNS rebinding and the Origin check
    stops cross-site requests.
    """
    client = request.client.host if request.client else ""

    if not _is_loopback_address(client):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Loopback clients only",
        )

    host = request.headers.get("host", "")

    try:
        hostname = urlsplit(f"//{host}").hostname
    except ValueError:
        hostname = None

    if hostname not in LOOPBACK_HOSTNAMES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Untrusted Host header",
        )

    origin = request.headers.get("origin")

    if origin is None:
        return

    parsed = urlsplit(origin)

    if parsed.scheme not in {"http", "https"} or parsed.netloc != host:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin requests are not allowed",
        )
