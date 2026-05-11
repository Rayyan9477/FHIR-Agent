"""Tests for the Synthea fixture loader (Phase 4)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from medrec_superpower.fhir import FixtureLoader
from medrec_superpower.schemas import MedRecord


@pytest.fixture
def loader() -> FixtureLoader:
    return FixtureLoader()


class TestFixtureLoaderPreAdmit:
    async def test_returns_two_pre_admit_meds_for_p123(self, loader: FixtureLoader) -> None:
        meds = await loader.get_medication_statements("Patient/P123")
        assert len(meds) == 2
        rxcuis = {m.rxcui for m in meds}
        assert rxcuis == {"860975", "314076"}  # Metformin + Lisinopril

    async def test_returns_empty_for_unknown_patient(self, loader: FixtureLoader) -> None:
        meds = await loader.get_medication_statements("Patient/UNKNOWN")
        assert meds == []

    async def test_records_are_valid_med_record_instances(self, loader: FixtureLoader) -> None:
        meds = await loader.get_medication_statements("Patient/P123")
        for m in meds:
            assert isinstance(m, MedRecord)
            assert m.rxcui
            assert m.display

    async def test_effective_before_filters_records(self, loader: FixtureLoader) -> None:
        # Both pre-admit meds start before 2025 — pass at this date
        meds = await loader.get_medication_statements(
            "Patient/P123", effective_before=date(2025, 1, 1)
        )
        assert len(meds) == 2

        # No med started before 2020 — fixture starts 2023+
        meds = await loader.get_medication_statements(
            "Patient/P123", effective_before=date(2020, 1, 1)
        )
        assert meds == []


class TestFixtureLoaderDischarge:
    async def test_returns_two_discharge_meds_for_e456(self, loader: FixtureLoader) -> None:
        meds = await loader.get_medication_requests("Encounter/E456")
        assert len(meds) == 2
        rxcuis = {m.rxcui for m in meds}
        assert rxcuis == {"200316", "617310"}  # Losartan + Atorvastatin

    async def test_returns_empty_for_unknown_encounter(self, loader: FixtureLoader) -> None:
        meds = await loader.get_medication_requests("Encounter/UNKNOWN")
        assert meds == []

    async def test_non_discharge_intent_rejected(self, loader: FixtureLoader) -> None:
        with pytest.raises(ValueError, match="intent='discharge'"):
            await loader.get_medication_requests("Encounter/E456", intent="order")


class TestFixtureLoaderEdgeCases:
    def test_missing_fixture_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            FixtureLoader(tmp_path / "does-not-exist")

    async def test_malformed_fixture_logged_and_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "BAD.json").write_text(
            json.dumps({"pre_admit_medications": ["not-a-record", 42, None]})
        )
        loader = FixtureLoader(tmp_path)
        meds = await loader.get_medication_statements("Patient/BAD")
        assert meds == []  # all entries invalid → skipped

    async def test_non_object_json_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "ARR.json").write_text("[1, 2, 3]")
        loader = FixtureLoader(tmp_path)
        meds = await loader.get_medication_statements("Patient/ARR")
        assert meds == []

    async def test_encounter_scan_skips_unparseable_files(self, tmp_path: Path) -> None:
        (tmp_path / "GOOD.json").write_text(
            json.dumps(
                {
                    "encounter": {"id": "Encounter/E1"},
                    "discharge_medications": [],
                }
            )
        )
        (tmp_path / "BROKEN.json").write_text("not json")
        loader = FixtureLoader(tmp_path)
        meds = await loader.get_medication_requests("Encounter/E1")
        assert meds == []  # discharge_medications is empty list

    async def test_fixture_loader_protocol_satisfied(self, loader: FixtureLoader) -> None:
        # Smoke test: the loader satisfies the FhirClient Protocol structurally.
        from medrec_superpower.fhir import FhirClient

        client: FhirClient = loader
        meds = await client.get_medication_statements("Patient/P123")
        assert len(meds) == 2
