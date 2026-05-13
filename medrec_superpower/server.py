"""medrec-superpower MCP server entry point.

Builds a :class:`FastMCP` server with:

* Lifespan-managed :class:`AppContext` holding the :class:`FixtureLoader`
  (dev fallback) and :class:`RxNavClient` (initialized once per process,
  closed on shutdown). The production FHIR client is constructed per-request
  from the Prompt Opinion ``X-FHIR-*`` headers (see
  :class:`PoFhirClient`).
* The three P0 tools registered as thin shims that bridge from the MCP wire
  surface to our typed, decorator-bound tool functions in
  :mod:`medrec_superpower.tools`.

**SHARP integration** (Prompt Opinion protocol —
https://docs.promptopinion.ai/fhir-context/mcp-fhir-context):

1. On ``initialize`` we declare the ``ai.promptopinion/fhir-context``
   capability extension so the platform agrees to forward FHIR context.
2. On every tool call the platform supplies these HTTP headers:
   * ``X-FHIR-Server-URL`` — workspace FHIR base URL
   * ``X-FHIR-Access-Token`` — bearer for that server
   * ``X-Patient-ID`` — currently-scoped patient
3. A Starlette middleware captures these into a :class:`ContextVar` so the
   tool wrappers can read them without the LLM threading them as args.

A developer escape hatch (``X-Sharp-Token`` header carrying an RS256-signed
JWT) keeps the offline / curl / unit-test path working.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

import structlog
from fastapi import FastAPI
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from medrec_superpower import __version__, errors
from medrec_superpower.drug import RxNavClient
from medrec_superpower.fhir import FhirClient, FixtureLoader, PoFhirClient
from medrec_superpower.schemas import ToolResult
from medrec_superpower.sharp import (
    InitializeResponseRewriteMiddleware,
    KeyResolver,
    RequestSharpContext,
    SharpContext,
    SharpContextMiddleware,
    SharpForbidden,
    SharpUnauthorized,
    StaticKeyResolver,
    current_request_context,
    validate_sharp,
)
from medrec_superpower.sharp.jwt import SharpError
from medrec_superpower.tools import (
    tool_check_interaction,
    tool_get_discharge_meds,
    tool_get_drug_education_handout,
    tool_get_patient_context,
    tool_get_pre_admit_meds,
    tool_lookup_rxnorm,
    tool_parse_discharge_summary,
)

logger = structlog.get_logger(__name__)

_UTC = timezone.utc
_PO_DEFAULT_ENCOUNTER = "Encounter/PROMPT_OPINION_DEFAULT"
_SERVER_NAME = "medrec-superpower"
_SERVER_VERSION = __version__
_CAPABILITIES: tuple[str, ...] = ("medrec.fhir_data", "medrec.reconcile")

# The SMART scopes we request when Prompt Opinion enables our FHIR extension.
# ``patient/<resource>.rs`` = read + search for the current patient.
_PO_FHIR_EXTENSION: dict[str, object] = {
    "scopes": [
        {"name": "patient/Patient.rs", "required": True},
        {"name": "patient/MedicationStatement.rs", "required": True},
        {"name": "patient/MedicationRequest.rs", "required": True},
        {"name": "patient/Encounter.rs"},
        {"name": "patient/Condition.rs"},
    ]
}


@dataclass
class AppContext:
    """Process-wide lifespan-managed services.

    ``sharp_audience`` / ``sharp_issuer`` are ``None`` when the corresponding
    claim check is intentionally disabled via env (demo escape hatch). The
    signature + expiry + required-claims checks are always enforced.
    """

    fhir_client: FhirClient
    rxnav_client: RxNavClient
    sharp_resolver: KeyResolver
    sharp_audience: str | None
    sharp_issuer: str | None


def _build_sharp_resolver() -> KeyResolver:
    """Build the SHARP key resolver from env.

    Used only by the developer escape-hatch path (``X-Sharp-Token``); the
    primary Prompt Opinion path uses HTTP headers and does not require this.
    For P0 we accept a local public-key PEM via ``SHARP_PUBLIC_KEY_PEM``.
    """
    pem_path = os.environ.get("SHARP_PUBLIC_KEY_PEM")
    if pem_path:
        with open(pem_path, "rb") as f:
            return StaticKeyResolver(f.read())
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
        # Demo escape hatches: when Prompt Opinion emits a dev-path SHARP JWT
        # whose aud/iss don't match our configured values, set these env vars
        # to "1" to bypass those individual claim checks. Signature + expiry
        # + required-claims are always enforced.
        allow_any_aud = os.environ.get("MEDREC_SHARP_ALLOW_ANY_AUDIENCE") == "1"
        allow_any_iss = os.environ.get("MEDREC_SHARP_ALLOW_ANY_ISSUER") == "1"
        audience: str | None = (
            None if allow_any_aud else os.environ.get("SHARP_AUDIENCE", _SERVER_NAME)
        )
        issuer: str | None = (
            None if allow_any_iss else os.environ.get("SHARP_ISSUER", "promptopinion.ai")
        )
        async with RxNavClient() as rxnav:
            ctx = AppContext(
                fhir_client=fhir,
                rxnav_client=rxnav,
                sharp_resolver=sharp_resolver,
                sharp_audience=audience,
                sharp_issuer=issuer,
            )
            logger.info(
                "server.lifespan.started",
                capabilities=list(_CAPABILITIES),
                sharp_aud_enforced=audience is not None,
                sharp_iss_enforced=issuer is not None,
            )
            try:
                yield ctx
            finally:
                logger.info("server.lifespan.stopped")

    return _lifespan


def _build_transport_security() -> TransportSecuritySettings:
    """Build FastMCP's DNS-rebinding defense from env.

    FastMCP defaults to ``enable_dns_rebinding_protection=True`` with an
    empty ``allowed_hosts``, which rejects every request. We invert that to
    a safe default and let operators tighten via env:

    * ``MEDREC_ALLOWED_HOSTS`` — comma-separated Host header allowlist.
      When set, DNS-rebinding protection is enabled and limited to these.
    * ``MEDREC_DISABLE_HOST_CHECK=1`` — disable protection entirely.
      Acceptable behind a managed reverse proxy (ngrok demo path); the
      proxy's TLS + auth bound the trust. Refuse in production by default.
    """
    if os.environ.get("MEDREC_DISABLE_HOST_CHECK") == "1":
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    raw = os.environ.get("MEDREC_ALLOWED_HOSTS", "")
    allowed = [h.strip() for h in raw.split(",") if h.strip()]
    if not allowed:
        allowed = ["127.0.0.1", "localhost", "testserver"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed,
    )


def _install_capability_extension(mcp: FastMCP[AppContext]) -> None:
    """Inject ``ai.promptopinion/fhir-context`` into the ``initialize`` reply.

    FastMCP doesn't expose ``experimental_capabilities`` through its public
    surface — we wrap the underlying low-level server's
    ``create_initialization_options`` method to add ours every time it's
    called. The supplementary :class:`InitializeResponseRewriteMiddleware`
    mirrors the resulting ``experimental`` block to ``extensions`` so the
    Prompt Opinion docs format works too.
    """
    underlying = mcp._mcp_server  # intentional FastMCP hook — see docstring
    original = underlying.create_initialization_options

    def patched(
        notification_options: object | None = None,
        experimental_capabilities: dict[str, object] | None = None,
    ) -> object:
        caps: dict[str, object] = dict(experimental_capabilities or {})
        caps.setdefault("ai.promptopinion/fhir-context", _PO_FHIR_EXTENSION)
        return original(notification_options, caps)  # type: ignore[arg-type]

    underlying.create_initialization_options = patched  # type: ignore[assignment,method-assign]


def build_mcp(sharp_resolver: KeyResolver) -> FastMCP[AppContext]:
    """Construct the FastMCP server with lifespan + tools wired in."""
    mcp = FastMCP(
        _SERVER_NAME,
        lifespan=_make_app_lifespan(sharp_resolver),
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
        transport_security=_build_transport_security(),
    )
    _install_capability_extension(mcp)

    async def _resolve_sharp_context(
        ctx: Context[ServerSession, AppContext, object],
    ) -> SharpContext | ToolResult[object]:
        """Resolve a :class:`SharpContext` from the in-flight HTTP request.

        Tries Prompt Opinion's FHIR-context headers first; falls back to a
        signed ``X-Sharp-Token`` JWT for the dev / curl / unit-test path.
        Returns a typed error envelope when neither is usable.
        """
        req: RequestSharpContext = current_request_context()
        if req.has_po_context:
            assert req.patient_id is not None  # nosec B101  # narrowed by has_po_context
            now = datetime.now(_UTC)
            # PO doesn't supply encounter — use a sentinel; PoFhirClient
            # interprets it as "patient-scoped search".
            return SharpContext(
                patient_id=req.patient_id,
                encounter_id=_PO_DEFAULT_ENCOUNTER,
                fhir_token=req.fhir_access_token,
                user_role="patient",
                issued_at=now,
                expires_at=now + timedelta(hours=1),
                issuer="promptopinion.ai",
                audience=_SERVER_NAME,
            )
        if req.has_dev_token:
            assert req.sharp_token is not None  # nosec B101  # narrowed by has_dev_token
            app = ctx.request_context.lifespan_context
            try:
                return await validate_sharp(
                    req.sharp_token,
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
        logger.warning("sharp.missing_context")
        return ToolResult[object](
            ok=False,
            error=errors.unauthorized(
                "no SHARP context: expected Prompt Opinion FHIR headers "
                "(X-Patient-ID, X-FHIR-Server-URL, X-FHIR-Access-Token) "
                "or a dev X-Sharp-Token"
            ),
        )

    @asynccontextmanager
    async def _open_fhir(app: AppContext) -> AsyncIterator[FhirClient]:
        """Return a per-request FHIR client.

        PO path → :class:`PoFhirClient` against the workspace FHIR server.
        Dev path → the lifespan-shared :class:`FixtureLoader`.
        """
        req = current_request_context()
        if req.has_po_context:
            assert req.fhir_server_url is not None  # nosec B101
            assert req.fhir_access_token is not None  # nosec B101
            assert req.patient_id is not None  # nosec B101
            async with PoFhirClient(
                fhir_server_url=req.fhir_server_url,
                access_token=req.fhir_access_token,
                patient_id=req.patient_id,
            ) as client:
                yield client
            return
        yield app.fhir_client

    @mcp.tool(
        name="get_pre_admit_meds",
        description=(
            "Return the patient's medications recorded BEFORE the current "
            "encounter started. Patient identity is bound to the SHARP/FHIR "
            "context forwarded by Prompt Opinion — never an LLM-controlled arg."
        ),
    )
    async def _get_pre_admit_meds(
        ctx: Context[ServerSession, AppContext, object],
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp_context(ctx)
        if isinstance(sharp_or_err, ToolResult):
            return sharp_or_err.model_dump(mode="json")
        app = ctx.request_context.lifespan_context
        async with _open_fhir(app) as fhir:
            result = await tool_get_pre_admit_meds(
                sharp_context=sharp_or_err,
                fhir_client=fhir,
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
        ctx: Context[ServerSession, AppContext, object],
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp_context(ctx)
        if isinstance(sharp_or_err, ToolResult):
            return sharp_or_err.model_dump(mode="json")
        app = ctx.request_context.lifespan_context
        async with _open_fhir(app) as fhir:
            result = await tool_get_discharge_meds(
                sharp_context=sharp_or_err,
                fhir_client=fhir,
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
        rxcui_a: Annotated[str, Field(description="First RxNorm Concept ID")],
        rxcui_b: Annotated[str, Field(description="Second RxNorm Concept ID")],
        ctx: Context[ServerSession, AppContext, object],
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp_context(ctx)
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

    @mcp.tool(
        name="get_patient_context",
        description=(
            "Return the patient's demographics, conditions, allergies, and "
            "safety-critical labs (eGFR, AST, ALT, INR). Use this before "
            "deciding renal/hepatic dosing or interaction severity. Missing "
            "labs are null in the data — never substituted."
        ),
    )
    async def _get_patient_context(
        ctx: Context[ServerSession, AppContext, object],
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp_context(ctx)
        if isinstance(sharp_or_err, ToolResult):
            return sharp_or_err.model_dump(mode="json")
        app = ctx.request_context.lifespan_context
        async with _open_fhir(app) as fhir:
            result = await tool_get_patient_context(
                sharp_context=sharp_or_err,
                fhir_client=fhir,
                patient_id=sharp_or_err.patient_id,
                encounter_id=sharp_or_err.encounter_id,
            )
        return result.model_dump(mode="json")

    @mcp.tool(
        name="parse_discharge_summary",
        description=(
            "Extract structured medication changes (HOLD/STOP/START/DOSE "
            "CHANGE) from the encounter's discharge summary narrative. "
            "Prefer this over inferring changes from comparing med lists — "
            "the discharge summary often carries restart conditions "
            "(e.g., 'HOLD 48h after CT contrast'). Returns empty + "
            "partial=true if no discharge document is on file."
        ),
    )
    async def _parse_discharge_summary(
        ctx: Context[ServerSession, AppContext, object],
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp_context(ctx)
        if isinstance(sharp_or_err, ToolResult):
            return sharp_or_err.model_dump(mode="json")
        app = ctx.request_context.lifespan_context
        async with _open_fhir(app) as fhir:
            result = await tool_parse_discharge_summary(
                sharp_context=sharp_or_err,
                fhir_client=fhir,
                patient_id=sharp_or_err.patient_id,
                encounter_id=sharp_or_err.encounter_id,
            )
        return result.model_dump(mode="json")

    @mcp.tool(
        name="lookup_rxnorm",
        description=(
            "Resolve a free-text drug name (e.g. 'Metformin') to ranked "
            "RxCUI candidates via RxNav. Call this when you need an RxCUI "
            "for check_interaction or get_drug_education_handout — never "
            "guess RxCUIs from training data (R3)."
        ),
    )
    async def _lookup_rxnorm(
        term: Annotated[str, Field(description="Free-text drug name to resolve")],
        ctx: Context[ServerSession, AppContext, object],
        max_results: Annotated[int, Field(description="Max candidates (1-50)", ge=1, le=50)] = 5,
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp_context(ctx)
        if isinstance(sharp_or_err, ToolResult):
            return sharp_or_err.model_dump(mode="json")
        app = ctx.request_context.lifespan_context
        result = await tool_lookup_rxnorm(
            sharp_context=sharp_or_err,
            rxnav_client=app.rxnav_client,
            patient_id=sharp_or_err.patient_id,
            encounter_id=sharp_or_err.encounter_id,
            term=term,
            max_results=max_results,
        )
        return result.model_dump(mode="json")

    @mcp.tool(
        name="get_drug_education_handout",
        description=(
            "Return an authoritative MedlinePlus URL for a drug, keyed by "
            "RxCUI when available. Use this for every patient-facing drug "
            "claim you emit — R4 mechanical (never compose URLs from "
            "training data)."
        ),
    )
    async def _get_drug_education_handout(
        display: Annotated[str, Field(description="Drug display name, e.g. 'Metformin'")],
        ctx: Context[ServerSession, AppContext, object],
        rxcui: Annotated[str | None, Field(description="RxCUI for an exact-match page")] = None,
    ) -> dict[str, object]:
        sharp_or_err = await _resolve_sharp_context(ctx)
        if isinstance(sharp_or_err, ToolResult):
            return sharp_or_err.model_dump(mode="json")
        result = await tool_get_drug_education_handout(
            sharp_context=sharp_or_err,
            patient_id=sharp_or_err.patient_id,
            encounter_id=sharp_or_err.encounter_id,
            rxcui=rxcui,
            display=display,
        )
        return result.model_dump(mode="json")

    return mcp


def build_http_app() -> FastAPI:
    """Build the outer FastAPI app: ``/healthz`` + mounted MCP server.

    **Fails closed at construction time** if SHARP key configuration is
    missing — we refuse to expose any endpoint without working identity
    validation. The :class:`KeyResolver` is constructed eagerly here.

    The middleware stack is (outermost → innermost):

    1. :class:`SharpContextMiddleware` — captures Prompt Opinion FHIR headers
       and the dev ``X-Sharp-Token`` into a ContextVar per HTTP request.
    2. :class:`InitializeResponseRewriteMiddleware` — mirrors MCP capability
       extensions from ``experimental`` to ``extensions`` so both spelling
       conventions work on the wire.

    The FastMCP ``StreamableHTTPSessionManager`` runs an anyio task group
    that must be entered via the outer ASGI lifespan; we delegate the sub-
    app's lifespan from the outer FastAPI app.
    """
    sharp_resolver = _build_sharp_resolver()
    mcp = build_mcp(sharp_resolver)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def _outer_lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp_app.router.lifespan_context(app):
            yield

    app = FastAPI(
        title=_SERVER_NAME,
        version=_SERVER_VERSION,
        description=(
            "Post-discharge medication reconciliation MCP server. "
            "FHIR + RxNav + SHARP-bound identity."
        ),
        lifespan=_outer_lifespan,
    )

    app.add_middleware(SharpContextMiddleware)
    app.add_middleware(InitializeResponseRewriteMiddleware)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "service": _SERVER_NAME,
            "version": _SERVER_VERSION,
            "capabilities": list(_CAPABILITIES),
        }

    app.mount("/mcp", mcp_app)
    return app


__all__ = ["AppContext", "build_http_app", "build_mcp"]
