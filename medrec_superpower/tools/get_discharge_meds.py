"""MCP tool: ``get_discharge_meds``.

Returns the patient's medications prescribed at discharge for the current
encounter. SHARP-bound on ``encounter_id``.
"""

from __future__ import annotations

import time

import structlog

from medrec_superpower import errors
from medrec_superpower.fhir import FhirClient
from medrec_superpower.schemas import MedRecord, ToolResult
from medrec_superpower.sharp import SharpContext, requires_sharp

logger = structlog.get_logger(__name__)

_TOOL_NAME = "get_discharge_meds"


@requires_sharp
async def tool_get_discharge_meds(
    *,
    sharp_context: SharpContext,
    fhir_client: FhirClient,
    patient_id: str,
    encounter_id: str,
) -> ToolResult[list[MedRecord]]:
    """Return MedicationRequest entries with ``intent="discharge"``."""
    del sharp_context, patient_id  # used only by decorator for V5 binding
    started_at = time.perf_counter()
    try:
        meds = await fhir_client.get_medication_requests(encounter_id, intent="discharge")
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception(
            "tool.get_discharge_meds.upstream_error",
            encounter_id=encounter_id,
            error=str(exc),
        )
        return ToolResult[list[MedRecord]](
            ok=False,
            error=errors.upstream_error(f"FHIR fixture read failed: {exc}"),
        )
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info(
        "tool.get_discharge_meds.success",
        tool_name=_TOOL_NAME,
        encounter_id=encounter_id,
        count=len(meds),
        duration_ms=duration_ms,
    )
    return ToolResult[list[MedRecord]](ok=True, data=meds)


__all__ = ["tool_get_discharge_meds"]
