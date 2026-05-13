"""Tests for ``tool_get_drug_education_handout`` + the resolver."""

from __future__ import annotations

import pytest

from medrec_superpower.drug import resolve_drug_handout
from medrec_superpower.schemas import ToolResult
from medrec_superpower.sharp import SharpContext
from medrec_superpower.tools import tool_get_drug_education_handout


class TestResolveDrugHandout:
    @pytest.mark.parametrize(
        ("rxcui", "display"),
        [
            ("860975", "Metformin"),
            ("314076", "Lisinopril"),
            ("200316", "Losartan"),
            ("617310", "Atorvastatin"),
        ],
    )
    def test_curated_rxcui_returns_exact_match(self, rxcui: str, display: str) -> None:
        handout = resolve_drug_handout(rxcui, display)
        assert handout.exact_match is True
        assert "medlineplus.gov/druginfo/meds/" in str(handout.url)
        assert handout.rxcui == rxcui

    def test_unknown_rxcui_falls_back_to_search(self) -> None:
        handout = resolve_drug_handout("999999", "Mystery Drug")
        assert handout.exact_match is False
        assert "medlineplus.gov/search" in str(handout.url)

    def test_no_rxcui_uses_display(self) -> None:
        handout = resolve_drug_handout(None, "Aspirin")
        assert handout.exact_match is False
        assert "Aspirin" in str(handout.url) or "aspirin" in str(handout.url).lower()


class TestGetDrugEducationHandoutTool:
    async def test_exact_match_handout(self, sharp_context: SharpContext) -> None:
        result = await tool_get_drug_education_handout(
            sharp_context=sharp_context,
            rxcui="860975",
            display="Metformin",
        )
        assert isinstance(result, ToolResult)
        assert result.ok is True
        assert result.data is not None
        assert result.data.exact_match is True
        assert result.partial is False

    async def test_unknown_drug_is_partial(self, sharp_context: SharpContext) -> None:
        result = await tool_get_drug_education_handout(
            sharp_context=sharp_context,
            rxcui=None,
            display="Mystery Drug",
        )
        assert result.ok is True
        assert result.data is not None
        assert result.data.exact_match is False
        assert result.partial is True
        assert "exact_rxcui_mapping" in result.missing

    async def test_empty_display_rejected(self, sharp_context: SharpContext) -> None:
        result = await tool_get_drug_education_handout(
            sharp_context=sharp_context,
            rxcui=None,
            display="",
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "BAD_REQUEST"
