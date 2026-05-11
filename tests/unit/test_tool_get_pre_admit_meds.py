"""Tests for ``tool_get_pre_admit_meds`` (Phase 5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from medrec_superpower.fhir import FixtureLoader
from medrec_superpower.schemas import MedRecord, ToolResult
from medrec_superpower.sharp import SharpContext, SharpForbidden
from medrec_superpower.tools import tool_get_pre_admit_meds


@pytest.fixture
def fhir() -> FixtureLoader:
    return FixtureLoader()


class TestGetPreAdmitMeds:
    async def test_happy_path_returns_two_meds(
        self, sharp_context: SharpContext, fhir: FixtureLoader
    ) -> None:
        result = await tool_get_pre_admit_meds(sharp_context=sharp_context, fhir_client=fhir)
        assert isinstance(result, ToolResult)
        assert result.ok is True
        assert result.error is None
        assert result.data is not None
        assert len(result.data) == 2
        rxcuis = {m.rxcui for m in result.data}
        assert rxcuis == {"860975", "314076"}
        assert all(isinstance(m, MedRecord) for m in result.data)

    async def test_cross_patient_kwarg_rejected_at_decorator(
        self, sharp_context: SharpContext, fhir: FixtureLoader
    ) -> None:
        """R1 mechanical: a caller cannot reach across SHARP scope."""
        with pytest.raises(SharpForbidden, match="patient_id mismatch"):
            await tool_get_pre_admit_meds(
                sharp_context=sharp_context,
                fhir_client=fhir,
                patient_id="Patient/PATTACKER",  # forced cross-patient
            )

    async def test_unknown_patient_returns_empty_ok(self, fhir: FixtureLoader) -> None:
        """Unknown patient is a legitimate empty result, not an error."""
        ctx = SharpContext(
            patient_id="Patient/UNKNOWN",
            encounter_id="Encounter/UNKNOWN",
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        result = await tool_get_pre_admit_meds(sharp_context=ctx, fhir_client=fhir)
        assert result.ok is True
        assert result.data == []
