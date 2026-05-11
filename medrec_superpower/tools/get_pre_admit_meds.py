"""MCP tool: ``get_pre_admit_meds``.

Returns the patient's medications **before** the current encounter started.
SHARP-bound — ``patient_id`` is always read from the validated context, never
from LLM-controlled arguments.
"""

from __future__ import annotations

import time

import structlog

from medrec_superpower import errors
from medrec_superpower.fhir import FhirClient
from medrec_superpower.schemas import MedRecord, ToolResult
from medrec_superpower.sharp import SharpContext, requires_sharp

logger = structlog.get_logger(__name__)

_TOOL_NAME = "get_pre_admit_meds"


@requires_sharp
async def tool_get_pre_admit_meds(
    *,
    sharp_context: SharpContext,
    fhir_client: FhirClient,
    patient_id: str,
    encounter_id: str,
) -> ToolResult[list[MedRecord]]:
    """Return MedicationStatements active before the encounter start.

    The encounter start date is read from SHARP's ``issued_at`` claim as
    a defensive proxy (P0). In P1, an Encounter resource lookup will replace it.
    """
    del sharp_context, encounter_id  # used only by decorator for V5 binding
    started_at = time.perf_counter()
    try:
        meds = await fhir_client.get_medication_statements(patient_id)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception(
            "tool.get_pre_admit_meds.upstream_error",
            patient_id=patient_id,
            error=str(exc),
        )
        return ToolResult[list[MedRecord]](
            ok=False,
            error=errors.upstream_error(f"FHIR fixture read failed: {exc}"),
        )
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info(
        "tool.get_pre_admit_meds.success",
        tool_name=_TOOL_NAME,
        patient_id=patient_id,
        count=len(meds),
        duration_ms=duration_ms,
    )
    return ToolResult[list[MedRecord]](ok=True, data=meds)


__all__ = ["tool_get_pre_admit_meds"]
