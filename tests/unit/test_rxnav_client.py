"""``RxNavClient`` tests — R3 fail-mode (the anti-hallucination gate)."""

from __future__ import annotations

import httpx
import pytest
import respx

from medrec_superpower.drug import InteractionResult, RxNavClient

_BASE = "https://rxnav.nlm.nih.gov/REST"


def _interaction_payload() -> dict[str, object]:
    """A minimal RxNav fullInteractionTypeGroup payload with one finding."""
    return {
        "fullInteractionTypeGroup": [
            {
                "fullInteractionType": [
                    {
                        "interactionPair": [
                            {
                                "severity": "high",
                                "description": "Warfarin and ibuprofen increase bleeding risk.",
                                "interactionConcept": [
                                    {"sourceConceptItem": {"url": "https://labels.fda.gov/example"}}
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }


class TestRxNavClient:
    @respx.mock
    async def test_known_interaction_returns_record(self) -> None:
        respx.get(_BASE + "/interaction/list.json").mock(
            return_value=httpx.Response(200, json=_interaction_payload())
        )
        async with RxNavClient() as rx:
            result = await rx.check_interaction("11289", "5640")
        assert isinstance(result, InteractionResult)
        assert result.check_succeeded is True
        assert len(result.interactions) == 1
        assert result.interactions[0].severity == "high"
        assert "bleeding" in result.interactions[0].description.lower()

    @respx.mock
    async def test_no_interactions_returns_empty(self) -> None:
        respx.get(_BASE + "/interaction/list.json").mock(return_value=httpx.Response(200, json={}))
        async with RxNavClient() as rx:
            result = await rx.check_interaction("860975", "200316")  # metformin + losartan
        assert result.check_succeeded is True
        assert result.interactions == []

    @respx.mock
    async def test_503_after_retries_returns_check_succeeded_false(self) -> None:
        """R3 mechanical: upstream 5xx never bubbles as exception."""
        respx.get(_BASE + "/interaction/list.json").mock(return_value=httpx.Response(503))
        async with RxNavClient(max_attempts=3) as rx:
            result = await rx.check_interaction("11289", "5640")
        assert result.check_succeeded is False
        assert result.error_message is not None
        assert "after 3 attempts" in result.error_message

    @respx.mock
    async def test_timeout_after_retries_returns_check_succeeded_false(self) -> None:
        respx.get(_BASE + "/interaction/list.json").mock(
            side_effect=httpx.TimeoutException("connect timeout")
        )
        async with RxNavClient(max_attempts=2) as rx:
            result = await rx.check_interaction("11289", "5640")
        assert result.check_succeeded is False
        assert "timeout" in (result.error_message or "").lower()

    @respx.mock
    async def test_404_deprecated_endpoint_no_retry(self) -> None:
        route = respx.get(_BASE + "/interaction/list.json").mock(return_value=httpx.Response(404))
        async with RxNavClient(max_attempts=3) as rx:
            result = await rx.check_interaction("11289", "5640")
        assert result.check_succeeded is False
        assert "deprecated" in (result.error_message or "")
        # Critically: only ONE call, no retries on 4xx
        assert route.call_count == 1

    @respx.mock
    async def test_malformed_json_returns_check_succeeded_false(self) -> None:
        respx.get(_BASE + "/interaction/list.json").mock(
            return_value=httpx.Response(
                200, content=b"not json at all", headers={"content-type": "application/json"}
            )
        )
        async with RxNavClient() as rx:
            result = await rx.check_interaction("11289", "5640")
        assert result.check_succeeded is False
        assert "not JSON" in (result.error_message or "") or "malformed" in (
            result.error_message or ""
        )

    @respx.mock
    async def test_payload_not_object_returns_check_succeeded_false(self) -> None:
        respx.get(_BASE + "/interaction/list.json").mock(
            return_value=httpx.Response(200, json=["not", "an", "object"])
        )
        async with RxNavClient() as rx:
            result = await rx.check_interaction("11289", "5640")
        assert result.check_succeeded is False

    @respx.mock
    async def test_empty_rxcuis_rejected(self) -> None:
        async with RxNavClient() as rx:
            result = await rx.check_interaction("", "5640")
        assert result.check_succeeded is False
        assert "non-empty" in (result.error_message or "")

    async def test_client_outside_context_raises(self) -> None:
        rx = RxNavClient()
        with pytest.raises(RuntimeError, match="async context manager"):
            await rx.check_interaction("11289", "5640")

    @respx.mock
    async def test_retry_recovers_on_third_attempt(self) -> None:
        route = respx.get(_BASE + "/interaction/list.json").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(503),
                httpx.Response(200, json=_interaction_payload()),
            ]
        )
        async with RxNavClient(max_attempts=3) as rx:
            result = await rx.check_interaction("11289", "5640")
        assert result.check_succeeded is True
        assert len(result.interactions) == 1
        assert route.call_count == 3

    @respx.mock
    async def test_severity_normalization(self) -> None:
        weird_payload: dict[str, object] = {
            "fullInteractionTypeGroup": [
                {
                    "fullInteractionType": [
                        {
                            "interactionPair": [
                                {
                                    "severity": "N/A",
                                    "description": "Unknown severity case",
                                    "interactionConcept": [],
                                },
                                {
                                    "severity": "moderate",
                                    "description": "Moderate severity case",
                                    "interactionConcept": [],
                                },
                            ]
                        }
                    ]
                }
            ]
        }
        respx.get(_BASE + "/interaction/list.json").mock(
            return_value=httpx.Response(200, json=weird_payload)
        )
        async with RxNavClient() as rx:
            result = await rx.check_interaction("11289", "5640")
        assert result.check_succeeded is True
        assert len(result.interactions) == 2
        assert {r.severity for r in result.interactions} == {"unknown", "moderate"}
