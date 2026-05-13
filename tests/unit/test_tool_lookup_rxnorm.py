"""Tests for ``tool_lookup_rxnorm``."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
import respx

from medrec_superpower.drug import RxNavClient, RxNormMatch
from medrec_superpower.schemas import ToolResult
from medrec_superpower.sharp import SharpContext
from medrec_superpower.tools import tool_lookup_rxnorm

_BASE = "https://rxnav.nlm.nih.gov/REST"


def _approx_payload(rxcui: str, name: str, score: float = 100.0) -> dict[str, object]:
    return {
        "approximateGroup": {
            "candidate": [{"rxcui": rxcui, "name": name, "score": score, "rxcuiType": "SCD"}]
        }
    }


@pytest_asyncio.fixture
async def rxnav() -> AsyncIterator[RxNavClient]:
    async with RxNavClient() as client:
        yield client


class TestLookupRxnormTool:
    @respx.mock
    async def test_happy_path(self, sharp_context: SharpContext, rxnav: RxNavClient) -> None:
        respx.get(_BASE + "/approximateTerm.json").mock(
            return_value=httpx.Response(200, json=_approx_payload("860975", "Metformin"))
        )
        result = await tool_lookup_rxnorm(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            term="Metformin",
        )
        assert isinstance(result, ToolResult)
        assert result.ok is True
        assert result.data is not None
        assert len(result.data) == 1
        match: RxNormMatch = result.data[0]
        assert match.rxcui == "860975"
        assert "metformin" in match.display.lower()

    @respx.mock
    async def test_empty_results_marked_partial(
        self, sharp_context: SharpContext, rxnav: RxNavClient
    ) -> None:
        respx.get(_BASE + "/approximateTerm.json").mock(
            return_value=httpx.Response(200, json={"approximateGroup": {"candidate": []}})
        )
        result = await tool_lookup_rxnorm(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            term="unobtainium",
        )
        assert result.ok is True
        assert result.data == []
        assert result.partial is True
        assert "rxnorm_candidates" in result.missing

    @respx.mock
    async def test_upstream_5xx_returns_empty_ok(
        self, sharp_context: SharpContext, rxnav: RxNavClient
    ) -> None:
        respx.get(_BASE + "/approximateTerm.json").mock(return_value=httpx.Response(503))
        result = await tool_lookup_rxnorm(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            term="Metformin",
        )
        # R3: never raise; surface as partial empty.
        assert result.ok is True
        assert result.data == []
        assert result.partial is True

    async def test_empty_term_returns_bad_request(
        self, sharp_context: SharpContext, rxnav: RxNavClient
    ) -> None:
        result = await tool_lookup_rxnorm(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            term="",
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "BAD_REQUEST"

    @pytest.mark.parametrize("max_results", [1, 3, 10])
    @respx.mock
    async def test_max_results_forwarded(
        self,
        sharp_context: SharpContext,
        rxnav: RxNavClient,
        max_results: int,
    ) -> None:
        route = respx.get(_BASE + "/approximateTerm.json").mock(
            return_value=httpx.Response(200, json=_approx_payload("860975", "Metformin"))
        )
        await tool_lookup_rxnorm(
            sharp_context=sharp_context,
            rxnav_client=rxnav,
            term="Metformin",
            max_results=max_results,
        )
        sent = route.calls.last.request
        assert sent.url.params["maxEntries"] == str(max_results)
