"""Prompt Opinion workspace FHIR client.

A per-request HTTP client that queries Prompt Opinion's workspace FHIR server
using the bearer token supplied via the ``X-FHIR-Access-Token`` header. Maps
FHIR R4 ``MedicationStatement`` / ``MedicationRequest`` bundles to our
internal :class:`MedRecord` schema.

Conforms to the :class:`FhirClient` Protocol defined in
``medrec_superpower.fhir.fixture_loader``, so the MCP tools accept it
transparently in place of the local fixture loader.
"""

from __future__ import annotations

import base64
from datetime import date
from types import TracebackType
from typing import cast

import httpx
import structlog
from pydantic import ValidationError
from typing_extensions import Self

from medrec_superpower.schemas import (
    Allergy,
    AllergySeverity,
    ClinicalStatus,
    Condition,
    MedRecord,
    PatientContext,
    Route,
    Sex,
)

logger = structlog.get_logger(__name__)

_TIMEOUT_S = 8.0
_CONNECT_TIMEOUT_S = 3.0


class PoFhirClient:
    """Async FHIR R4 client scoped to a single workspace request.

    Construct one per tool invocation — the bearer token and FHIR base URL
    rotate per call (Prompt Opinion supplies them via headers). The client
    is intentionally cheap to spin up; reuse beyond a single tool call is
    not safe because the bearer token may expire mid-request.
    """

    def __init__(
        self,
        *,
        fhir_server_url: str,
        access_token: str,
        patient_id: str,
        timeout_seconds: float = _TIMEOUT_S,
        connect_timeout_seconds: float = _CONNECT_TIMEOUT_S,
    ) -> None:
        if not fhir_server_url.startswith(("http://", "https://")):
            raise ValueError(f"fhir_server_url must be http(s): got {fhir_server_url!r}")
        if not access_token:
            raise ValueError("access_token must be non-empty")
        if not patient_id:
            raise ValueError("patient_id must be non-empty")
        self._base_url = fhir_server_url.rstrip("/")
        self._access_token = access_token
        self._patient_id = _strip_resource_prefix(patient_id)
        self._timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/fhir+json, application/json",
            },
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_medication_statements(
        self,
        patient_id: str,
        effective_before: date | None = None,
    ) -> list[MedRecord]:
        """Return the patient's pre-admit medications.

        ``MedicationStatement`` is the FHIR resource that semantically maps to
        "what the patient was already taking" — but Synthea (Prompt Opinion's
        default synthetic data) doesn't emit MedicationStatement records.
        When the canonical query returns empty we fall back to all
        non-active ``MedicationRequest`` records for the patient (status in
        ``stopped, completed, on-hold``), which approximates a pre-admit
        list on Synthea data.

        ``effective_before`` is honored client-side. Empty / 4xx / 5xx
        responses degrade to an empty list — the tool layer surfaces that.
        """
        # Canonical path — works for real EHRs.
        params = {"patient": self._patient_id, "_count": "100"}
        bundle = await self._search("MedicationStatement", params)
        records: list[MedRecord] = []
        if bundle:
            records = [r for r in (_statement_to_med_record(e) for e in bundle) if r is not None]
        # Fallback for Synthea — pull historical MedicationRequests.
        if not records:
            fallback_params: dict[str, str] = {
                "patient": self._patient_id,
                "_count": "100",
                # Comma-separated status values: FHIR R4 search "OR" syntax.
                "status": "stopped,completed,on-hold",
            }
            fallback_bundle = await self._search("MedicationRequest", fallback_params)
            if fallback_bundle:
                records = [
                    r for r in (_request_to_med_record(e) for e in fallback_bundle) if r is not None
                ]
        # Final fallback: any MedicationRequest for the patient (the demo path
        # — Synthea patients sometimes have only ``active`` records and no
        # pre-admit history at all; we'd rather show meds than nothing).
        if not records:
            any_bundle = await self._search(
                "MedicationRequest",
                {"patient": self._patient_id, "_count": "100"},
            )
            if any_bundle:
                records = [
                    r for r in (_request_to_med_record(e) for e in any_bundle) if r is not None
                ]
        if effective_before is not None:
            records = [r for r in records if _started_before(r, effective_before)]
        return records

    async def get_medication_requests(
        self,
        encounter_id: str,
        intent: str = "discharge",
    ) -> list[MedRecord]:
        """Return the patient's discharge medications.

        FHIR R4 lets you scope MedicationRequest by ``intent=discharge``, but
        Synthea emits ``intent=order`` exclusively. We progressively widen
        the search:

        1. ``status=active`` (semantically: "what should the patient be
           taking now") — this is what real EHRs return for a recent discharge.
        2. No filter at all — last-resort so the demo has data to reason over.

        ``encounter_id`` is accepted for Protocol compatibility but only used
        when it's a real FHIR reference (not our PO sentinel).
        """
        del intent  # the PO/Synthea path ignores intent; see docstring
        base_params: dict[str, str] = {"patient": self._patient_id, "_count": "100"}
        if encounter_id and "PROMPT_OPINION_DEFAULT" not in encounter_id:
            base_params["encounter"] = _strip_resource_prefix(encounter_id)
        # Primary: active prescriptions.
        active_params = dict(base_params)
        active_params["status"] = "active"
        bundle = await self._search("MedicationRequest", active_params)
        records: list[MedRecord] = []
        if bundle:
            records = [r for r in (_request_to_med_record(e) for e in bundle) if r is not None]
        if not records:
            # Fallback: any MedicationRequest for the patient.
            any_bundle = await self._search("MedicationRequest", base_params)
            if any_bundle:
                records = [
                    r for r in (_request_to_med_record(e) for e in any_bundle) if r is not None
                ]
        return records

    async def get_patient_context(self, patient_id: str) -> PatientContext:
        """Return demographics + conditions + allergies + labs.

        Issues 4 parallel-ish FHIR queries (we serialise for simplicity at
        this scale). Missing fields degrade to ``None`` — never fabricated.
        """
        del patient_id  # the bearer scopes to ``self._patient_id``
        patient = await self._read("Patient", self._patient_id)
        conditions_bundle = await self._search(
            "Condition",
            {"patient": self._patient_id, "_count": "100"},
        )
        allergies_bundle = await self._search(
            "AllergyIntolerance",
            {"patient": self._patient_id, "_count": "50"},
        )
        # Single category=laboratory query — we filter the LOINC codes locally.
        labs_bundle = await self._search(
            "Observation",
            {"patient": self._patient_id, "category": "laboratory", "_count": "200"},
        )

        age, sex = _extract_age_sex(patient)
        conditions = [
            c for c in (_condition_from_fhir(e) for e in (conditions_bundle or [])) if c is not None
        ]
        allergies = [
            a for a in (_allergy_from_fhir(e) for e in (allergies_bundle or [])) if a is not None
        ]
        egfr, ast, alt, inr = _extract_lab_values(labs_bundle or [])

        return PatientContext(
            patient_id=f"Patient/{self._patient_id}",
            age=age,
            sex=sex,
            egfr=egfr,
            lft_ast=ast,
            lft_alt=alt,
            inr=inr,
            allergies=allergies,
            conditions=conditions,
        )

    async def get_discharge_summary_text(self, encounter_id: str) -> str | None:
        """Return the most recent discharge-summary text for this patient.

        Strategy: fetch all DocumentReferences for the patient, prefer those
        whose ``type.text`` mentions "discharge" or LOINC 18842-5; pick the
        latest by ``date``; decode the first base64-encoded ``text/plain``
        attachment.
        """
        del encounter_id  # PO scope is patient-wide; encounter optional
        bundle = await self._search(
            "DocumentReference",
            {"patient": self._patient_id, "_count": "50"},
        )
        if not bundle:
            return None
        candidates = sorted(
            (d for d in bundle if _looks_like_discharge_summary(d)),
            key=_doc_ref_sort_key,
            reverse=True,
        )
        if not candidates:
            return None
        for doc in candidates:
            text = _decode_doc_attachment(doc)
            if text:
                return text
        return None

    async def _read(self, resource_type: str, resource_id: str) -> dict[str, object] | None:
        """GET /<Resource>/<id>; return the resource dict or None."""
        if self._client is None:
            raise RuntimeError("PoFhirClient must be used as an async context manager")
        try:
            response = await self._client.get(f"/{resource_type}/{resource_id}")
        except httpx.HTTPError as exc:
            logger.warning(
                "po_fhir.read.transport_error",
                resource_type=resource_type,
                error=str(exc),
            )
            return None
        if response.status_code >= 400:
            return None
        try:
            payload: object = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _search(
        self, resource_type: str, params: dict[str, str]
    ) -> list[dict[str, object]] | None:
        """Run a FHIR ``GET /<Resource>?...`` search; return the entry list or None."""
        if self._client is None:
            raise RuntimeError("PoFhirClient must be used as an async context manager")
        try:
            response = await self._client.get(f"/{resource_type}", params=params)
        except httpx.HTTPError as exc:
            logger.warning(
                "po_fhir.search.transport_error",
                resource_type=resource_type,
                error=str(exc),
            )
            return None
        if response.status_code >= 400:
            logger.warning(
                "po_fhir.search.upstream_error",
                resource_type=resource_type,
                status=response.status_code,
            )
            return None
        try:
            payload: object = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        entries_raw = payload.get("entry")
        if not isinstance(entries_raw, list):
            return []
        out: list[dict[str, object]] = []
        for entry in entries_raw:
            if isinstance(entry, dict):
                resource = entry.get("resource")
                if isinstance(resource, dict):
                    out.append(cast("dict[str, object]", resource))
        return out


