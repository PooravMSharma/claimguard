"""Request IDs, an access log without personal data, security headers and a safe error response.

Pure ASGI middleware (not BaseHTTPMiddleware) so streaming responses and background tasks are not buffered.
"""

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("claimguard.access")
error_log = logging.getLogger("claimguard.error")

REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
# Pages that need the browser to run scripts (API docs, development only)
DOCS = ("/docs", "/redoc", "/openapi.json")


def _headers(path: str, production: bool, content_type: bytes) -> list[tuple[bytes, bytes]]:
    out = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]
    if content_type.startswith(b"application/json") and not path.startswith(DOCS):
        # JSON is data, never a page: nothing in it may run or be framed. Page images and original PDFs (opened
        # in a browser tab, where the PDF viewer needs to run) are left to the browser's defaults.
        out.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
    if production:
        out.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
    return out


class RequestContext:
    def __init__(self, app: ASGIApp, production: bool):
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = given if REQUEST_ID.match(given) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        path = scope.get("path", "")
        started = time.monotonic()
        status = 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                content_type = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                headers += [(k, v) for k, v in _headers(path, self.production, content_type) if k not in present]
                if b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))     # claim data must not sit in caches
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            # path only: query strings can carry names searched for in the queue
            log.info("%s %s %s %dms id=%s", scope.get("method"), path, status,
                     int((time.monotonic() - started) * 1000), request_id)


def install(app: FastAPI, production: bool) -> None:
    app.add_middleware(RequestContext, production=production)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = request.state.request_id if hasattr(request.state, "request_id") else uuid.uuid4().hex
        error_log.exception("Unhandled error id=%s %s %s", request_id, request.method, request.url.path)
        return JSONResponse(status_code=500, headers={"x-request-id": request_id},
                            content={"detail": f"Something went wrong on our side. Reference: {request_id}"})
