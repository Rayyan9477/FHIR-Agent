"""Tests for the SHARP-context HTTP-header capture middleware.

The middleware reads SHARP-bearing headers from each request scope and
stashes them in a :class:`ContextVar` so MCP tool handlers can read them
without the LLM threading them through as arguments. Mistakes here would
let a caller forge patient identity, so every code path needs explicit
coverage.
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from medrec_superpower.sharp.headers import (
    RequestSharpContext,
    SharpContextMiddleware,
    current_request_context,
)

_Scope = dict[str, Any]
_Message = dict[str, Any]


def _scope(headers: list[tuple[bytes, bytes]], scope_type: str = "http") -> _Scope:
    return {"type": scope_type, "headers": headers}


async def _noop_receive() -> _Message:  # pragma: no cover - never invoked
    return {"type": "http.disconnect"}


async def _noop_send(_message: _Message) -> None:  # pragma: no cover
    return None


def _captured_app() -> tuple[Callable[..., Awaitable[None]], list[RequestSharpContext]]:
    """An ASGI app stub that records the active SHARP context."""
    captured: list[RequestSharpContext] = []

    async def app(_scope: _Scope, _r: object, _s: object) -> None:
        captured.append(current_request_context())

    return app, captured


class TestSharpContextMiddleware:
    async def test_po_headers_captured(self) -> None:
        app, captured = _captured_app()
        middleware = SharpContextMiddleware(app)
        await middleware(
            _scope(
                [
                    (b"x-fhir-server-url", b"https://workspace.example/fhir"),
                    (b"x-fhir-access-token", b"po-bearer"),
                    (b"x-patient-id", b"patient-42"),
                ]
            ),
            _noop_receive,
            _noop_send,
        )
        assert len(captured) == 1
        ctx = captured[0]
        assert ctx.patient_id == "patient-42"
        assert ctx.fhir_server_url == "https://workspace.example/fhir"
        assert ctx.fhir_access_token == "po-bearer"
        assert ctx.has_po_context is True
        assert ctx.has_dev_token is False

    async def test_dev_sharp_token_captured(self) -> None:
        app, captured = _captured_app()
        middleware = SharpContextMiddleware(app)
        await middleware(
            _scope([(b"x-sharp-token", b"eyJabc.def.ghi")]),
            _noop_receive,
            _noop_send,
        )
        assert captured[0].sharp_token == "eyJabc.def.ghi"
        assert captured[0].has_dev_token is True
        assert captured[0].has_po_context is False

    async def test_empty_headers_yield_empty_context(self) -> None:
        app, captured = _captured_app()
        middleware = SharpContextMiddleware(app)
        await middleware(_scope([]), _noop_receive, _noop_send)
        ctx = captured[0]
        assert ctx.has_po_context is False
        assert ctx.has_dev_token is False
        assert ctx.patient_id is None

    async def test_partial_po_headers_do_not_count_as_po_context(self) -> None:
        """Two out of three PO headers is not enough — refuse the request."""
        app, captured = _captured_app()
        middleware = SharpContextMiddleware(app)
        await middleware(
            _scope(
                [
                    (b"x-fhir-server-url", b"https://workspace.example/fhir"),
                    (b"x-patient-id", b"patient-42"),
                    # Missing X-FHIR-Access-Token
                ]
            ),
            _noop_receive,
            _noop_send,
        )
        assert captured[0].has_po_context is False

    async def test_header_names_are_case_insensitive(self) -> None:
        """ASGI headers arrive in mixed case; matching is lowercase."""
        app, captured = _captured_app()
        middleware = SharpContextMiddleware(app)
        await middleware(
            _scope(
                [
                    (b"X-Patient-ID", b"abc"),
                    (b"X-FHIR-Server-URL", b"https://x/fhir"),
                    (b"X-FHIR-Access-Token", b"tok"),
                ]
            ),
            _noop_receive,
            _noop_send,
        )
        assert captured[0].has_po_context is True

    async def test_non_http_scope_passes_through(self) -> None:
        """Lifespan / websocket scopes must not crash the middleware."""
        app, captured = _captured_app()
        middleware = SharpContextMiddleware(app)
        await middleware(
            _scope([(b"x-patient-id", b"ignored")], scope_type="lifespan"),
            _noop_receive,
            _noop_send,
        )
        # The app still ran; ContextVar holds the default (empty) value.
        assert captured[0].has_po_context is False

    async def test_context_resets_after_request(self) -> None:
        """Per-request ContextVar must not leak across requests."""
        app, captured = _captured_app()
        middleware = SharpContextMiddleware(app)

        async def _run_in_isolated_context(headers: list[tuple[bytes, bytes]]) -> None:
            await middleware(_scope(headers), _noop_receive, _noop_send)

        ctx = contextvars.copy_context()
        await ctx.run(
            _run_in_isolated_context,
            [
                (b"x-patient-id", b"first"),
                (b"x-fhir-server-url", b"https://a/fhir"),
                (b"x-fhir-access-token", b"t1"),
            ],
        )
        # After the middleware exits, current_request_context() in the test
        # context is the default — no leakage.
        assert current_request_context().has_po_context is False
        assert captured[0].patient_id == "first"

    async def test_whitespace_only_header_treated_as_absent(self) -> None:
        app, captured = _captured_app()
        middleware = SharpContextMiddleware(app)
        await middleware(
            _scope(
                [
                    (b"x-patient-id", b"   "),
                    (b"x-fhir-server-url", b"https://x/fhir"),
                    (b"x-fhir-access-token", b"t"),
                ]
            ),
            _noop_receive,
            _noop_send,
        )
        assert captured[0].patient_id is None
        assert captured[0].has_po_context is False


class TestRequestSharpContextProperties:
    def test_has_po_context_requires_all_three(self) -> None:
        assert RequestSharpContext().has_po_context is False
        assert RequestSharpContext(patient_id="x").has_po_context is False
        assert (
            RequestSharpContext(patient_id="x", fhir_server_url="https://y/fhir").has_po_context
            is False
        )
        assert (
            RequestSharpContext(
                patient_id="x",
                fhir_server_url="https://y/fhir",
                fhir_access_token="t",
            ).has_po_context
            is True
        )

    def test_has_dev_token(self) -> None:
        assert RequestSharpContext().has_dev_token is False
        assert RequestSharpContext(sharp_token="abc").has_dev_token is True


@pytest.fixture(autouse=True)
def _reset_contextvar() -> None:
    """Sanity reset between tests; the middleware should already handle this."""
    return