# --------------------------------------------------------------------------- helpers


def _strip_resource_prefix(reference: str) -> str:
    """``Patient/abc`` -> ``abc``; passthrough plain IDs."""
    return reference.rsplit("/", 1)[-1] if "/" in reference else reference


_ROUTE_MAP: dict[str, Route] = {
    "po": "PO",
    "oral": "PO",
    "iv": "IV",
    "intravenous": "IV",
    "im": "IM",
    "intramuscular": "IM",
    "sc": "SC",
    "subcutaneous": "SC",
    "topical": "TOPICAL",
}


def _coerce_route(raw: object) -> Route | None:
    if not isinstance(raw, str):
        return None
    return _ROUTE_MAP.get(raw.strip().lower(), "OTHER")


def _extract_rxcui_and_display(
    medication: dict[str, object] | None,
) -> tuple[str | None, str | None]:
    """Pull ``rxcui`` + human display from a FHIR CodeableConcept."""
    if not isinstance(medication, dict):
        return None, None
    coding_raw = medication.get("coding")
    rxcui: str | None = None
    display: str | None = None
    if isinstance(coding_raw, list):
        for code_obj in coding_raw:
            if not isinstance(code_obj, dict):
                continue
            system = code_obj.get("system")
            if isinstance(system, str) and "rxnorm" in system.lower():
                code = code_obj.get("code")
                if isinstance(code, str) and code:
                    rxcui = code
            disp = code_obj.get("display")
            if isinstance(disp, str) and disp and display is None:
                display = disp
    if display is None:
        text = medication.get("text")
        if isinstance(text, str) and text:
            display = text
    return rxcui, display


