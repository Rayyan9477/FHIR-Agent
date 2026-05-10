# TESTING.md — Testing Strategy

> Three layers: unit (mocked external deps), integration (real sandbox or HAPI), eval (golden scenarios + LLM-as-judge). The anti-hallucination eval is the must-pass gate.

---

## Layout

```
tests/
├── unit/
│   ├── test_tool_get_pre_admit_meds.py
│   ├── test_tool_get_discharge_meds.py
│   ├── test_tool_check_interaction.py
│   ├── test_tool_lookup_rxnorm.py
│   ├── test_tool_get_patient_context.py
│   ├── test_tool_parse_discharge_summary.py
│   ├── test_tool_get_drug_education_handout.py
│   ├── test_tool_get_renal_dosing_guidance.py
│   ├── test_tool_get_pharmacy_fill_history.py
│   ├── test_sharp_validation.py
│   ├── test_pydantic_schemas.py
│   └── test_redact_logging.py
├── integration/
│   ├── conftest.py                        # boots local MCP server + HAPI sandbox client
│   ├── test_coordinator_flow.py           # full E2E with real FHIR
│   ├── test_safety_specialist.py          # known-bad regimens must flag
│   └── test_marketplace_publishing.py     # smoke test against staging marketplace
├── eval/
│   ├── goldens/                           # 12 Synthea-generated scenarios
│   │   ├── 001_metformin_hold_contrast.json
│   │   ├── 002_acei_to_arb_switch.json
│   │   ├── 003_warfarin_nsaid_interaction.json
│   │   └── ...
│   ├── run_eval.py                        # exact-match + LLM-as-judge
│   └── anti_hallucination.py              # provenance check
└── fixtures/
    ├── synthea/                           # synthetic patients
    ├── fhir/                              # recorded responses
    └── sharp/                             # signed test tokens
```

---

## Unit tests

### One file per MCP tool

Pattern: mock the external client (FHIR, RxNav, openFDA, MedlinePlus), exercise the tool, assert on:

- Happy path returns correct schema
- Each error code produces correct envelope
- SHARP scope enforcement (V5)
- PHI redaction in logs

Example skeleton:

```python
# tests/unit/test_tool_check_interaction.py
import pytest
from medrec_superpower.tools.check_interaction import tool

@pytest.mark.asyncio
async def test_clinically_significant_interaction(rxnav_mock):
    rxnav_mock.add_interaction("11289", "5640", severity="major", ...)  # warfarin + ibuprofen
    result = await tool(rxcui_a="11289", rxcui_b="5640", sharp_context=valid_sharp)
    assert result.ok
    assert result.data["severity"] == "major"
    assert "warfarin" in result.data["citations"][0]

@pytest.mark.asyncio
async def test_no_interaction_returns_clear(rxnav_mock):
    rxnav_mock.add_interaction("860975", "200316", severity=None)  # metformin + losartan
    result = await tool(rxcui_a="860975", rxcui_b="200316", sharp_context=valid_sharp)
    assert result.ok
    assert result.data["severity"] is None

@pytest.mark.asyncio
async def test_rxnav_503_returns_check_succeeded_false(rxnav_mock):
    rxnav_mock.set_503()
    result = await tool(rxcui_a="11289", rxcui_b="5640", sharp_context=valid_sharp)
    assert result.ok          # the tool didn't fail
    assert result.data["check_succeeded"] is False
    # CRITICAL: Coordinator-side test asserts the user is told this explicitly
```

### SHARP-specific tests

```python
# tests/unit/test_sharp_validation.py
- test_valid_token_passes
- test_expired_token_returns_401
- test_invalid_signature_returns_401
- test_audience_mismatch_returns_401
- test_cross_patient_kwarg_returns_403         # V5
- test_patient_role_on_clinician_tool_403      # V6
- test_redact_strips_patient_id_from_logs
```

### Schema tests

