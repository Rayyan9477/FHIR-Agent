"""medrec-superpower MCP server entry point.

Builds a :class:`FastMCP` server with:

* Lifespan-managed :class:`AppContext` holding the :class:`FhirClient` and
  :class:`RxNavClient` (initialized once per process, closed on shutdown).
* Three P0 tools registered as thin shims that bridge from the MCP wire
  surface to our typed, decorator-bound tool functions in
  :mod:`medrec_superpower.tools`.

**SHARP integration note (P0)**: per ``docs/design/SHARP_CONTEXT.md`` the
SHARP JWT is expected to ride in an ``x-sharp-context`` HTTP header set by
the Prompt Opinion A2A runtime. Pending platform confirmation (RISKS Q1),
P0 also accepts the JWT as an explicit ``sharp_token`` tool parameter —
the Prompt Opinion runtime auto-injects it, the LLM never constructs it.
The server validates the token on every call and converts to a typed
:class:`SharpContext` before invoking any tool body.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import FastAPI
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import Field

from medrec_superpower import __version__, errors
from medrec_superpower.drug import RxNavClient
from medrec_superpower.fhir import FhirClient, FixtureLoader
from medrec_superpower.schemas import ToolResult
from medrec_superpower.sharp import (
    KeyResolver,
    SharpContext,
    SharpForbidden,
    SharpUnauthorized,
    StaticKeyResolver,
    validate_sharp,
)
from medrec_superpower.sharp.jwt import SharpError
from medrec_superpower.tools import (
    tool_check_interaction,
    tool_get_discharge_meds,
    tool_get_pre_admit_meds,
)

logger = structlog.get_logger(__name__)


@dataclass
class AppContext:
    """Process-wide lifespan-managed services."""

    fhir_client: FhirClient
    rxnav_client: RxNavClient
    sharp_resolver: KeyResolver
    sharp_audience: str
    sharp_issuer: str


_SERVER_NAME = "medrec-superpower"
_CAPABILITIES: tuple[str, ...] = ("medrec.fhir_data", "medrec.reconcile")


def _build_sharp_resolver() -> KeyResolver:
    """Build the SHARP key resolver from env.

    For P0 we accept a local public-key PEM via ``SHARP_PUBLIC_KEY_PEM``
    (path) so the server can run without a live JWKS endpoint. When
    ``SHARP_JWKS_URL`` is set, the production :class:`JWKSResolver` is
    used. See ``docs/reference/RISKS.md`` Q1.
    """
    pem_path = os.environ.get("SHARP_PUBLIC_KEY_PEM")
    if pem_path:
        with open(pem_path, "rb") as f:
            return StaticKeyResolver(f.read())
    # Prod path will be: JWKSResolver(os.environ["SHARP_JWKS_URL"])
    # P0: refuse to start without a key configured — fail closed.
    raise RuntimeError(
        "no SHARP key configured. Set SHARP_PUBLIC_KEY_PEM (P0) or SHARP_JWKS_URL (P1+)."
    )


def _make_app_lifespan(
    sharp_resolver: KeyResolver,
) -> Callable[[FastMCP[AppContext]], AbstractAsyncContextManager[AppContext]]:
    """Bind the (already-validated) SHARP resolver into a lifespan factory."""

    @asynccontextmanager
    async def _lifespan(_server: FastMCP[AppContext]) -> AsyncIterator[AppContext]:
        fhir = FixtureLoader()
        rxnav = RxNavClient()
        await rxnav.__aenter__()
        ctx = AppContext(
            fhir_client=fhir,
            rxnav_client=rxnav,
            sharp_resolver=sharp_resolver,
            sharp_audience=os.environ.get("SHARP_AUDIENCE", _SERVER_NAME),
            sharp_issuer=os.environ.get("SHARP_ISSUER", "promptopinion.ai"),
        )
        logger.info("server.lifespan.started", capabilities=list(_CAPABILITIES))
        try:
            yield ctx
        finally:
            await rxnav.__aexit__(None, None, None)
            logger.info("server.lifespan.stopped")

    return _lifespan


def build_mcp(sharp_resolver: KeyResolver) -> FastMCP[AppContext]:
    """Construct the FastMCP server with lifespan + tools wired in."""
    mcp = FastMCP(
        _SERVER_NAME,
        lifespan=_make_app_lifespan(sharp_resolver),
        json_response=True,
        stateless_http=True,
    )

    async def _resolve_sharp(
        ctx: Context[ServerSession, AppContext, object], token: str
    ) -> SharpContext | ToolResult[object]:
        """Validate the SHARP JWT. Return a typed ToolResult on failure."""
        app = ctx.request_context.lifespan_context
        try:
            return await validate_sharp(
                token,
                key_resolver=app.sharp_resolver,
                audience=app.sharp_audience,
                issuer=app.sharp_issuer,
            )
        except SharpUnauthorized as exc:
            logger.warning("sharp.unauthorized", error=str(exc))
            return ToolResult[object](ok=False, error=errors.unauthorized(str(exc)))
        except SharpForbidden as exc:
            logger.warning("sharp.forbidden", error=str(exc))
            return ToolResult[object](ok=False, error=errors.forbidden(str(exc)))
        except SharpError as exc:
            logger.warning("sharp.error", error=str(exc))
            return ToolResult[object](ok=False, error=errors.unauthorized(str(exc)))

    @mcp.tool(
        name="get_pre_admit_meds",
        description=(
            "Return the patient's medications recorded BEFORE the current "
            "encounter started. Identity is bound to the SHARP context — "
            "patient_id is never an LLM-controlled argument."
        ),
    )
    async def _get_pre_admit_meds(
        sharp_token: Annotated[
            str,
            Field(
                description=(
                    "SHARP JWT auto-injected by the Prompt Opinion runtime. "
                    "Never construct this from chat."
                )
            ),
        ],
        ctx: Context[ServerSession, AppContext, object],
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp(ctx, sharp_token)
        if isinstance(sharp_or_err, ToolResult):
            return sharp_or_err.model_dump(mode="json")
        app = ctx.request_context.lifespan_context
        result = await tool_get_pre_admit_meds(
            sharp_context=sharp_or_err,
            fhir_client=app.fhir_client,
            patient_id=sharp_or_err.patient_id,
            encounter_id=sharp_or_err.encounter_id,
        )
        return result.model_dump(mode="json")

    @mcp.tool(
        name="get_discharge_meds",
        description=(
            "Return the patient's medications prescribed at discharge for "
            "the current encounter (intent='discharge')."
        ),
    )
    async def _get_discharge_meds(
        sharp_token: Annotated[str, Field(description="SHARP JWT (auto-injected)")],
        ctx: Context[ServerSession, AppContext, object],
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp(ctx, sharp_token)
        if isinstance(sharp_or_err, ToolResult):
            return sharp_or_err.model_dump(mode="json")
        app = ctx.request_context.lifespan_context
        result = await tool_get_discharge_meds(
            sharp_context=sharp_or_err,
            fhir_client=app.fhir_client,
            patient_id=sharp_or_err.patient_id,
            encounter_id=sharp_or_err.encounter_id,
        )
        return result.model_dump(mode="json")

    @mcp.tool(
        name="check_interaction",
        description=(
            "Look up clinically-significant drug-drug interactions between "
            "two RxCUIs via RxNav. On upstream failure returns "
            "data.check_succeeded=false (R3 — never hallucinate drug data)."
        ),
    )
    async def _check_interaction(
        sharp_token: Annotated[str, Field(description="SHARP JWT (auto-injected)")],
        rxcui_a: Annotated[str, Field(description="First RxNorm Concept ID")],
        rxcui_b: Annotated[str, Field(description="Second RxNorm Concept ID")],
        ctx: Context[ServerSession, AppContext, object],
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp(ctx, sharp_token)
        if isinstance(sharp_or_err, ToolResult):
            return sharp_or_err.model_dump(mode="json")
        app = ctx.request_context.lifespan_context
        result = await tool_check_interaction(
            sharp_context=sharp_or_err,
            rxnav_client=app.rxnav_client,
            patient_id=sharp_or_err.patient_id,
            encounter_id=sharp_or_err.encounter_id,
            rxcui_a=rxcui_a,
            rxcui_b=rxcui_b,
        )
        return result.model_dump(mode="json")

    return mcp


def build_http_app() -> FastAPI:
    """Build the outer FastAPI app: ``/healthz`` + mounted MCP server.

    **Fails closed at construction time** if SHARP key configuration is
    missing — we refuse to expose any endpoint without working identity
    validation. The :class:`KeyResolver` is constructed eagerly here.
    """
    sharp_resolver = _build_sharp_resolver()
    mcp = build_mcp(sharp_resolver)
    app = FastAPI(
        title=_SERVER_NAME,
        version=__version__,
        description=(
            "Post-discharge medication reconciliation MCP server. "
            "FHIR + RxNav + SHARP-bound identity."
        ),
    )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "service": _SERVER_NAME,
            "version": __version__,
            "capabilities": list(_CAPABILITIES),
        }

    # Mount the MCP streamable-HTTP transport at /mcp.
    app.mount("/mcp", mcp.streamable_http_app())
    return app


__all__ = ["AppContext", "build_http_app", "build_mcp"]
