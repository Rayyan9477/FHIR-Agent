"""MCP tool: ``get_drug_education_handout``.

Returns an authoritative MedlinePlus URL for a drug — keyed by RxCUI when
available, otherwise a MedlinePlus search URL based on the display name.

R4 mechanical: every patient-facing drug claim the Coordinator (or Patient
Educator) emits must cite a returned URL. The LLM **never** composes URLs;
it always reads them from this tool's output.
"""

from __future__ import annotations

import structlog

from medrec_superpower import errors
from medrec_superpower.drug import DrugHandout, resolve_drug_handout
from medrec_superpower.schemas import ToolResult
from medrec_superpower.sharp import SharpContext, requires_sharp

logger = structlog.get_logger(__name__)

_TOOL_NAME = "get_drug_education_handout"


@requires_sharp
async def tool_get_drug_education_handout(
    *,
    sharp_context: SharpContext,
    patient_id: str,
    encounter_id: str,
    rxcui: str | None = None,
    display: str = "",
) -> ToolResult[DrugHandout]:
    """MedlinePlus URL for a drug. Provide ``rxcui`` if known; ``display`` always."""
    del sharp_context, patient_id, encounter_id
    if not display or not display.strip():
        return ToolResult[DrugHandout](
            ok=False,
            error=errors.bad_request("display must be a non-empty drug name"),
        )
    handout = resolve_drug_handout(rxcui, display.strip())
    logger.info(
        "tool.get_drug_education_handout.completed",
        tool_name=_TOOL_NAME,
        rxcui=rxcui,
        exact_match=handout.exact_match,
    )
    return ToolResult[DrugHandout](
        ok=True,
        data=handout,
        partial=not handout.exact_match,
        missing=["exact_rxcui_mapping"] if not handout.exact_match else [],
    )


__all__ = ["tool_get_drug_education_handout"]
