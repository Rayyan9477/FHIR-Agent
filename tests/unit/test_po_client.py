"""Unit tests for :class:`PoFhirClient` against a respx-mocked FHIR server."""

from __future__ import annotations

import httpx
import pytest
import respx

from medrec_superpower.fhir import PoFhirClient

_BASE = "https://workspace.example/fhir"
_TOKEN = "po-bearer-token"
_PATIENT = "patient-42"


def _client() -> PoFhirClient:
    return PoFhirClient(fhir_server_url=_BASE, access_token=_TOKEN, patient_id=_PATIENT)


class TestConstruction:
    def test_requires_https(self) -> None:
        with pytest.raises(ValueError, match="http"):
            PoFhirClient(fhir_server_url="ftp://nope", access_token="t", patient_id=_PATIENT)

    def test_requires_access_token(self) -> None:
        with pytest.raises(ValueError, match="access_token"):
            PoFhirClient(fhir_server_url=_BASE, access_token="", patient_id=_PATIENT)

    def test_requires_patient_id(self) -> None:
        with pytest.raises(ValueError, match="patient_id"):
            PoFhirClient(fhir_server_url=_BASE, access_token=_TOKEN, patient_id="")

    def test_strips_resource_prefix(self) -> None:
        c = PoFhirClient(fhir_server_url=_BASE, access_token=_TOKEN, patient_id="Patient/abc")
        assert c._patient_id == "abc"

    async def test_call_outside_context_raises(self) -> None:
        c = _client()
        with pytest.raises(RuntimeError, match="async context manager"):
            await c.get_medication_statements(_PATIENT)


def _statement_bundle(rxcui: str, display: str) -> dict[str, object]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {
                "resource": {
                    "resourceType": "MedicationStatement",
                    "id": "ms-001",
                    "medicationCodeableConcept": {
                        "coding": [
                            {
                                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                "code": rxcui,
                                "display": display,
                            }
                        ]
                    },
                    "dosage": [{"text": "100 MG QD"}],
                }
            }
        ],
    }


def _request_bundle(rxcui: str, display: str) -> dict[str, object]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {
                "resource": {
                    "resourceType": "MedicationRequest",
                    "id": "mr-001",
                    "intent": "discharge",
                    "medicationCodeableConcept": {
                        "coding": [
                            {
                                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                "code": rxcui,
                                "display": display,
                            }
                        ]
                    },
                    "dosageInstruction": [{"text": "10 MG QHS"}],
                }
            }
        ],
    }


class TestGetMedicationStatements:
    @respx.mock
    async def test_happy_path(self) -> None:
        route = respx.get(f"{_BASE}/MedicationStatement").mock(
            return_value=httpx.Response(200, json=_statement_bundle("860975", "Metformin 1000 MG"))
        )
        async with _client() as c:
            records = await c.get_medication_statements(_PATIENT)
        assert route.called
        assert len(records) == 1
        assert records[0].rxcui == "860975"
        assert records[0].source_resource_id == "MedicationStatement/ms-001"
        sent = route.calls.last.request
        assert sent.headers["authorization"] == f"Bearer {_TOKEN}"
        assert sent.url.params["patient"] == _PATIENT

    @respx.mock
    async def test_falls_back_to_medication_request_when_statement_empty(self) -> None:
        """Synthea-style data: MedicationStatement empty, fallback queries MedicationRequest."""
        respx.get(f"{_BASE}/MedicationStatement").mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle"})
        )
        # First MedicationRequest fallback: status=stopped,completed,on-hold
        respx.get(f"{_BASE}/MedicationRequest").mock(
            return_value=httpx.Response(200, json=_request_bundle("314076", "Lisinopril"))
        )
        async with _client() as c:
            records = await c.get_medication_statements(_PATIENT)
        assert len(records) == 1
        assert records[0].rxcui == "314076"

    @respx.mock
    async def test_double_fallback_to_unfiltered_medication_request(self) -> None:
        """MedicationStatement + filtered MedicationRequest empty → unfiltered."""
        respx.get(f"{_BASE}/MedicationStatement").mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle"})
        )
        responses = [
            # First MR call: historical filter — empty
            httpx.Response(200, json={"resourceType": "Bundle"}),
            # Second MR call: any-status — has data
            httpx.Response(200, json=_request_bundle("860975", "Metformin")),
        ]
        respx.get(f"{_BASE}/MedicationRequest").mock(side_effect=responses)
        async with _client() as c:
            records = await c.get_medication_statements(_PATIENT)
        assert len(records) == 1
        assert records[0].rxcui == "860975"

    @respx.mock
    async def test_5xx_returns_empty_list(self) -> None:
        """All three queries 5xx → empty list (R3: never hallucinate)."""
        respx.get(f"{_BASE}/MedicationStatement").mock(return_value=httpx.Response(500, json={}))
        respx.get(f"{_BASE}/MedicationRequest").mock(return_value=httpx.Response(500, json={}))
        async with _client() as c:
            records = await c.get_medication_statements(_PATIENT)
        assert records == []

    @respx.mock
    async def test_transport_error_returns_empty(self) -> None:
        respx.get(f"{_BASE}/MedicationStatement").mock(side_effect=httpx.ConnectError("boom"))
        respx.get(f"{_BASE}/MedicationRequest").mock(side_effect=httpx.ConnectError("boom"))
        async with _client() as c:
            records = await c.get_medication_statements(_PATIENT)
        assert records == []

    @respx.mock
    async def test_missing_rxcui_skipped(self) -> None:
        """Records without RxNorm codes are silently skipped (R3 fail-safe)."""
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "MedicationStatement",
                        "id": "ms-no-code",
                        "medicationCodeableConcept": {"text": "Unknown drug"},
                    }
                }
            ],
        }
        respx.get(f"{_BASE}/MedicationStatement").mock(
            return_value=httpx.Response(200, json=bundle)
        )
        # Fallback MedicationRequest is empty.
        respx.get(f"{_BASE}/MedicationRequest").mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle"})
        )
        async with _client() as c:
            records = await c.get_medication_statements(_PATIENT)
        assert records == []


