"""MCP tool: ``lookup_rxnorm``.

Resolves a free-text drug name (e.g., ``"Metformin"``) to one or more
RxCUIs via RxNav's ``approximateTerm`` endpoint. The Coordinator uses this
to convert names mentioned by the user into the codes that
:func:`check_interaction` and :func:`get_drug_education_handout` need —
without trusting the LLM to remember codes (R3 mechanical).

Returns a ranked candidate list — the LLM picks by ``score`` / ``term_type``.
Empty list when RxNav fails or the term is unrecognisable; the tool still
returns ``ok=True`` (the failure is in-band, not exception-borne).
"""

from __future__ import annotations

import time

import structlog

from medrec_superpower import errors
from medrec_superpower.drug import RxNavClient, RxNormMatch
from medrec_superpower.schemas import ToolResult
from medrec_superpower.sharp import SharpContext, requires_sharp

logger = structlog.get_logger(__name__)

_TOOL_NAME = "lookup_rxnorm"


@requires_sharp
async def tool_lookup_rxnorm(
    *,
    sharp_context: SharpContext,
    rxnav_client: RxNavClient,
    patient_id: str,
    encounter_id: str,
    term: str,
    max_results: int = 5,
) -> ToolResult[list[RxNormMatch]]:
    """Ranked RxCUI candidates for a drug term."""
    del sharp_context, patient_id, encounter_id  # decorator-bound only
    if not term or not term.strip():
        return ToolResult[list[RxNormMatch]](
            ok=False,
            error=errors.bad_request("term must be non-empty"),
        )
    started_at = time.perf_counter()
    matches = await rxnav_client.lookup_rxnorm(term, max_results=max_results)
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info(
        "tool.lookup_rxnorm.completed",
        tool_name=_TOOL_NAME,
        term=term,
        result_count=len(matches),
        duration_ms=duration_ms,
    )
    return ToolResult[list[RxNormMatch]](
        ok=True,
        data=matches,
        partial=not matches,
        missing=["rxnorm_candidates"] if not matches else [],
    )


__all__ = ["tool_lookup_rxnorm"]
