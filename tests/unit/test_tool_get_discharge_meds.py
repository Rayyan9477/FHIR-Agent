"""Tests for ``tool_get_discharge_meds`` (Phase 5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from medrec_superpower.fhir import FixtureLoader
from medrec_superpower.schemas import MedRecord, ToolResult
from medrec_superpower.sharp import SharpContext, SharpForbidden
from medrec_superpower.tools import tool_get_discharge_meds


@pytest.fixture
def fhir() -> FixtureLoader:
    return FixtureLoader()


class TestGetDischargeMeds:
    async def test_happy_path_returns_two_discharge_meds(
        self, sharp_context: SharpContext, fhir: FixtureLoader
    ) -> None:
        result = await tool_get_discharge_meds(sharp_context=sharp_context, fhir_client=fhir)
        assert isinstance(result, ToolResult)
        assert result.ok is True
        assert result.data is not None
        rxcuis = {m.rxcui for m in result.data}
        assert rxcuis == {"200316", "617310"}  # Losartan + Atorvastatin
        assert all(isinstance(m, MedRecord) for m in result.data)

    async def test_cross_encounter_rejected_at_decorator(
        self, sharp_context: SharpContext, fhir: FixtureLoader
    ) -> None:
        with pytest.raises(SharpForbidden, match="encounter_id mismatch"):
            await tool_get_discharge_meds(
                sharp_context=sharp_context,
                fhir_client=fhir,
                encounter_id="Encounter/EVIL",
            )

    async def test_unknown_encounter_returns_empty_ok(self, fhir: FixtureLoader) -> None:
        ctx = SharpContext(
            patient_id="Patient/P123",
            encounter_id="Encounter/UNKNOWN",
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        result = await tool_get_discharge_meds(sharp_context=ctx, fhir_client=fhir)
        assert result.ok is True
        assert result.data == []
