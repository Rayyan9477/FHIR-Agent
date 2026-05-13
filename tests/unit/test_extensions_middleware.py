"""Tests for the MCP capability-extension rewrite middleware.

The middleware mirrors ``capabilities.experimental`` →
``capabilities.extensions`` on JSON-RPC ``initialize`` responses so that
Prompt Opinion (which inspects ``extensions``) sees the same value MCP
emits under ``experimental``. Anything that isn't an initialize JSON
response must pass through untouched.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from medrec_superpower.sharp.extensions_middleware import (
    InitializeResponseRewriteMiddleware,
)

_Scope = dict[str, Any]
_Message = dict[str, Any]


def _http_scope() -> _Scope:
    return {"type": "http", "headers": []}


async def _noop_receive() -> _Message:  # pragma: no cover
    return {"type": "http.disconnect"}


_SendFn = Callable[[_Message], Awaitable[None]]


def _stub_app(
    body: bytes,
    content_type: bytes = b"application/json",
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> Callable[..., Awaitable[None]]:
    """Return an ASGI app stub that emits a single response with given body."""

    async def app(_scope: _Scope, _receive: object, send: _SendFn) -> None:
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", content_type),
            (b"content-length", str(len(body)).encode()),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    return app


async def _collect(
    middleware: InitializeResponseRewriteMiddleware,
    scope: _Scope | None = None,
) -> tuple[_Message, bytes]:
    """Run the middleware against the stub and return start-message + body."""
    captured_start: dict[str, Any] = {}
    body_chunks: list[bytes] = []

    async def send(message: _Message) -> None:
        if message["type"] == "http.response.start":
            captured_start.update(message)
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))

    await middleware(scope or _http_scope(), _noop_receive, send)
    return captured_start, b"".join(body_chunks)


def _initialize_response(
    *,
    with_experimental: bool = True,
    extra_caps: dict[str, Any] | None = None,
) -> bytes:
    caps: dict[str, Any] = dict(extra_caps or {})
    if with_experimental:
        caps["experimental"] = {
            "ai.promptopinion/fhir-context": {"scopes": [{"name": "patient/Patient.rs"}]}
        }
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": caps}}).encode()


class TestRewrite:
    async def test_experimental_is_mirrored_to_extensions(self) -> None:
        body = _initialize_response()
        app = _stub_app(body)
        _, new_body = await _collect(InitializeResponseRewriteMiddleware(app))
        data = json.loads(new_body)
        caps = data["result"]["capabilities"]
        assert "experimental" in caps  # original preserved
        assert caps["extensions"] == caps["experimental"]
        assert (
            caps["extensions"]["ai.promptopinion/fhir-context"]["scopes"][0]["name"]
            == "patient/Patient.rs"
        )

    async def test_no_experimental_is_passthrough(self) -> None:
        body = _initialize_response(with_experimental=False)
        app = _stub_app(body)
        _, new_body = await _collect(InitializeResponseRewriteMiddleware(app))
        data = json.loads(new_body)
        assert "extensions" not in data["result"]["capabilities"]

    async def test_existing_extensions_not_overwritten(self) -> None:
        body = _initialize_response(extra_caps={"extensions": {"already": {"there": True}}})
        app = _stub_app(body)
        _, new_body = await _collect(InitializeResponseRewriteMiddleware(app))
        data = json.loads(new_body)
        # ``setdefault`` semantics — existing wins.
        assert data["result"]["capabilities"]["extensions"] == {"already": {"there": True}}

    async def test_non_json_response_passthrough(self) -> None:
        body = b"<html>not json</html>"
        app = _stub_app(body, content_type=b"text/html")
        start, new_body = await _collect(InitializeResponseRewriteMiddleware(app))
        assert new_body == body
        # content-length untouched since body wasn't rewritten
        headers = dict(start["headers"])
        assert headers[b"content-length"] == str(len(body)).encode()

    async def test_invalid_json_passthrough(self) -> None:
        body = b"{not valid json"
        app = _stub_app(body)
        _, new_body = await _collect(InitializeResponseRewriteMiddleware(app))
        assert new_body == body

    async def test_non_jsonrpc_result_passthrough(self) -> None:
        body = json.dumps({"hello": "world"}).encode()
        app = _stub_app(body)
        _, new_body = await _collect(InitializeResponseRewriteMiddleware(app))
        assert new_body == body

    async def test_content_length_updated_after_rewrite(self) -> None:
        body = _initialize_response()
        app = _stub_app(body)
        start, new_body = await _collect(InitializeResponseRewriteMiddleware(app))
        headers = dict(start["headers"])
        assert headers[b"content-length"] == str(len(new_body)).encode()
        assert len(new_body) > len(body), "expected rewrite to grow the payload"

    async def test_non_http_scope_passthrough(self) -> None:
        body = _initialize_response()
        app = _stub_app(body)
        # Lifespan scope should be a complete pass-through (no rewrite logic).
        await _collect(
            InitializeResponseRewriteMiddleware(app),
            scope={"type": "lifespan", "headers": []},
        )
