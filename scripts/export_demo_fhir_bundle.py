#!/usr/bin/env python
"""Emit a FHIR R4 transaction Bundle from ``tests/fixtures/synthea/P123.json``
suitable for upload into Prompt Opinion's "Add patient → FHIR bundle" flow.

The bundle uses ``POST`` semantics (the server assigns IDs). Inter-resource
references inside the bundle use ``urn:uuid:`` placeholders that the FHIR
server resolves at transaction commit time. This is required for Prompt
Opinion's workspace FHIR server, which refuses ``PUT`` ("update-or-create
not supported").

Bundle contents:

* 1 Patient
* 1 Encounter
* 2 Conditions (T2DM, HTN)
* 1 AllergyIntolerance (ACE inhibitor)
* 4 Observations (eGFR, AST, ALT, INR)
* 2 MedicationRequest (status=stopped) — the pre-admit list (Metformin, Lisinopril)
* 2 MedicationRequest (status=active)  — the discharge list (Losartan, Atorvastatin)
* 1 DocumentReference — the discharge summary text

Usage::

    uv run python scripts/export_demo_fhir_bundle.py
    # writes ./demo/p123_fhir_bundle.json — upload that to Prompt Opinion
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_DEFAULT_FIXTURE = Path("tests/fixtures/synthea/P123.json")
_DEFAULT_OUT = Path("demo/p123_fhir_bundle.json")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _post_entry(resource: dict[str, Any], full_url: str) -> dict[str, Any]:
    """Wrap a resource as a POST transaction-bundle entry.

    The ``fullUrl`` is a UUID-namespaced placeholder that other resources in
    the same bundle can use as ``reference`` values; the FHIR server rewrites
    them to the freshly-assigned resource IDs when committing the
    transaction. Resources MUST NOT carry a server ID for POST semantics.
    """
    rtype = resource["resourceType"]
    return {
        "fullUrl": full_url,
        "resource": resource,
        "request": {"method": "POST", "url": rtype},
    }


def _med_request(
    *,
    patient_ref: str,
    encounter_ref: str,
    rxcui: str,
    display: str,
    dose: str | None,
    status: str,
    authored: str,
    intent: str = "order",
) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resourceType": "MedicationRequest",
        "status": status,
        "intent": intent,
        "subject": {"reference": patient_ref},
        "encounter": {"reference": encounter_ref},
        "authoredOn": authored,
        "medicationCodeableConcept": {
            "coding": [
                {
                    "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                    "code": rxcui,
                    "display": display,
                }
            ],
            "text": display,
        },
    }
    if dose:
        resource["dosageInstruction"] = [
            {
                "text": dose,
                "route": {"coding": [{"code": "oral", "display": "Oral"}]},
            }
        ]
    return resource


def build_bundle(fixture: dict[str, Any]) -> dict[str, Any]:
    patient_data = fixture["patient"]
    encounter_data = fixture["encounter"]

    # Stable urn:uuid placeholders for cross-references inside this bundle.
    patient_url = f"urn:uuid:{uuid4()}"
    encounter_url = f"urn:uuid:{uuid4()}"
    patient_ref = patient_url
    encounter_ref = encounter_url

    patient_resource: dict[str, Any] = {
        "resourceType": "Patient",
        "name": [{"text": patient_data["name"]}],
        "birthDate": patient_data["birth_date"],
        "gender": {"M": "male", "F": "female"}.get(patient_data.get("sex", ""), "unknown"),
    }
    encounter_resource: dict[str, Any] = {
        "resourceType": "Encounter",
        "status": "finished",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory",
        },
        "subject": {"reference": patient_ref},
        "period": {
            "start": encounter_data["start"],
            "end": encounter_data["end"],
        },
        "reasonCode": [{"text": encounter_data.get("type", "elective")}],
    }

    entries: list[dict[str, Any]] = [
        _post_entry(patient_resource, patient_url),
        _post_entry(encounter_resource, encounter_url),
    ]

    for condition in fixture.get("conditions", []):
        entries.append(
            _post_entry(
                {
                    "resourceType": "Condition",
                    "subject": {"reference": patient_ref},
                    "clinicalStatus": {
                        "coding": [
                            {
                                "system": (
                                    "http://terminology.hl7.org/CodeSystem/condition-clinical"
                                ),
                                "code": condition["clinical_status"],
                            }
                        ]
                    },
                    "code": {
                        "coding": [
                            {
                                "system": "http://hl7.org/fhir/sid/icd-10-cm",
                                "code": condition["code"],
                                "display": condition["display"],
                            }
                        ],
                        "text": condition["display"],
                    },
                },
                f"urn:uuid:{uuid4()}",
            )
        )

    for allergy in fixture.get("allergies", []):
        entries.append(
            _post_entry(
                {
                    "resourceType": "AllergyIntolerance",
                    "patient": {"reference": patient_ref},
                    "clinicalStatus": {
                        "coding": [
                            {
                                "system": (
                                    "http://terminology.hl7.org/CodeSystem/"
                                    "allergyintolerance-clinical"
                                ),
                                "code": "active",
                            }
                        ]
                    },
                    "code": {"text": allergy["substance"]},
                    "reaction": [{"manifestation": [{"text": allergy.get("reaction", "")}]}],
                },
                f"urn:uuid:{uuid4()}",
            )
        )

    obs = fixture.get("observations", {})
    obs_map = [
        ("egfr_mlmin_1_73m2", "33914-3", "eGFR", "mL/min/1.73m2"),
        ("lft_ast", "1920-8", "AST", "U/L"),
        ("lft_alt", "1742-6", "ALT", "U/L"),
        ("inr", "6301-6", "INR", "ratio"),
    ]
    for key, loinc, label, unit in obs_map:
        if obs.get(key) is None:
            continue
        entries.append(
            _post_entry(
                {
                    "resourceType": "Observation",
                    "status": "final",
                    "category": [
                        {
                            "coding": [
                                {
                                    "system": (
                                        "http://terminology.hl7.org/CodeSystem/observation-category"
                                    ),
                                    "code": "laboratory",
                                }
                            ]
                        }
                    ],
                    "code": {
                        "coding": [
                            {
                                "system": "http://loinc.org",
                                "code": loinc,
                                "display": label,
                            }
                        ],
                        "text": label,
                    },
                    "subject": {"reference": patient_ref},
                    "encounter": {"reference": encounter_ref},
                    "valueQuantity": {
                        "value": obs[key],
                        "unit": unit,
                        "system": "http://unitsofmeasure.org",
                        "code": unit,
                    },
                },
                f"urn:uuid:{uuid4()}",
            )
        )

    # Pre-admit list → MedicationRequest status=stopped (semantically "what
    # was tried before and is no longer active"). PoFhirClient picks these up
    # via the stopped/completed/on-hold fallback.
    pre_authored = encounter_data["start"]
    for med in fixture.get("pre_admit_medications", []):
        entries.append(
            _post_entry(
                _med_request(
                    patient_ref=patient_ref,
                    encounter_ref=encounter_ref,
                    rxcui=med["rxcui"],
                    display=med["display"],
                    dose=f"{med.get('dose', '')} {med.get('frequency', '')}".strip() or None,
                    status="stopped",
                    authored=pre_authored,
                ),
                f"urn:uuid:{uuid4()}",
            )
        )

    # Discharge list → status=active (semantically "currently prescribed").
    disch_authored = encounter_data["end"]
    for med in fixture.get("discharge_medications", []):
        entries.append(
            _post_entry(
                _med_request(
                    patient_ref=patient_ref,
                    encounter_ref=encounter_ref,
                    rxcui=med["rxcui"],
                    display=med["display"],
                    dose=f"{med.get('dose', '')} {med.get('frequency', '')}".strip() or None,
                    status="active",
                    authored=disch_authored,
                ),
                f"urn:uuid:{uuid4()}",
            )
        )

    # Discharge summary as a DocumentReference for context.
    summary = fixture.get("discharge_summary")
    if isinstance(summary, dict) and isinstance(summary.get("text"), str):
        import base64

        encoded = base64.b64encode(summary["text"].encode("utf-8")).decode("ascii")
        entries.append(
            _post_entry(
                {
                    "resourceType": "DocumentReference",
                    "status": "current",
                    "subject": {"reference": patient_ref},
                    "context": {"encounter": [{"reference": encounter_ref}]},
                    "date": summary.get("created", _now()),
                    "type": {"text": "Discharge summary"},
                    "content": [
                        {
                            "attachment": {
                                "contentType": "text/plain",
                                "data": encoded,
                                "title": "Discharge summary",
                            }
                        }
                    ],
                },
                f"urn:uuid:{uuid4()}",
            )
        )

    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "timestamp": _now(),
        "entry": entries,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixture", type=Path, default=_DEFAULT_FIXTURE)
    p.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = p.parse_args()

    if not args.fixture.is_file():
        print(f"fixture not found: {args.fixture}")
        return 1

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    bundle = build_bundle(fixture)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    entry_count = len(bundle["entry"])
    print(f"wrote {args.out}  ({entry_count} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
