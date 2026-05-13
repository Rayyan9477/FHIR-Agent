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

from medrec_superpower.schemas import Allergy, Condition, MedRecord, PatientContext, Sex

logger = structlog.get_logger(__name__)


class FixtureNotFoundError(LookupError):
    """Raised when no fixture matches the requested patient or encounter id."""


class FhirClient(Protocol):
    """Structural type any FHIR-backed data source must satisfy.

    Implementations: :class:`FixtureLoader` (dev) and
    :class:`medrec_superpower.fhir.PoFhirClient` (Prompt Opinion workspace).
    """

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

    async def get_patient_context(  # pragma: no cover - protocol
        self,
        patient_id: str,
    ) -> PatientContext:
        """Return demographics + conditions + allergies + key labs.

        Conforms to :class:`PatientContext`. Missing fields are ``None``;
        the caller decides whether to mark a tool result ``partial``.
        """
        ...

    async def get_discharge_summary_text(  # pragma: no cover - protocol
        self,
        encounter_id: str,
    ) -> str | None:
        """Return the discharge summary narrative or ``None`` if absent."""
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
        """Read ``<patient_id-stub>.json`` and parse as JSON.

        Defense-in-depth: the patient_id always traces back to a SHARP-signed
        JWT issued by a trusted ``iss``, so the value is already constrained.
        We still refuse any stub that would escape the fixture dir (slashes,
        leading dots) — cheap insurance against a future signer compromise.
        """
        stub = patient_id.split("/", 1)[-1]  # "Patient/P123" -> "P123"
        if not stub or "/" in stub or "\\" in stub or stub.startswith("."):
            raise FixtureNotFoundError(f"unsafe patient_id stub: {stub!r}")
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

    async def get_patient_context(self, patient_id: str) -> PatientContext:
        """Return demographics + conditions + allergies + labs from the fixture."""
        try:
            payload = self._load(patient_id)
        except FixtureNotFoundError:
            # Honest empty context — caller decides whether to mark partial.
            return PatientContext(patient_id=patient_id, age=0, sex="U")
        patient_raw = payload.get("patient", {})
        patient = patient_raw if isinstance(patient_raw, dict) else {}
        obs_raw = payload.get("observations", {})
        obs = obs_raw if isinstance(obs_raw, dict) else {}
        allergies_raw = payload.get("allergies", []) or []
        conditions_raw = payload.get("conditions", []) or []

        allergies: list[Allergy] = []
        if isinstance(allergies_raw, list):
            for a in allergies_raw:
                if not isinstance(a, dict):
                    continue
                try:
                    allergies.append(Allergy.model_validate(a))
                except ValidationError as exc:
                    logger.warning("fixture.allergy.invalid", error=str(exc))

        conditions: list[Condition] = []
        if isinstance(conditions_raw, list):
            for c in conditions_raw:
                if not isinstance(c, dict):
                    continue
                try:
                    conditions.append(Condition.model_validate(c))
                except ValidationError as exc:
                    logger.warning("fixture.condition.invalid", error=str(exc))

        age = patient.get("age", 0) if isinstance(patient.get("age"), int) else 0
        sex_raw = patient.get("sex", "U")
        sex: Sex = sex_raw if sex_raw in ("M", "F", "O", "U") else "U"

        def _num(value: object) -> float | None:
            return float(value) if isinstance(value, (int, float)) else None

        return PatientContext(
            patient_id=patient_id,
            age=age,
            sex=sex,
            egfr=_num(obs.get("egfr_mlmin_1_73m2")),
            lft_ast=_num(obs.get("lft_ast")),
            lft_alt=_num(obs.get("lft_alt")),
            inr=_num(obs.get("inr")),
            allergies=allergies,
            conditions=conditions,
        )

    async def get_discharge_summary_text(self, encounter_id: str) -> str | None:
        """Return the discharge-summary narrative text or ``None``."""
        try:
            payload = self._load_by_encounter(encounter_id)
        except FixtureNotFoundError:
            return None
        summary = payload.get("discharge_summary")
        if not isinstance(summary, dict):
            return None
        text = summary.get("text")
        return text if isinstance(text, str) and text.strip() else None


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