class TestGetMedicationRequests:
    @respx.mock
    async def test_active_status_is_primary_filter(self) -> None:
        """First-attempt query for discharge meds is ``status=active``."""
        route = respx.get(f"{_BASE}/MedicationRequest").mock(
            return_value=httpx.Response(200, json=_request_bundle("200316", "Losartan"))
        )
        async with _client() as c:
            records = await c.get_medication_requests("Encounter/PROMPT_OPINION_DEFAULT")
        assert route.called
        sent = route.calls.last.request
        assert "encounter" not in sent.url.params
        assert sent.url.params["patient"] == _PATIENT
        assert sent.url.params["status"] == "active"
        # The deprecated intent=discharge filter must NOT appear (Synthea
        # data doesn't use that intent).
        assert "intent" not in sent.url.params
        assert len(records) == 1
        assert records[0].rxcui == "200316"

    @respx.mock
    async def test_falls_back_to_unfiltered_when_active_empty(self) -> None:
        """When status=active returns empty, retry without the status filter."""
        responses = [
            httpx.Response(200, json={"resourceType": "Bundle"}),  # active: empty
            httpx.Response(200, json=_request_bundle("860975", "Metformin")),  # no filter: hit
        ]
        respx.get(f"{_BASE}/MedicationRequest").mock(side_effect=responses)
        async with _client() as c:
            records = await c.get_medication_requests("Encounter/PROMPT_OPINION_DEFAULT")
        assert len(records) == 1

    @respx.mock
    async def test_real_encounter_id_is_passed_through(self) -> None:
        route = respx.get(f"{_BASE}/MedicationRequest").mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle"})
        )
        async with _client() as c:
            await c.get_medication_requests("Encounter/real-enc-1")
        sent = route.calls.last.request
        assert sent.url.params["encounter"] == "real-enc-1"


class TestRouteCoercion:
    @respx.mock
    async def test_oral_route_normalized(self) -> None:
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "MedicationStatement",
                        "id": "ms-route",
                        "medicationCodeableConcept": {
                            "coding": [
                                {
                                    "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                    "code": "1",
                                    "display": "Test drug",
                                }
                            ]
                        },
                        "dosage": [
                            {
                                "text": "1 tab",
                                "route": {"coding": [{"code": "oral"}]},
                            }
                        ],
                    }
                }
            ],
        }
        respx.get(f"{_BASE}/MedicationStatement").mock(
            return_value=httpx.Response(200, json=bundle)
        )
        async with _client() as c:
            records = await c.get_medication_statements(_PATIENT)
        assert records[0].route == "PO"


# --------------------------------------------------------------------------- get_patient_context


def _patient_resource(birth_date: str = "1962-03-22", gender: str = "female") -> dict[str, object]:
    return {"resourceType": "Patient", "id": _PATIENT, "birthDate": birth_date, "gender": gender}


def _condition_bundle(code: str, display: str, status: str = "active") -> dict[str, object]:
    return {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Condition",
                    "clinicalStatus": {"coding": [{"code": status}]},
                    "code": {
                        "coding": [
                            {
                                "system": "http://hl7.org/fhir/sid/icd-10-cm",
                                "code": code,
                                "display": display,
                            }
                        ],
                        "text": display,
                    },
                }
            }
        ],
    }


