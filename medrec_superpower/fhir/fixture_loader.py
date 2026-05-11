"""Synthea fixture loader implementing the :class:`FhirClient` protocol.

P0 uses this against ``tests/fixtures/synthea/*.json``. P1 swaps in a real
HAPI FHIR client that satisfies the same Protocol. Tools are unaware of
which implementation backs the call.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Protocol, cast

import structlog
from pydantic import ValidationError

from medrec_superpower.schemas import MedRecord

logger = structlog.get_logger(__name__)


class FixtureNotFoundError(LookupError):
    """Raised when no fixture matches the requested patient or encounter id."""


class FhirClient(Protocol):
    """Structural type any FHIR-backed data source must satisfy."""

    async def get_medication_statements(  # pragma: no cover - protocol
        self,
        patient_id: str,
        effective_before: date | None = None,
    ) -> list[MedRecord]:
        """Return MedicationStatements valid before ``effective_before``."""
        ...

    async def get_medication_requests(  # pragma: no cover - protocol
        self,
        encounter_id: str,
        intent: str = "discharge",
    ) -> list[MedRecord]:
        """Return MedicationRequests for an encounter filtered by intent."""
        ...


_DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "synthea"


class FixtureLoader:
    """File-backed FHIR client.

    Loads JSON fixtures keyed by the patient id segment (e.g. ``"P123"`` for
    ``Patient/P123``). Mutually exclusive with :class:`HapiClient` (P1).
    """

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._dir = fixture_dir or _DEFAULT_FIXTURE_DIR
        if not self._dir.is_dir():
            raise FileNotFoundError(f"fixture dir not found: {self._dir}")

    def _load(self, patient_id: str) -> dict[str, object]:
        """Read ``<patient_id-stub>.json`` and parse as JSON."""
        stub = patient_id.split("/", 1)[-1]  # "Patient/P123" -> "P123"
        path = self._dir / f"{stub}.json"
        if not path.is_file():
            raise FixtureNotFoundError(f"no fixture for patient_id={patient_id!r}")
        with path.open(encoding="utf-8") as f:
            payload: object = json.load(f)
        if not isinstance(payload, dict):
            raise FixtureNotFoundError(f"fixture {path.name} is not a JSON object")
        return cast("dict[str, object]", payload)

    def _load_by_encounter(self, encounter_id: str) -> dict[str, object]:
        """Linear scan for the fixture whose encounter id matches.

        Acceptable at P0 fixture scale (single patient). When the suite
        grows, index by encounter at load time.
        """
        for path in self._dir.glob("*.json"):
            with path.open(encoding="utf-8") as f:
                try:
                    payload_any: object = json.load(f)
                except json.JSONDecodeError:
                    continue
            if not isinstance(payload_any, dict):
                continue
            encounter = payload_any.get("encounter")
            if isinstance(encounter, dict) and encounter.get("id") == encounter_id:
                return cast("dict[str, object]", payload_any)
        raise FixtureNotFoundError(f"no fixture for encounter_id={encounter_id!r}")

    async def get_medication_statements(
        self,
        patient_id: str,
        effective_before: date | None = None,
    ) -> list[MedRecord]:
        try:
            payload = self._load(patient_id)
        except FixtureNotFoundError:
            logger.info("fixture.medication_statements.miss", patient_id=patient_id)
            return []
        raw_list = payload.get("pre_admit_medications", [])
        if not isinstance(raw_list, list):
            return []
        records = _parse_med_records(raw_list)
        if effective_before is not None:
            records = [r for r in records if _started_before(r, effective_before)]
        return records

    async def get_medication_requests(
        self,
        encounter_id: str,
        intent: str = "discharge",
    ) -> list[MedRecord]:
        # P0: only "discharge" intent is supported; reject other values
        # rather than silently returning everything (defensive).
        if intent != "discharge":
            raise ValueError(f"P0 fixture loader only supports intent='discharge', got {intent!r}")
        try:
            payload = self._load_by_encounter(encounter_id)
        except FixtureNotFoundError:
            logger.info("fixture.medication_requests.miss", encounter_id=encounter_id)
            return []
        raw_list = payload.get("discharge_medications", [])
        if not isinstance(raw_list, list):
            return []
        return _parse_med_records(raw_list)


def _parse_med_records(raw: list[object]) -> list[MedRecord]:
    """Parse each list entry through Pydantic; skip invalid entries with a log."""
    records: list[MedRecord] = []
    for entry in raw:
        if not isinstance(entry, dict):
            logger.warning("fixture.med_record.bad_shape", entry_type=type(entry).__name__)
            continue
        try:
            records.append(MedRecord.model_validate(entry))
        except ValidationError as exc:
            logger.warning("fixture.med_record.invalid", error=str(exc))
    return records


def _started_before(record: MedRecord, when: date) -> bool:
    if record.effective_period is None:
        return True  # no period = always valid
    start = record.effective_period[0]
    return start <= when


__all__ = ["FhirClient", "FixtureLoader", "FixtureNotFoundError"]
