"""Factory functions for ``ErrorEnvelope`` by canonical code.

Use these in MCP tools instead of constructing ``ErrorEnvelope`` directly.
Call sites then wrap the envelope in their typed ``ToolResult[T]``:

.. code-block:: python

    from medrec_superpower import errors
    from medrec_superpower.schemas import MedRecord, ToolResult

    return ToolResult[list[MedRecord]](
        ok=False,
        error=errors.forbidden("patient_id mismatch with SHARP scope"),
    )
"""

from __future__ import annotations

from medrec_superpower.schemas import ErrorEnvelope


def forbidden(message: str = "access denied") -> ErrorEnvelope:
    """Cross-patient access or other scope violation (R1)."""
    return ErrorEnvelope(code="FORBIDDEN", message=message, retryable=False)


def unauthorized(message: str = "unauthorized") -> ErrorEnvelope:
    """SHARP token expired / invalid signature."""
    return ErrorEnvelope(code="UNAUTHORIZED", message=message, retryable=False)


def not_found(message: str = "resource not found") -> ErrorEnvelope:
    """Resource explicitly not found. Prefer empty success payloads for FHIR misses."""
    return ErrorEnvelope(code="NOT_FOUND", message=message, retryable=False)


def bad_request(message: str) -> ErrorEnvelope:
    """Client-side input failure (malformed RxCUI, etc.)."""
    return ErrorEnvelope(code="BAD_REQUEST", message=message, retryable=False)


def upstream_error(message: str, *, retryable: bool = True) -> ErrorEnvelope:
    """5xx from an upstream like RxNav / openFDA / FHIR."""
    return ErrorEnvelope(code="UPSTREAM_ERROR", message=message, retryable=retryable)


def timeout(message: str = "upstream timeout") -> ErrorEnvelope:
    """Upstream timed out (after capped retries)."""
    return ErrorEnvelope(code="TIMEOUT", message=message, retryable=True)


def internal(message: str = "internal error") -> ErrorEnvelope:
    """Unexpected internal failure — log + alert, never silently retry."""
    return ErrorEnvelope(code="INTERNAL", message=message, retryable=False)


def schema_validation(message: str) -> ErrorEnvelope:
    """Pydantic validation failure on an outbound payload."""
    return ErrorEnvelope(code="SCHEMA_VALIDATION", message=message, retryable=False)


__all__ = [
    "bad_request",
    "forbidden",
    "internal",
    "not_found",
    "schema_validation",
    "timeout",
    "unauthorized",
    "upstream_error",
]
