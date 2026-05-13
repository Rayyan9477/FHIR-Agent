"""Tests for ``tool_get_patient_context``."""

from __future__ import annotations

import pytest

from medrec_superpower.fhir import FixtureLoader
from medrec_superpower.schemas import PatientContext, ToolResult
from medrec_superpower.sharp import SharpContext
from medrec_superpower.tools import tool_get_patient_context


@pytest.fixture
def fhir() -> FixtureLoader:
    return FixtureLoader()


class TestGetPatientContext:
    async def test_happy_path_returns_context_with_labs(
        self, sharp_context: SharpContext, fhir: FixtureLoader
    ) -> None:
        result = await tool_get_patient_context(sharp_context=sharp_context, fhir_client=fhir)
        assert isinstance(result, ToolResult)
        assert result.ok is True
        assert result.data is not None
        ctx: PatientContext = result.data
        assert ctx.age == 64
        assert ctx.egfr == 58.0
        # All labs present in the fixture → partial=False.
        assert result.partial is False
        assert result.missing == []
        # Conditions and allergies round-trip.
        assert any(c.code == "E11.9" for c in ctx.conditions)
        assert any("ACE" in a.substance for a in ctx.allergies)

    async def test_missing_labs_mark_partial(
        self,
        sharp_context: SharpContext,
        fhir: FixtureLoader,
        tmp_path: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If FHIR omits a lab, the tool sets partial=True and lists the field."""

        # Patch the fixture loader to omit AST and INR.
        async def shimmed(patient_id: str) -> PatientContext:
            base = await FixtureLoader().get_patient_context(patient_id)
            return base.model_copy(update={"lft_ast": None, "inr": None})

        monkeypatch.setattr(fhir, "get_patient_context", shimmed)
        result = await tool_get_patient_context(sharp_context=sharp_context, fhir_client=fhir)
        assert result.ok is True
        assert result.partial is True
        assert "lft_ast" in result.missing
        assert "inr" in result.missing