def _extract_dose_and_route(
    dosage_raw: object,
) -> tuple[str | None, Route | None, str | None]:
    """Pull dose / route / frequency from a FHIR ``dosage`` array."""
    if not isinstance(dosage_raw, list) or not dosage_raw:
        return None, None, None
    first = dosage_raw[0]
    if not isinstance(first, dict):
        return None, None, None
    dose: str | None = None
    route: Route | None = None
    frequency: str | None = None
    text = first.get("text")
    if isinstance(text, str) and text:
        dose = text
    route_concept = first.get("route")
    if isinstance(route_concept, dict):
        route_coding = route_concept.get("coding")
        if isinstance(route_coding, list):
            for c in route_coding:
                if isinstance(c, dict):
                    code = c.get("code") or c.get("display")
                    coerced = _coerce_route(code)
                    if coerced is not None:
                        route = coerced
                        break
    timing = first.get("timing")
    if isinstance(timing, dict):
        repeat = timing.get("repeat")
        if isinstance(repeat, dict):
            frequency_value = repeat.get("frequency")
            period = repeat.get("period")
            period_unit = repeat.get("periodUnit")
            if frequency_value is not None and period is not None and period_unit:
                frequency = f"{frequency_value}/{period}{period_unit}"
        code = timing.get("code")
        if isinstance(code, dict):
            timing_text = code.get("text")
            if isinstance(timing_text, str) and timing_text and frequency is None:
                frequency = timing_text
    return dose, route, frequency


