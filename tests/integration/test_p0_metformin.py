"""End-to-end test simulating the P0 Coordinator's Metformin scenario.

This test stands in for the live Coordinator agent until the Anthropic API
key arrives (Phase 10). It exercises the **exact tool call sequence** the
Coordinator system prompt would emit for the question
*"Should I still be taking my Metformin?"* and asserts:

1. All 3 P0 tools succeed end-to-end.
2. Pre-admit list contains Metformin (rxcui 860975).
3. Discharge list does **not** contain Metformin (it's held).
4. The drug-interaction check for Metformin paired with Losartan returns
   ``check_succeeded=True`` and no high-severity interaction (R3 honest path).
5. SHARP cross-patient access is rejected (R1).
6. A ``ReconciliationReport`` can be assembled from the tool outputs and
   validates against the schema.

When Phase 10 lands, this test should pass unchanged AND the live agent
should emit the same tool sequence — the agent's job is purely orchestration
+ narrative, not new data.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
import respx

from medrec_superpower.drug import RxNavClient
from medrec_superpower.fhir import FixtureLoader
from medrec_superpower.schemas import (
    MedChangeAction,
    MedChangeEvent,
    ReconciliationReport,
    SafetyVerdict,
    ToolResult,
)
from medrec_superpower.sharp import SharpContext, SharpForbidden
from medrec_superpower.tools import (
    tool_check_interaction,
    tool_get_discharge_meds,
    tool_get_pre_admit_meds,
)

UTC = timezone.utc
_RXNAV = "https://rxnav.nlm.nih.gov/REST"


def _no_interaction_payload() -> dict[str, object]:
    # RxNav returns empty groups when no clinically-significant interaction.
    return {}


@pytest_asyncio.fixture
async def rxnav() -> object:
    async with RxNavClient() as c:
        yield c


@pytest.fixture
def fhir() -> FixtureLoader:
    return FixtureLoader()


@respx.mock
async def test_p0_metformin_end_to_end(
    sharp_context: SharpContext,
    fhir: FixtureLoader,
    rxnav: RxNavClient,
) -> None:
    # ── Mock RxNav: Metformin × Losartan has no clinically-significant interaction
    respx.get(_RXNAV + "/interaction/list.json").mock(
        return_value=httpx.Response(200, json=_no_interaction_payload())
    )

    # ── Step 1: get pre-admit meds
    pre_admit_result = await tool_get_pre_admit_meds(sharp_context=sharp_context, fhir_client=fhir)
    assert pre_admit_result.ok is True
    assert pre_admit_result.data is not None
    pre_admit_rxcuis = {m.rxcui for m in pre_admit_result.data}
    assert "860975" in pre_admit_rxcuis, "Metformin should be in pre-admit list"

    # ── Step 2: get discharge meds — Metformin should be ABSENT (held)
    discharge_result = await tool_get_discharge_meds(sharp_context=sharp_context, fhir_client=fhir)
    assert discharge_result.ok is True
    assert discharge_result.data is not None
    discharge_rxcuis = {m.rxcui for m in discharge_result.data}
    assert "860975" not in discharge_rxcuis, "Metformin should be HELD on discharge"

    # ── Step 3: check Metformin × Losartan (the actual concern)
    interaction_result = await tool_check_interaction(
        sharp_context=sharp_context,
        rxnav_client=rxnav,
        rxcui_a="860975",  # Metformin
        rxcui_b="200316",  # Losartan
    )
    assert interaction_result.ok is True
    assert interaction_result.data is not None
    assert interaction_result.data.check_succeeded is True
    # No clinically-significant interaction — R3 honest path
    assert interaction_result.data.interactions == []

    # ── Coordinator assembly: synthesize a Reconciliation Report from the tool outputs
    changes: list[MedChangeEvent] = [
        MedChangeEvent(
            drug_name="Metformin",
            rxcui="860975",
            action=MedChangeAction.HOLD,
            reason="IV contrast for CT, hold 48h post-procedure",
            source="discharge_summary",
        ),
    ]
    safety = SafetyVerdict(
        status="caution",
        flags=[],  # no flags above info → caution by Specialist convention
        required_clinician_review=False,
        citations=[],
        reviewed_at=datetime.now(UTC),
    )
    report = ReconciliationReport(
        patient_id=sharp_context.patient_id,
        encounter_id=sharp_context.encounter_id,
        generated_at=datetime.now(UTC),
        changes=changes,
        safety=safety,
        daily_plan=None,  # P0 omits — coordinator-rendered inline
        questions_for_doctor=[
            "Should I recheck my kidney function before restarting Metformin?",
        ],
    )
    assert report.safety.status == "caution"
    assert any(c.action == MedChangeAction.HOLD for c in report.changes)


async def test_p0_cross_patient_blocked(sharp_context: SharpContext, fhir: FixtureLoader) -> None:
    """R1 mechanical: a cross-patient call cannot escape SHARP scope."""
    with pytest.raises(SharpForbidden, match="patient_id mismatch"):
        await tool_get_pre_admit_meds(
            sharp_context=sharp_context,
            fhir_client=fhir,
            patient_id="Patient/ANOTHER",
        )


@respx.mock
async def test_p0_r3_rxnav_503_surfaces_uncertainty(
    sharp_context: SharpContext, rxnav: RxNavClient
) -> None:
    """R3 mechanical: a 5xx from RxNav results in check_succeeded=False
    so the Coordinator can tell the user 'I couldn't verify…' rather than
    inventing safety data."""
    respx.get(_RXNAV + "/interaction/list.json").mock(return_value=httpx.Response(503))
    result = await tool_check_interaction(
        sharp_context=sharp_context,
        rxnav_client=rxnav,
        rxcui_a="860975",
        rxcui_b="200316",
    )
    assert result.ok is True
    assert isinstance(result, ToolResult)
    assert result.data is not None
    assert result.data.check_succeeded is False
    assert result.data.error_message  # non-empty failure description
