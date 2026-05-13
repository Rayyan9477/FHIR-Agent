"""ASGI middleware that mirrors ``capabilities.experimental`` →
``capabilities.extensions`` on MCP ``initialize`` responses.

The MCP wire spec puts custom capability extensions under
``ServerCapabilities.experimental``. Prompt Opinion's docs at
https://docs.promptopinion.ai/fhir-context/mcp-fhir-context use the key
``extensions`` instead. We emit both so the same server works against
either reading.

The middleware only touches JSON HTTP responses whose body is a valid
JSON-RPC initialize result. Everything else passes through untouched.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

_Scope = dict[str, object]
_Message = dict[str, object]
_Receive = Callable[[], Awaitable[_Message]]
_Send = Callable[[_Message], Awaitable[None]]
_AsgiApp = Callable[[_Scope, _Receive, _Send], Awaitable[None]]


def _is_json_content_type(headers: list[object]) -> bool:
    for item in headers:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        k, v = item
        if not isinstance(k, bytes) or not isinstance(v, bytes):
            continue
        if k.lower() == b"content-type":
            return b"application/json" in v.lower()
    return False


def _rewrite_payload(body: bytes) -> bytes:
    """Add ``extensions`` mirror to a JSON-RPC initialize response."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(data, dict):
        return body
    result = data.get("result")
    if not isinstance(result, dict):
        return body
    caps = result.get("capabilities")
    if not isinstance(caps, dict):
        return body
    experimental = caps.get("experimental")
    if not isinstance(experimental, dict) or not experimental:
        return body
    # Mirror — never overwrite an existing extensions field.
    caps.setdefault("extensions", experimental)
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


class InitializeResponseRewriteMiddleware:
    """Mirror ``experimental`` → ``extensions`` on JSON responses.

    Buffers the full response body before forwarding so we can swap the
    Content-Length header. Acceptable because Prompt Opinion's streamable
    transport uses ``json_response=True`` (single JSON blob per call).
    """

    def __init__(self, app: _AsgiApp) -> None:
        self.app = app

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start_message: _Message | None = None
        body_chunks: list[bytes] = []
        completed = False

        async def buffered_send(message: _Message) -> None:
            nonlocal start_message, completed
            mtype = message.get("type")
            if mtype == "http.response.start":
                start_message = message
                return
            if mtype == "http.response.body":
                chunk = message.get("body", b"")
                if isinstance(chunk, bytes):
                    body_chunks.append(chunk)
                if not message.get("more_body", False):
                    completed = True
                    await _flush(start_message, body_chunks, send)
                return
            await send(message)

        await self.app(scope, receive, buffered_send)
        if not completed and start_message is not None:
            # Defensive: stream ended without a final body marker; flush what we have.
            await _flush(start_message, body_chunks, send)


async def _flush(
    start: _Message | None,
    chunks: list[bytes],
    send: _Send,
) -> None:
    if start is None:
        return
    body = b"".join(chunks)
    headers_obj = start.get("headers") or []
    headers_list = list(headers_obj) if isinstance(headers_obj, list) else []
    new_body = _rewrite_payload(body) if _is_json_content_type(headers_list) else body
    if new_body is not body:
        new_headers: list[tuple[bytes, bytes]] = []
        for item in headers_list:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            k, v = item
            if isinstance(k, bytes) and k.lower() == b"content-length":
                continue
            if isinstance(k, bytes) and isinstance(v, bytes):
                new_headers.append((k, v))
        new_headers.append((b"content-length", str(len(new_body)).encode("ascii")))
        start = {**start, "headers": new_headers}
    await send(start)
    await send({"type": "http.response.body", "body": new_body, "more_body": False})


__all__ = ["InitializeResponseRewriteMiddleware"]