def _allergy_bundle(substance: str, severity: str = "moderate") -> dict[str, object]:
    return {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "AllergyIntolerance",
                    "code": {"text": substance},
                    "reaction": [
                        {
                            "severity": severity,
                            "manifestation": [{"text": "rash"}],
                        }
                    ],
                }
            }
        ],
    }


def _obs_bundle(loinc: str, value: float, unit: str) -> dict[str, object]:
    return {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Observation",
                    "code": {
                        "coding": [
                            {
                                "system": "http://loinc.org",
                                "code": loinc,
                                "display": "Lab",
                            }
                        ]
                    },
                    "valueQuantity": {"value": value, "unit": unit},
                    "effectiveDateTime": "2026-05-09T00:00:00Z",
                }
            }
        ],
    }


class TestGetPatientContext:
    @respx.mock
    async def test_happy_path_returns_full_context(self) -> None:
        respx.get(f"{_BASE}/Patient/{_PATIENT}").mock(
            return_value=httpx.Response(200, json=_patient_resource())
        )
        respx.get(f"{_BASE}/Condition").mock(
            return_value=httpx.Response(200, json=_condition_bundle("E11.9", "Diabetes T2"))
        )
        respx.get(f"{_BASE}/AllergyIntolerance").mock(
            return_value=httpx.Response(200, json=_allergy_bundle("ACE inhibitors"))
        )
        respx.get(f"{_BASE}/Observation").mock(
            return_value=httpx.Response(200, json=_obs_bundle("33914-3", 58.0, "mL/min/1.73m2"))
        )

        async with _client() as c:
            ctx = await c.get_patient_context(_PATIENT)

        assert ctx.age > 0  # birthDate 1962 → some positive age
        assert ctx.sex == "F"
        assert ctx.egfr == 58.0
        assert any(c.code == "E11.9" for c in ctx.conditions)
        assert any("ACE" in a.substance for a in ctx.allergies)

    @respx.mock
    async def test_unknown_patient_returns_empty_skeleton(self) -> None:
        # All FHIR queries return 404 — context still constructible.
        respx.get(f"{_BASE}/Patient/{_PATIENT}").mock(return_value=httpx.Response(404))
        respx.get(f"{_BASE}/Condition").mock(return_value=httpx.Response(404))
        respx.get(f"{_BASE}/AllergyIntolerance").mock(return_value=httpx.Response(404))
        respx.get(f"{_BASE}/Observation").mock(return_value=httpx.Response(404))

        async with _client() as c:
            ctx = await c.get_patient_context(_PATIENT)

        assert ctx.age == 0
        assert ctx.sex == "U"
        assert ctx.egfr is None
        assert ctx.conditions == []
        assert ctx.allergies == []

    @respx.mock
    async def test_multiple_labs_latest_wins(self) -> None:
        """Two eGFR Observations: the one with later effectiveDateTime wins."""
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Observation",
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "33914-3"}]
                        },
                        "valueQuantity": {"value": 45.0},
                        "effectiveDateTime": "2024-01-01T00:00:00Z",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "33914-3"}]
                        },
                        "valueQuantity": {"value": 58.0},
                        "effectiveDateTime": "2026-05-09T00:00:00Z",
                    }
                },
            ],
        }
        respx.get(f"{_BASE}/Patient/{_PATIENT}").mock(
            return_value=httpx.Response(200, json=_patient_resource())
        )
        respx.get(f"{_BASE}/Condition").mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle"})
        )
        respx.get(f"{_BASE}/AllergyIntolerance").mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle"})
        )
        respx.get(f"{_BASE}/Observation").mock(return_value=httpx.Response(200, json=bundle))

        async with _client() as c:
            ctx = await c.get_patient_context(_PATIENT)
        assert ctx.egfr == 58.0


# --------------------------------------------------------------------------- discharge summary


import base64  # noqa: E402 — local import for the discharge-summary tests


def _doc_ref_bundle(text_content: str) -> dict[str, object]:
    encoded = base64.b64encode(text_content.encode("utf-8")).decode("ascii")
    return {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "DocumentReference",
                    "type": {"text": "Discharge summary"},
                    "date": "2026-05-11T10:45:00Z",
                    "content": [
                        {
                            "attachment": {
                                "contentType": "text/plain",
                                "data": encoded,
                            }
                        }
                    ],
                }
            }
        ],
    }


