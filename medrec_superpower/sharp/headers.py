"""SHARP context propagation via HTTP headers (Prompt Opinion protocol).

Prompt Opinion injects FHIR identity into every tool call via these headers,
gated by an MCP capability declaration (see ``server.py``):

* ``X-FHIR-Server-URL``  — workspace FHIR server base URL
* ``X-FHIR-Access-Token`` — bearer for that FHIR server
* ``X-Patient-ID``       — currently-scoped patient

For developer use without Prompt Opinion (unit tests, curl, etc.), a signed
SHARP JWT can be supplied via ``X-Sharp-Token``. The two paths are mutually
exclusive at request time.

The middleware captures these once per HTTP request and stores them in a
``contextvars.ContextVar`` so the MCP tool wrappers — which run several
async-stack frames below the HTTP layer — can read them without the LLM
needing to thread them through as tool arguments.

Reference: https://docs.promptopinion.ai/fhir-context/mcp-fhir-context
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# ASGI types — narrow aliases so we don't import the full asgiref machinery.
_Scope = dict[str, object]
_Message = dict[str, object]
_Receive = Callable[[], Awaitable[_Message]]
_Send = Callable[[_Message], Awaitable[None]]
_AsgiApp = Callable[[_Scope, _Receive, _Send], Awaitable[None]]


@dataclass(frozen=True)
class RequestSharpContext:
    """All SHARP-relevant identity sources read from a single HTTP request.

    Either the PO-protocol fields (``patient_id`` + ``fhir_server_url`` +
    ``fhir_access_token``) are present, or the dev ``sharp_token`` JWT is
    present, or neither (resulting in an ``UNAUTHORIZED`` envelope at the
    tool layer).
    """

    patient_id: str | None = None
    fhir_server_url: str | None = None
    fhir_access_token: str | None = None
    fhir_refresh_token: str | None = None
    fhir_refresh_url: str | None = None
    sharp_token: str | None = None

    @property
    def has_po_context(self) -> bool:
        """True when Prompt Opinion supplied a usable FHIR triple."""
        return bool(self.patient_id and self.fhir_server_url and self.fhir_access_token)

    @property
    def has_dev_token(self) -> bool:
        """True when a developer-signed SHARP JWT was supplied for offline use."""
        return bool(self.sharp_token)


# ContextVar default is None to satisfy ruff B039 ("no mutable defaults"); we
# materialise an empty :class:`RequestSharpContext` on read instead.
_current: contextvars.ContextVar[RequestSharpContext | None] = contextvars.ContextVar(
    "_medrec_sharp_request_context",
    default=None,
)


def current_request_context() -> RequestSharpContext:
    """Return the SHARP context captured for the in-flight HTTP request."""
    return _current.get() or RequestSharpContext()


def _decode_header(value: object) -> str | None:
    if isinstance(value, bytes):
        decoded = value.decode("latin-1").strip()
        return decoded or None
    return None


class SharpContextMiddleware:
    """ASGI middleware: snapshot SHARP-bearing headers into a ContextVar.

    Mounted on the outer FastAPI app so it runs before the streamable-HTTP
    sub-app dispatches the JSON-RPC request to a tool handler. The capture
    runs for every HTTP request regardless of path — non-MCP routes simply
    don't read the ContextVar.
    """

    def __init__(self, app: _AsgiApp) -> None:
        self.app = app

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or []
        if not isinstance(raw_headers, list):  # pragma: no cover - defensive
            await self.app(scope, receive, send)
            return

        header_map: dict[bytes, bytes] = {}
        for item in raw_headers:
            if isinstance(item, tuple) and len(item) == 2:
                k, v = item
                if isinstance(k, bytes) and isinstance(v, bytes):
                    header_map[k.lower()] = v

        ctx = RequestSharpContext(
            patient_id=_decode_header(header_map.get(b"x-patient-id")),
            fhir_server_url=_decode_header(header_map.get(b"x-fhir-server-url")),
            fhir_access_token=_decode_header(header_map.get(b"x-fhir-access-token")),
            fhir_refresh_token=_decode_header(header_map.get(b"x-fhir-refresh-token")),
            fhir_refresh_url=_decode_header(header_map.get(b"x-fhir-refresh-url")),
            sharp_token=_decode_header(header_map.get(b"x-sharp-token")),
        )
        token = _current.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)


__all__ = [
    "RequestSharpContext",
    "SharpContextMiddleware",
    "current_request_context",
]
