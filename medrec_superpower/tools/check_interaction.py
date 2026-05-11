"""MCP tool: ``check_interaction``.

Looks up clinically-significant drug-drug interactions between two RxCUIs
via the :class:`RxNavClient`. **R3 mechanical**: when the RxNav lookup
fails (timeout, 5xx, deprecation 404, malformed payload), the tool returns
``ok=True`` with ``data.check_succeeded=False``. The Coordinator must
surface this to the user — never substitute LLM-derived interaction data.
"""

from __future__ import annotations

import time

import structlog

from medrec_superpower import errors
from medrec_superpower.drug import InteractionResult, RxNavClient
from medrec_superpower.schemas import ToolResult
from medrec_superpower.sharp import SharpContext, requires_sharp

logger = structlog.get_logger(__name__)

_TOOL_NAME = "check_interaction"


@requires_sharp
async def tool_check_interaction(
    *,
    sharp_context: SharpContext,
    rxnav_client: RxNavClient,
    patient_id: str,
    encounter_id: str,
    rxcui_a: str,
    rxcui_b: str,
) -> ToolResult[InteractionResult]:
    """Pairwise interaction lookup. SHARP authorises; rxcui args are LLM-controlled."""
    del sharp_context, patient_id, encounter_id  # used only by decorator for V5
    if not rxcui_a or not rxcui_b:
        return ToolResult[InteractionResult](
            ok=False,
            error=errors.bad_request("rxcui_a and rxcui_b must be non-empty"),
        )
    started_at = time.perf_counter()
    result = await rxnav_client.check_interaction(rxcui_a, rxcui_b)
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info(
        "tool.check_interaction.completed",
        tool_name=_TOOL_NAME,
        rxcui_a=rxcui_a,
        rxcui_b=rxcui_b,
        check_succeeded=result.check_succeeded,
        interactions=len(result.interactions),
        duration_ms=duration_ms,
    )
    # R3 mechanical: check_succeeded=False is *data*, not a tool failure.
    # The Coordinator's job is to surface it; ours is to never fake it.
    return ToolResult[InteractionResult](ok=True, data=result)


__all__ = ["tool_check_interaction"]