def _statement_to_med_record(resource: dict[str, object]) -> MedRecord | None:
    """Map a FHIR R4 MedicationStatement to our :class:`MedRecord`."""
    resource_id = resource.get("id")
    if not isinstance(resource_id, str):
        return None
    rxcui, display = _extract_rxcui_and_display(
        _as_codeable_concept(resource.get("medicationCodeableConcept"))
    )
    if rxcui is None or display is None:
        return None
    dose, route, frequency = _extract_dose_and_route(resource.get("dosage"))
    try:
        return MedRecord(
            rxcui=rxcui,
            display=display,
            dose=dose,
            route=route,
            frequency=frequency,
            source_resource_id=f"MedicationStatement/{resource_id}",
            effective_period=None,
        )
    except ValidationError as exc:
        logger.warning("po_fhir.med_statement.invalid", error=str(exc))
        return None


def _request_to_med_record(resource: dict[str, object]) -> MedRecord | None:
    """Map a FHIR R4 MedicationRequest to our :class:`MedRecord`."""
    resource_id = resource.get("id")
    if not isinstance(resource_id, str):
        return None
    rxcui, display = _extract_rxcui_and_display(
        _as_codeable_concept(resource.get("medicationCodeableConcept"))
    )
    if rxcui is None or display is None:
        return None
    dose, route, frequency = _extract_dose_and_route(resource.get("dosageInstruction"))
    try:
        return MedRecord(
            rxcui=rxcui,
            display=display,
            dose=dose,
            route=route,
            frequency=frequency,
            source_resource_id=f"MedicationRequest/{resource_id}",
            effective_period=None,
        )
    except ValidationError as exc:
        logger.warning("po_fhir.med_request.invalid", error=str(exc))
        return None


