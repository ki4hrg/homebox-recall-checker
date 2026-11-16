"""Request guards for a UI with no sign-in.

The UI relies on the network or the reverse proxy for who may use it, so two checks stop other
web pages from using a visitor's browser against it:

- ``HostGuard`` refuses requests whose ``Host`` isn't one this app answers to. A DNS-rebinding
  page points its own name at the LAN address and then reads the UI as same-origin; its requests
  still carry its own name in ``Host``, which isn't on the list.
- ``CrossSiteGuard`` refuses every state-changing request a browser sends from another origin
  (``Sec-Fetch-Site`` and ``Origin``), so a page on any site can't confirm or dismiss a match,
  start a check or send a test notification. There are no sessions or cookies to protect, so
  there's no token: the browser's own headers say where a request came from. Clients that send
  neither header (curl, scripts) aren't browsers acting for someone else, and are allowed.

Neither check reads ``X-Forwarded-*``: anyone who reaches the port directly could set them.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import is_ip

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Loopback names and addresses are always allowed, so the container healthcheck keeps working.
LOOPBACK_NAMES = frozenset({"localhost"})


def split_host(value: str) -> tuple[str, str]:
    """Split a Host header or URL netloc into (lower-case host, port or "")."""
    value = value.strip().lower()
    if value.startswith("["):  # [v6]:port
        host, _, rest = value[1:].partition("]")
        return host, rest.removeprefix(":")
    host, _, port = value.rpartition(":") if value.count(":") == 1 else (value, "", "")
    return host.rstrip("."), port


class HostGuard:
    """Refuse a request whose Host is not allowed.

    ``names`` is the list of host names (no ports). ``None`` turns the check off (``*``).
    ``allow_any_ip`` admits any IP-literal Host: DNS rebinding needs a name, so an address
    can't be used for it, and people who open the UI by its LAN address keep working.
    """

    def __init__(self, app: ASGIApp, names: Iterable[str] | None, allow_any_ip: bool) -> None:
        self.app = app
        self.names = None if names is None else frozenset(n.lower() for n in names)
        self.allow_any_ip = allow_any_ip

    def allowed(self, host_header: str) -> bool:
        if self.names is None:
            return True
        host, _ = split_host(host_header)
        if not host:
            return False
        if host in self.names or host in LOOPBACK_NAMES:
            return True
        if is_ip(host):
            return self.allow_any_ip or ipaddress.ip_address(host).is_loopback
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if not self.allowed(Headers(scope=scope).get("host", "")):
            response = PlainTextResponse(
                "Unknown host. Add this host name to ALLOWED_HOSTS (see the README).", 400
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _origin_netloc(scheme: str, netloc: str) -> str:
    """host[:port] with the scheme's default port dropped, as browsers write Origin."""
    host, port = split_host(netloc)
    if (scheme, port) in (("http", "80"), ("https", "443")):
        port = ""
    shown = f"[{host}]" if ":" in host else host
    return f"{shown}:{port}" if port else shown


class CrossSiteGuard:
    """Refuse a state-changing request that a browser sent from another origin.

    Allowed when ``Sec-Fetch-Site`` is ``same-origin`` or absent, and ``Origin``, when present,
    is this app's: its host[:port] equals the request's ``Host`` header, or it equals
    ``public_url``'s origin exactly. The scheme isn't compared with ``Host``: behind a proxy that
    terminates TLS the app sees plain HTTP, and knowing the browser's scheme would mean trusting
    ``X-Forwarded-Proto``. ``public_url`` covers a proxy that rewrites ``Host``.
    """

    def __init__(self, app: ASGIApp, public_url: str = "") -> None:
        self.app = app
        self.public_origin = ""
        if public_url:
            parts = urlsplit(public_url)
            if parts.scheme and parts.netloc:
                self.public_origin = (
                    f"{parts.scheme}://{_origin_netloc(parts.scheme, parts.netloc)}"
                )

    def same_origin(self, headers: Headers) -> bool:
        site = headers.get("sec-fetch-site")
        if site is not None and site != "same-origin":
            return False
        origin = headers.get("origin")
        if origin is None:
            return True
        parts = urlsplit(origin.strip().lower())
        if parts.scheme not in ("http", "https") or not parts.netloc:  # includes "null"
            return False
        netloc = _origin_netloc(parts.scheme, parts.netloc)
        if self.public_origin and f"{parts.scheme}://{netloc}" == self.public_origin:
            return True
        host = headers.get("host", "")
        if not host:
            return False
        # Host carries the port the browser used, or none for the default one.
        return netloc in (_origin_netloc("http", host), _origin_netloc("https", host))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        if not self.same_origin(Headers(scope=scope)):
            response = PlainTextResponse("Cross-site request refused.", 403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


# The UI loads only its own stylesheet, its own theme script and recall images from the feeds'
# hosts, and is never framed: a page that frames it can't trick a click onto Confirm or Dismiss.
# `script-src 'self'` admits only files this app serves: no inline script or event handler, and
# nothing else it answers can run as one (JSON, plain text and HTML fail `nosniff`'s script check).
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' https: http:",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ]
)
SECURITY_HEADERS = [
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    # Links out (Homebox, the recall pages) don't carry the match page's address.
    (b"referrer-policy", b"same-origin"),
]


class SecurityHeaders:
    """Add the headers above to every response, refusals and static files included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in SECURITY_HEADERS if k not in names]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


# The only request bodies are one-field forms. Anyone who reaches the port could otherwise send a
# body as large as they like, and the form parser would hold it in memory.
MAX_BODY_BYTES = 16 * 1024


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Refuse a request body over ``MAX_BODY_BYTES``: 413 when ``Content-Length`` says so,
    and an error partway through reading when it's absent or wrong."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > self.limit:
            response = PlainTextResponse("Request body too large.", 413)
            await response(scope, receive, send)
            return
        seen = 0

        async def counted() -> dict:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise BodyTooLarge
            return message

        await self.app(scope, counted, send)
