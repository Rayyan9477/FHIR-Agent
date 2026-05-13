"""End-to-end HTTP tests against the mounted MCP streamable transport.

Exercises the actual transport that Prompt Opinion's runtime uses. Covers:

* The dev path: ``X-Sharp-Token`` header carrying our RS256-signed JWT, fixture
  loader returns Synthea P123 data.
* The Prompt Opinion path: ``X-Patient-ID`` + ``X-FHIR-Server-URL`` +
  ``X-FHIR-Access-Token`` headers, a respx-mocked FHIR R4 server supplies
  MedicationStatement / MedicationRequest bundles.
* SHARP failure modes — bad token, missing context — must surface as typed
  UNAUTHORIZED envelopes, never 500s.

Earlier we shipped a bug where the FastMCP session manager task group was
never started because the mounted sub-app's lifespan never ran. The unit +
decorator tests passed while the HTTP path crashed with "Task group is not
initialized." These tests are the safety net against that whole class of
regression.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from medrec_superpower.server import build_http_app
from tests.conftest import SharpTokenFactory


@pytest.fixture
def sharp_key_env(
    sharp_public_pem: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    pem_path = tmp_path / "sharp_pub.pem"
    pem_path.write_bytes(sharp_public_pem)
    monkeypatch.setenv("SHARP_PUBLIC_KEY_PEM", str(pem_path))
    return pem_path


@pytest.fixture
def http_client(sharp_key_env: Path) -> Iterator[TestClient]:
    del sharp_key_env
    app = build_http_app()
    # FastMCP's DNS-rebinding defense rejects the default ``testserver`` Host
    # header that ``TestClient`` synthesises. Use a loopback Host instead.
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


_BASE_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _call_tool(
    client: TestClient,
    name: str,
    *,
    arguments: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }
    all_headers = {**_BASE_HEADERS, **(headers or {})}
    response = client.post("/mcp/", json=body, headers=all_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "result" in payload, payload
    structured = payload["result"].get("structuredContent")
    if structured is None:
        text = payload["result"]["content"][0]["text"]
        structured = json.loads(text)
    assert isinstance(structured, dict)
    return structured


def _initialize_response(client: TestClient) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "test", "version": "0.0.0"},
            "capabilities": {},
        },
    }
    response = client.post("/mcp/", json=body, headers=_BASE_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


class TestInitializeCapabilities:
    """The ``initialize`` reply must advertise the Prompt Opinion FHIR extension."""

    def test_advertises_fhir_context_extension(self, http_client: TestClient) -> None:
        reply = _initialize_response(http_client)
        caps = reply["result"]["capabilities"]
        # Mirrored to both names — MCP wire uses ``experimental``, Prompt
        # Opinion docs call the field ``extensions``.
        for key in ("experimental", "extensions"):
            assert key in caps, f"capabilities missing {key!r}: {caps}"
            ext = caps[key]
            assert "ai.promptopinion/fhir-context" in ext, ext
            scopes = ext["ai.promptopinion/fhir-context"]["scopes"]
            scope_names = {s["name"] for s in scopes}
            # The two clinically-required scopes for our tools.
            assert "patient/MedicationStatement.rs" in scope_names
            assert "patient/MedicationRequest.rs" in scope_names


class TestDevPathHeaders:
    """``X-Sharp-Token`` header carries the dev JWT; tools use fixture loader."""

    def test_get_pre_admit_meds_returns_metformin_and_lisinopril(
        self,
        http_client: TestClient,
        sharp_token_factory: SharpTokenFactory,
    ) -> None:
        token = sharp_token_factory()
        result = _call_tool(
            http_client,
            "get_pre_admit_meds",
            headers={"X-Sharp-Token": token},
        )
        assert result["ok"] is True, result
        rxcuis = {m["rxcui"] for m in result["data"]}
        assert rxcuis == {"860975", "314076"}

    def test_get_discharge_meds_returns_losartan_and_atorvastatin(
        self,
        http_client: TestClient,
        sharp_token_factory: SharpTokenFactory,
    ) -> None:
        token = sharp_token_factory()
        result = _call_tool(
            http_client,
            "get_discharge_meds",
            headers={"X-Sharp-Token": token},
        )
        assert result["ok"] is True
        rxcuis = {m["rxcui"] for m in result["data"]}
        assert rxcuis == {"200316", "617310"}

    def test_check_interaction_404_surfaces_honestly(
        self,
        http_client: TestClient,
        sharp_token_factory: SharpTokenFactory,
    ) -> None:
        token = sharp_token_factory()
        result = _call_tool(
            http_client,
            "check_interaction",
            arguments={"rxcui_a": "860975", "rxcui_b": "200316"},
            headers={"X-Sharp-Token": token},
        )
        assert result["ok"] is True
        assert result["data"]["check_succeeded"] is False
        assert result["data"]["error_message"]

    def test_get_patient_context_returns_demographics_and_labs(
        self,
        http_client: TestClient,
        sharp_token_factory: SharpTokenFactory,
    ) -> None:
        token = sharp_token_factory()
        result = _call_tool(
            http_client,
            "get_patient_context",
            headers={"X-Sharp-Token": token},
        )
        assert result["ok"] is True, result
        assert result["data"]["age"] == 64
        assert result["data"]["egfr"] == 58.0
        # No missing labs on the curated fixture → partial=False.
        assert result["partial"] is False

    def test_parse_discharge_summary_extracts_structured_changes(
        self,
        http_client: TestClient,
        sharp_token_factory: SharpTokenFactory,
    ) -> None:
        token = sharp_token_factory()
        result = _call_tool(
            http_client,
            "parse_discharge_summary",
            headers={"X-Sharp-Token": token},
        )
        assert result["ok"] is True, result
        events = result["data"]
        assert isinstance(events, list)
        assert len(events) >= 3
        actions = {e["action"] for e in events}
        assert {"hold", "stop", "start"}.issubset(actions)

    def test_get_drug_education_handout_exact_match(
        self,
        http_client: TestClient,
        sharp_token_factory: SharpTokenFactory,
    ) -> None:
        token = sharp_token_factory()
        result = _call_tool(
            http_client,
            "get_drug_education_handout",
            arguments={"rxcui": "860975", "display": "Metformin"},
            headers={"X-Sharp-Token": token},
        )
        assert result["ok"] is True, result
        assert result["data"]["exact_match"] is True
        assert "medlineplus.gov/druginfo/meds/" in result["data"]["url"]

    @respx.mock
    def test_lookup_rxnorm_returns_candidates(
        self,
        http_client: TestClient,
        sharp_token_factory: SharpTokenFactory,
    ) -> None:
        respx.get("https://rxnav.nlm.nih.gov/REST/approximateTerm.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "approximateGroup": {
                        "candidate": [
                            {
                                "rxcui": "860975",
                                "name": "Metformin",
                                "score": "100",
                                "rxcuiType": "SCD",
                            }
                        ]
                    }
                },
            )
        )
        token = sharp_token_factory()
        result = _call_tool(
            http_client,
            "lookup_rxnorm",
            arguments={"term": "Metformin"},
            headers={"X-Sharp-Token": token},
        )
        assert result["ok"] is True
        assert any(c["rxcui"] == "860975" for c in result["data"])


def _med_statement_bundle(patient_id: str) -> dict[str, object]:
    """A minimal FHIR R4 MedicationStatement bundle for the PO-protocol tests."""
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {
                "resource": {
                    "resourceType": "MedicationStatement",
                    "id": "ms-met-001",
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "medicationCodeableConcept": {
                        "coding": [
                            {
                                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                "code": "860975",
                                "display": "Metformin 1000 MG Oral Tablet",
                            }
                        ]
                    },
                    "dosage": [{"text": "1000 MG twice daily"}],
                }
            },
        ],
    }


def _med_request_bundle(patient_id: str) -> dict[str, object]:
    """A minimal FHIR R4 MedicationRequest bundle."""
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {
                "resource": {
                    "resourceType": "MedicationRequest",
                    "id": "mr-los-003",
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "intent": "discharge",
                    "medicationCodeableConcept": {
                        "coding": [
                            {
                                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                "code": "200316",
                                "display": "Losartan 50 MG Oral Tablet",
                            }
                        ]
                    },
                    "dosageInstruction": [{"text": "50 MG once daily"}],
                }
            },
        ],
    }


_FHIR_BASE = "https://workspace.example/fhir"
_PO_HEADERS = {
    "X-FHIR-Server-URL": _FHIR_BASE,
    "X-FHIR-Access-Token": "po-bearer-token",
    "X-Patient-ID": "barry322",
}


class TestPromptOpinionPath:
    """``X-Patient-ID`` + bearer flow against a respx-mocked workspace FHIR server."""

    @respx.mock
    def test_get_pre_admit_meds_calls_workspace_fhir(self, http_client: TestClient) -> None:
        route = respx.get(f"{_FHIR_BASE}/MedicationStatement").mock(
            return_value=httpx.Response(200, json=_med_statement_bundle("barry322"))
        )
        result = _call_tool(http_client, "get_pre_admit_meds", headers=_PO_HEADERS)
        assert result["ok"] is True, result
        assert route.called
        # Request must have included the bearer + patient param.
        sent = route.calls.last.request
        assert sent.headers["authorization"] == "Bearer po-bearer-token"
        assert sent.url.params["patient"] == "barry322"
        rxcuis = {m["rxcui"] for m in result["data"]}
        assert rxcuis == {"860975"}

    @respx.mock
    def test_get_discharge_meds_calls_workspace_fhir(self, http_client: TestClient) -> None:
        route = respx.get(f"{_FHIR_BASE}/MedicationRequest").mock(
            return_value=httpx.Response(200, json=_med_request_bundle("barry322"))
        )
        result = _call_tool(http_client, "get_discharge_meds", headers=_PO_HEADERS)
        assert result["ok"] is True, result
        assert route.called
        sent = route.calls.last.request
        # PO path uses patient-scoped search filtered by status=active.
        assert sent.url.params["patient"] == "barry322"
        assert sent.url.params["status"] == "active"
        rxcuis = {m["rxcui"] for m in result["data"]}
        assert rxcuis == {"200316"}

    @respx.mock
    def test_workspace_fhir_500_returns_empty_ok(self, http_client: TestClient) -> None:
        """Upstream FHIR failure → empty list with ``ok=True`` (no hallucination).

        Both the canonical MedicationStatement query AND every fallback
        ``MedicationRequest`` query must fail before we degrade to empty.
        """
        respx.get(f"{_FHIR_BASE}/MedicationStatement").mock(
            return_value=httpx.Response(500, json={})
        )
        respx.get(f"{_FHIR_BASE}/MedicationRequest").mock(return_value=httpx.Response(500, json={}))
        result = _call_tool(http_client, "get_pre_admit_meds", headers=_PO_HEADERS)
        assert result["ok"] is True
        assert result["data"] == []


class TestSharpEnforcement:
    """SHARP errors must surface as typed envelopes, never 500s."""

    def test_no_context_returns_unauthorized(self, http_client: TestClient) -> None:
        result = _call_tool(http_client, "get_pre_admit_meds")
        assert result["ok"] is False
        assert result["error"]["code"] == "UNAUTHORIZED"
        assert "no SHARP context" in result["error"]["message"]

    def test_bad_dev_token_returns_unauthorized(self, http_client: TestClient) -> None:
        result = _call_tool(
            http_client,
            "get_pre_admit_meds",
            headers={"X-Sharp-Token": "not.a.jwt"},
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "UNAUTHORIZED"

    def test_audience_mismatch_returns_unauthorized(
        self,
        http_client: TestClient,
        sharp_token_factory: SharpTokenFactory,
    ) -> None:
        token = sharp_token_factory(audience="some-other-platform")
        result = _call_tool(
            http_client,
            "get_pre_admit_meds",
            headers={"X-Sharp-Token": token},
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "UNAUTHORIZED"