def _as_codeable_concept(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def _started_before(record: MedRecord, when: date) -> bool:
    if record.effective_period is None:
        return True
    return record.effective_period[0] <= when


# --------------------------------------------------------------------------- patient context


_SEX_MAP: dict[str, Sex] = {
    "male": "M",
    "female": "F",
    "other": "O",
    "unknown": "U",
}


def _extract_age_sex(patient: dict[str, object] | None) -> tuple[int, Sex]:
    """Best-effort age + sex extraction from a FHIR Patient resource."""
    if not isinstance(patient, dict):
        return 0, "U"
    gender_raw = patient.get("gender")
    sex: Sex = _SEX_MAP.get(gender_raw.lower(), "U") if isinstance(gender_raw, str) else "U"
    age = 0
    birth_date = patient.get("birthDate")
    if isinstance(birth_date, str):
        try:
            birth = date.fromisoformat(birth_date)
            today = date.today()
            age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        except ValueError:
            age = 0
    return max(0, min(130, age)), sex


_CONDITION_STATUS_MAP: dict[str, ClinicalStatus] = {
    "active": "active",
    "recurrence": "active",
    "relapse": "active",
    "remission": "remission",
    "inactive": "remission",
    "resolved": "resolved",
}


def _condition_from_fhir(resource: dict[str, object]) -> Condition | None:
    code_obj = resource.get("code")
    if not isinstance(code_obj, dict):
        return None
    coding = code_obj.get("coding")
    code: str | None = None
    display: str | None = None
    if isinstance(coding, list):
        for c in coding:
            if not isinstance(c, dict):
                continue
            if code is None:
                cc = c.get("code")
                if isinstance(cc, str) and cc:
                    code = cc
            if display is None:
                dd = c.get("display")
                if isinstance(dd, str) and dd:
                    display = dd
    if display is None:
        txt = code_obj.get("text")
        if isinstance(txt, str) and txt:
            display = txt
    if not code or not display:
        return None

    clinical_status: ClinicalStatus = "active"
    cs = resource.get("clinicalStatus")
    if isinstance(cs, dict):
        cs_coding = cs.get("coding")
        if isinstance(cs_coding, list):
            for c in cs_coding:
                if isinstance(c, dict):
                    raw = c.get("code")
                    if isinstance(raw, str):
                        mapped = _CONDITION_STATUS_MAP.get(raw.lower())
                        if mapped is not None:
                            clinical_status = mapped
                            break
    try:
        return Condition(code=code, display=display, clinical_status=clinical_status)
    except ValidationError:
        return None


_SEVERITY_MAP: dict[str, AllergySeverity] = {
    "mild": "mild",
    "moderate": "moderate",
    "severe": "severe",
}


def _allergy_from_fhir(resource: dict[str, object]) -> Allergy | None:
    code_obj = resource.get("code")
    substance: str | None = None
    if isinstance(code_obj, dict):
        text = code_obj.get("text")
        if isinstance(text, str) and text:
            substance = text
        if substance is None:
            coding = code_obj.get("coding")
            if isinstance(coding, list):
                for c in coding:
                    if isinstance(c, dict):
                        disp = c.get("display")
                        if isinstance(disp, str) and disp:
                            substance = disp
                            break
    if not substance:
        return None

    severity: AllergySeverity | None = None
    reactions = resource.get("reaction")
    reaction_text: str | None = None
    if isinstance(reactions, list) and reactions:
        first = reactions[0]
        if isinstance(first, dict):
            sev_raw = first.get("severity")
            if isinstance(sev_raw, str):
                severity = _SEVERITY_MAP.get(sev_raw.lower())
            mans = first.get("manifestation")
            if isinstance(mans, list):
                for m in mans:
                    if isinstance(m, dict):
                        txt = m.get("text")
                        if isinstance(txt, str) and txt:
                            reaction_text = txt
                            break
    try:
        return Allergy(substance=substance, reaction=reaction_text, severity=severity)
    except ValidationError:
        return None


# LOINC codes we care about for the safety-critical labs.
_LAB_LOINCS = {
    "egfr": "33914-3",
    "ast": "1920-8",
    "alt": "1742-6",
    "inr": "6301-6",
}


def _extract_lab_values(
    observations: list[dict[str, object]],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return (egfr, ast, alt, inr) — latest non-null per LOINC code."""
    latest: dict[str, tuple[str, float]] = {}
    for obs in observations:
        code_obj = obs.get("code")
        if not isinstance(code_obj, dict):
            continue
        loinc: str | None = None
        coding = code_obj.get("coding")
        if isinstance(coding, list):
            for c in coding:
                if isinstance(c, dict):
                    code = c.get("code")
                    if isinstance(code, str):
                        loinc = code
                        break
        if loinc is None:
            continue
        target = next((k for k, v in _LAB_LOINCS.items() if v == loinc), None)
        if target is None:
            continue
        value_obj = obs.get("valueQuantity")
        if not isinstance(value_obj, dict):
            continue
        v = value_obj.get("value")
        if not isinstance(v, (int, float)):
            continue
        effective = obs.get("effectiveDateTime") or obs.get("issued") or ""
        eff_str = effective if isinstance(effective, str) else ""
        # Prefer the latest by effectiveDateTime when multiple are present.
        if target not in latest or eff_str > latest[target][0]:
            latest[target] = (eff_str, float(v))
    return (
        latest.get("egfr", ("", None))[1],
        latest.get("ast", ("", None))[1],
        latest.get("alt", ("", None))[1],
        latest.get("inr", ("", None))[1],
    )


# --------------------------------------------------------------------------- discharge summary


_DISCHARGE_TYPE_HINTS = ("discharge summary", "discharge")
_DISCHARGE_LOINC = "18842-5"


def _looks_like_discharge_summary(doc: dict[str, object]) -> bool:
    type_obj = doc.get("type")
    if isinstance(type_obj, dict):
        text = type_obj.get("text")
        if isinstance(text, str) and any(hint in text.lower() for hint in _DISCHARGE_TYPE_HINTS):
            return True
        coding = type_obj.get("coding")
        if isinstance(coding, list):
            for c in coding:
                if isinstance(c, dict):
                    code = c.get("code")
                    if isinstance(code, str) and code == _DISCHARGE_LOINC:
                        return True
                    disp = c.get("display")
                    if isinstance(disp, str) and any(
                        hint in disp.lower() for hint in _DISCHARGE_TYPE_HINTS
                    ):
                        return True
    return False


def _doc_ref_sort_key(doc: dict[str, object]) -> str:
    """Sort key: prefer more-recent ``date``; fall back to empty string."""
    d = doc.get("date")
    return d if isinstance(d, str) else ""


def _decode_doc_attachment(doc: dict[str, object]) -> str | None:
    """Decode the first base64-encoded ``text/plain`` attachment, if any."""
    content = doc.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if not isinstance(item, dict):
            continue
        attachment = item.get("attachment")
        if not isinstance(attachment, dict):
            continue
        content_type = attachment.get("contentType")
        if isinstance(content_type, str) and "text" not in content_type:
            continue
        data = attachment.get("data")
        if isinstance(data, str) and data:
            try:
                return base64.b64decode(data).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
        url = attachment.get("url")
        if isinstance(url, str) and url:
            # In the demo path the document is always inline; remote
            # attachments would need an additional fetch.
            return None
    return None


__all__ = ["PoFhirClient"]
