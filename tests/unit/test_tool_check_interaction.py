"""Tests for ``tool_check_interaction`` (Phase 5).

R3 mechanical gate: the tool MUST return ``ok=True, data.check_succeeded=False``
when RxNav fails — never hallucinate, never raise across the MCP boundary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
import respx

from medrec_superpower.drug import InteractionResult, RxNavClient
from medrec_superpower.schemas import ToolResult
from medrec_superpower.sharp import SharpContext
from medrec_superpower.tools import tool_check_interaction

_BASE = "https://rxnav.nlm.nih.gov/REST"


def _interaction_payload() -> dict[str, object]:
    return {
        "fullInteractionTypeGroup": [
            {
                "fullInteractionType": [
                    {
                        "interactionPair": [
                            {
                                "severity": "high",
                                "description": "Warfarin + ibuprofen → bleeding risk.",
                                "interactionConcept": [
                                    {"sourceConceptItem": {"url": "https://labels.fda.gov/x"}}
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }


@pytest_asyncio.fixture
async def rxnav() -> AsyncIterator[RxNavClient]:
    async with RxNavClient() as client:
        yield client


class TestCheckInteractionTool:
    @respx.mock
    async def test_known_interaction_happy_path(
        self, sharp_context: SharpContext, rxnav: RxNavClient
    ) -> None:
        respx.get(_BASE + "/interaction/list.json").mock(
            return_value=httpx.Response(200, json=_interaction_payload())
        )
        result = await tool_check_interaction(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            rxcui_a="11289",
            rxcui_b="5640",
        )
        assert isinstance(result, ToolResult)
        assert result.ok is True
        assert isinstance(result.data, InteractionResult)
        assert result.data.check_succeeded is True
        assert len(result.data.interactions) == 1
        assert result.data.interactions[0].severity == "high"

    @respx.mock
    async def test_503_surfaces_as_check_succeeded_false(
        self, sharp_context: SharpContext, rxnav: RxNavClient
    ) -> None:
        """R3 mechanical: the gate against drug-data hallucination."""
        respx.get(_BASE + "/interaction/list.json").mock(return_value=httpx.Response(503))
        result = await tool_check_interaction(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            rxcui_a="11289",
            rxcui_b="5640",
        )
        # The tool itself succeeded — the data carries the failure signal.
        assert result.ok is True
        assert result.error is None
        assert result.data is not None
        assert result.data.check_succeeded is False
        assert result.data.error_message is not None

    @respx.mock
    async def test_no_interactions(self, sharp_context: SharpContext, rxnav: RxNavClient) -> None:
        respx.get(_BASE + "/interaction/list.json").mock(return_value=httpx.Response(200, json={}))
        result = await tool_check_interaction(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            rxcui_a="860975",
            rxcui_b="200316",
        )
        assert result.ok is True
        assert result.data is not None
        assert result.data.check_succeeded is True
        assert result.data.interactions == []

    async def test_empty_rxcui_rejected_as_bad_request(
        self, sharp_context: SharpContext, rxnav: RxNavClient
    ) -> None:
        result = await tool_check_interaction(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            rxcui_a="",
            rxcui_b="5640",
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "BAD_REQUEST"

    @respx.mock
    async def test_404_deprecated_endpoint(
        self, sharp_context: SharpContext, rxnav: RxNavClient
    ) -> None:
        respx.get(_BASE + "/interaction/list.json").mock(return_value=httpx.Response(404))
        result = await tool_check_interaction(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            rxcui_a="11289",
            rxcui_b="5640",
        )
        assert result.ok is True
        assert result.data is not None
        assert result.data.check_succeeded is False
        assert "deprecated" in (result.data.error_message or "")

    @pytest.mark.parametrize(
        ("rxcui_a", "rxcui_b"),
        [("", ""), ("11289", ""), ("", "5640")],
    )
    async def test_empty_inputs_all_rejected(
        self,
        rxcui_a: str,
        rxcui_b: str,
        sharp_context: SharpContext,
        rxnav: RxNavClient,
    ) -> None:
        result = await tool_check_interaction(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            rxcui_a=rxcui_a,
            rxcui_b=rxcui_b,
        )
        assert result.ok is False
        assert result.error is not None