```python
# tests/unit/test_pydantic_schemas.py
- test_safety_verdict_hold_status_consistent_with_flags
- test_reconciliation_report_hold_means_no_daily_plan   # R5 mechanical
- test_tool_result_ok_xor_error
- test_med_change_event_round_trip_json
```

---

## Integration tests

Boot the actual MCP server (FastMCP under TestClient) and a HAPI FHIR sandbox client. Use a deterministic test patient seeded from Synthea.

```python
# tests/integration/test_coordinator_flow.py
@pytest.mark.asyncio
async def test_metformin_hold_scenario_e2e(test_client, hapi_sandbox, sharp_token):
    # SHARP context: P_TEST, encounter E_TEST
    # Expected: parse_discharge_summary surfaces HOLD, Specialist verdict caution

    response = await test_client.post(
        "/agents/coordinator",
        json={"message": "Should I still be taking my Metformin?"},
        headers={"x-sharp-context": sharp_token},
    )
    report = ReconciliationReport.model_validate(response.json())

    assert report.safety.status == "caution"
    assert any(c.drug_name == "Metformin" and c.action == "HOLD" for c in report.changes)
    assert report.daily_plan is not None       # caution allows daily plan
    assert any("eGFR" in f.message for f in report.safety.flags)
```

### Specialist red-team

```python
# tests/integration/test_safety_specialist.py
- test_warfarin_plus_nsaid_must_flag
- test_acei_plus_arb_must_flag           # duplicate therapy
- test_serotonin_syndrome_combo_must_flag
- test_qt_prolongation_combo_must_flag
- test_renal_failure_metformin_must_be_hold
```

These are known-bad regimens. The Specialist must catch every one.

---

## Eval (golden scenarios)

12 Synthea-generated scenarios exercise the system end-to-end with deterministic expected outputs.

```bash
uv run python tests/eval/run_eval.py
```

### Scoring

| Dimension | Score | How |
|-----------|-------|-----|
| Structural correctness | 0/1 per field | Exact match on `MedChangeEvent.action`, `SafetyVerdict.status`, etc. |
| Narrative quality | 1–5 | LLM-as-judge (Sonnet) reading the narrative section against rubric |
| Citation provenance | 0/1 | Every drug claim traces to a tool call (anti-hallucination) |
| Reading level | 0/1 | Flesch-Kincaid grade ≤ 7 for narrative |

The eval prints a per-scenario breakdown and an aggregate score. Target for P1 submission: ≥ 90% structural, ≥ 4.0/5 narrative, 100% citation provenance.

### Anti-hallucination check (must pass)

```python
# tests/eval/anti_hallucination.py
"""
For each scenario:
  1. Run the system end-to-end with trace logging on.
  2. Extract every drug fact from the final ReconciliationReport
     (interactions, dosing, education).
  3. For each fact, find the MCP tool call in the trace whose
     return value contains that fact.
  4. If any fact has no provenance → FAIL.
"""
```

This is the gate. CI fails if any drug claim originates from the LLM rather than a tool call.

---

## Manual demo dry-run

Before recording the 3-min video:

```
✅ Run all unit tests: uv run pytest tests/unit -v
✅ Run all integration tests: uv run pytest tests/integration -v
✅ Run eval: uv run python tests/eval/run_eval.py
✅ Anti-hallucination passes 100%
✅ Force-fail scenarios from SAFETY.md "Demo failure-mode preparation" checked
✅ Marketplace listing live and reachable
✅ Workspace launch produces SHARP context (visible in network log)
✅ End-to-end demo path < 20s wall clock
```

---

## CI

GitHub Actions (or local pre-commit):

```yaml
# .github/workflows/test.yml (sketch)
- ruff check .
- ruff format --check .
- mypy medrec_superpower
- pytest tests/unit
- pytest tests/integration
- python tests/eval/run_eval.py --fail-on-regression
- python tests/eval/anti_hallucination.py    # gate
```

The anti-hallucination check is a **must-pass** gate, not a soft signal.