class TestGetDischargeSummaryText:
    @respx.mock
    async def test_decodes_base64_attachment(self) -> None:
        respx.get(f"{_BASE}/DocumentReference").mock(
            return_value=httpx.Response(
                200, json=_doc_ref_bundle("HOLD Metformin for 48 hours.")
            )
        )
        async with _client() as c:
            text = await c.get_discharge_summary_text("Encounter/PROMPT_OPINION_DEFAULT")
        assert text is not None
        assert "Metformin" in text

    @respx.mock
    async def test_no_documents_returns_none(self) -> None:
        respx.get(f"{_BASE}/DocumentReference").mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle"})
        )
        async with _client() as c:
            text = await c.get_discharge_summary_text("Encounter/anything")
        assert text is None

    @respx.mock
    async def test_skips_non_discharge_documents(self) -> None:
        """A non-discharge DocumentReference must not match."""
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "DocumentReference",
                        "type": {"text": "Progress note"},
                        "date": "2026-05-11T10:45:00Z",
                        "content": [
                            {
                                "attachment": {
                                    "contentType": "text/plain",
                                    "data": base64.b64encode(b"not a discharge summary").decode(),
                                }
                            }
                        ],
                    }
                }
            ],
        }
        respx.get(f"{_BASE}/DocumentReference").mock(
            return_value=httpx.Response(200, json=bundle)
        )
        async with _client() as c:
            text = await c.get_discharge_summary_text("Encounter/anything")
        assert text is None

    @respx.mock
    async def test_picks_latest_by_date(self) -> None:
        """Two discharge summaries — the later ``date`` wins."""
        old = base64.b64encode(b"OLD summary").decode()
        new = base64.b64encode(b"NEW summary").decode()
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "DocumentReference",
                        "type": {"text": "Discharge summary"},
                        "date": "2024-01-01T00:00:00Z",
                        "content": [
                            {"attachment": {"contentType": "text/plain", "data": old}}
                        ],
                    }
                },
                {
                    "resource": {
                        "resourceType": "DocumentReference",
                        "type": {"text": "Discharge summary"},
                        "date": "2026-05-11T00:00:00Z",
                        "content": [
                            {"attachment": {"contentType": "text/plain", "data": new}}
                        ],
                    }
                },
            ],
        }
        respx.get(f"{_BASE}/DocumentReference").mock(
            return_value=httpx.Response(200, json=bundle)
        )
        async with _client() as c:
            text = await c.get_discharge_summary_text("Encounter/anything")
        assert text == "NEW summary"

    @respx.mock
    async def test_non_inline_attachment_returns_none(self) -> None:
        """An attachment with URL but no inline data is not auto-fetched."""
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "DocumentReference",
                        "type": {"text": "Discharge summary"},
                        "date": "2026-05-11T00:00:00Z",
                        "content": [
                            {
                                "attachment": {
                                    "contentType": "text/plain",
                                    "url": "https://example.com/doc.txt",
                                }
                            }
                        ],
                    }
                }
            ],
        }
        respx.get(f"{_BASE}/DocumentReference").mock(
            return_value=httpx.Response(200, json=bundle)
        )
        async with _client() as c:
            text = await c.get_discharge_summary_text("Encounter/anything")
        assert text is None

    @respx.mock
    async def test_non_text_content_type_skipped(self) -> None:
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "DocumentReference",
                        "type": {"text": "Discharge summary"},
                        "date": "2026-05-11T00:00:00Z",
                        "content": [
                            {
                                "attachment": {
                                    "contentType": "application/pdf",
                                    "data": base64.b64encode(b"binary stuff").decode(),
                                }
                            }
                        ],
                    }
                }
            ],
        }
        respx.get(f"{_BASE}/DocumentReference").mock(
            return_value=httpx.Response(200, json=bundle)
        )
        async with _client() as c:
            text = await c.get_discharge_summary_text("Encounter/anything")
        assert text is None

    @respx.mock
    async def test_5xx_returns_none(self) -> None:
        respx.get(f"{_BASE}/DocumentReference").mock(return_value=httpx.Response(500))
        async with _client() as c:
            text = await c.get_discharge_summary_text("Encounter/anything")
        assert text is None

    @respx.mock
    async def test_loinc_type_recognised(self) -> None:
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "DocumentReference",
                        "type": {"coding": [{"code": "18842-5"}]},
                        "date": "2026-05-11T00:00:00Z",
                        "content": [
                            {
                                "attachment": {
                                    "contentType": "text/plain",
                                    "data": base64.b64encode(b"LOINC-matched").decode(),
                                }
                            }
                        ],
                    }
                }
            ],
        }
        respx.get(f"{_BASE}/DocumentReference").mock(
            return_value=httpx.Response(200, json=bundle)
        )
        async with _client() as c:
            text = await c.get_discharge_summary_text("Encounter/anything")
        assert text == "LOINC-matched"
