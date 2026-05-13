"""MCP tool: ``get_patient_context``.

Returns demographics + conditions + allergies + key safety labs (eGFR, AST,
ALT, INR) for the SHARP-bound patient. The Coordinator (and the Drug Safety
Specialist when P2 is reached) consume this to decide things like *"is this
patient's renal function low enough to require a Metformin restart-dose
adjustment?"*.

R3 mechanical: missing labs degrade to ``None`` in the structured payload,
and the tool sets ``partial=True`` + ``missing=[<field names>]`` so the
caller never confuses "absent data" with "normal value".
"""

from __future__ import annotations

import time

import structlog

from medrec_superpower import errors
from medrec_superpower.fhir import FhirClient
from medrec_superpower.schemas import PatientContext, ToolResult
from medrec_superpower.sharp import SharpContext, requires_sharp

logger = structlog.get_logger(__name__)

_TOOL_NAME = "get_patient_context"


@requires_sharp
async def tool_get_patient_context(
    *,
    sharp_context: SharpContext,
    fhir_client: FhirClient,
    patient_id: str,
    encounter_id: str,
) -> ToolResult[PatientContext]:
    """Return the patient's demographics, conditions, allergies, and labs."""
    del sharp_context, encounter_id  # decorator-bound; tools don't use them here
    started_at = time.perf_counter()
    try:
        context = await fhir_client.get_patient_context(patient_id)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception(
            "tool.get_patient_context.upstream_error",
            patient_id=patient_id,
            error=str(exc),
        )
        return ToolResult[PatientContext](
            ok=False,
            error=errors.upstream_error(f"FHIR read failed: {exc}"),
        )
    duration_ms = int((time.perf_counter() - started_at) * 1000)

    missing: list[str] = []
    for field, value in (
        ("egfr", context.egfr),
        ("lft_ast", context.lft_ast),
        ("lft_alt", context.lft_alt),
        ("inr", context.inr),
    ):
        if value is None:
            missing.append(field)
    partial = bool(missing)

    logger.info(
        "tool.get_patient_context.success",
        tool_name=_TOOL_NAME,
        patient_id=patient_id,
        conditions=len(context.conditions),
        allergies=len(context.allergies),
        missing_labs=missing,
        duration_ms=duration_ms,
    )
    return ToolResult[PatientContext](
        ok=True,
        data=context,
        partial=partial,
        missing=missing,
    )


__all__ = ["tool_get_patient_context"]
