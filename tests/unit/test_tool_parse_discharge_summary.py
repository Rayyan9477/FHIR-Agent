"""Tests for ``tool_parse_discharge_summary`` + the regex extractor."""

from __future__ import annotations

import pytest

from medrec_superpower.fhir import FixtureLoader
from medrec_superpower.schemas import MedChangeAction, ToolResult
from medrec_superpower.sharp import SharpContext
from medrec_superpower.tools import tool_parse_discharge_summary
from medrec_superpower.tools.parse_discharge_summary import parse_changes


class TestParseChangesRegex:
    """Pure-function tests on the regex extractor — no FHIR/SHARP needed."""

    def test_hold_with_restart_condition(self) -> None:
        events = parse_changes("HOLD Metformin 1000 mg BID for 48 hours following IV contrast.")
        assert len(events) == 1
        assert events[0].action == MedChangeAction.HOLD
        assert events[0].drug_name.lower().startswith("metformin")
        reason = events[0].reason
        assert reason is not None
        assert "contrast" in reason.lower()

    def test_stop_and_start_combo(self) -> None:
        text = (
            "STOP Lisinopril 10 mg daily due to ACE-inhibitor cough.\nSTART Losartan 50 mg daily."
        )
        events = parse_changes(text)
        actions = {(e.drug_name.lower(), e.action) for e in events}
        assert any(d.startswith("lisinopril") for d, _ in actions)
        assert any(d.startswith("losartan") for d, _ in actions)
        action_set = {a for _, a in actions}
        assert MedChangeAction.STOP in action_set
        assert MedChangeAction.START in action_set

    def test_restart_classified_as_start(self) -> None:
        events = parse_changes("RESTART Atorvastatin 40 mg at bedtime.")
        assert len(events) == 1
        assert events[0].action == MedChangeAction.START

    def test_discontinue_classified_as_stop(self) -> None:
        events = parse_changes("DISCONTINUE Warfarin pending repeat INR.")
        assert len(events) == 1
        assert events[0].action == MedChangeAction.STOP

    def test_dose_change_classified(self) -> None:
        events = parse_changes("DOSE CHANGE Metformin 500 mg twice daily.")
        assert len(events) == 1
        assert events[0].action == MedChangeAction.DOSE_CHANGE

    def test_empty_text_yields_no_events(self) -> None:
        assert parse_changes("") == []
        assert parse_changes("No medication instructions here.") == []

    def test_dedupes_same_action_drug(self) -> None:
        text = "HOLD Metformin 1000 mg for 48h.\nHOLD Metformin again next week."
        events = parse_changes(text)
        # Same (drug, action) pair → kept once.
        assert len(events) == 1


@pytest.fixture
def fhir() -> FixtureLoader:
    return FixtureLoader()


class TestParseDischargeSummaryTool:
    async def test_happy_path_returns_med_change_events(
        self, sharp_context: SharpContext, fhir: FixtureLoader
    ) -> None:
        result = await tool_parse_discharge_summary(sharp_context=sharp_context, fhir_client=fhir)
        assert isinstance(result, ToolResult)
        assert result.ok is True
        assert result.data is not None
        assert len(result.data) >= 3, result.data  # HOLD + STOP + 2x START
        actions = {e.action for e in result.data}
        assert MedChangeAction.HOLD in actions
        assert MedChangeAction.STOP in actions
        assert MedChangeAction.START in actions
        # Source field always discharge_summary for events from this tool.
        assert all(e.source == "discharge_summary" for e in result.data)
        # Confidence is < 1.0 because regex extraction is heuristic.
        assert all(e.confidence < 1.0 for e in result.data)

    async def test_no_discharge_doc_marks_partial(
        self,
        sharp_context: SharpContext,
        fhir: FixtureLoader,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def no_doc(encounter_id: str) -> None:
            return None

        monkeypatch.setattr(fhir, "get_discharge_summary_text", no_doc)
        result = await tool_parse_discharge_summary(sharp_context=sharp_context, fhir_client=fhir)
        assert result.ok is True
        assert result.data == []
        assert result.partial is True
        assert "discharge_summary_document" in result.missing
