# MCP_SERVER.md — `medrec-superpower` MCP Server

> Python MCP server exposing 9 deterministic tools that any A2A agent can call. The "Superpower" half of the hackathon.

---

## Module layout

```
medrec_superpower/
├── __init__.py
├── server.py                # mcp SDK entrypoint, HTTP+SSE transport
├── tools/                   # one file per tool
│   ├── get_pre_admit_meds.py
│   ├── get_discharge_meds.py
│   ├── check_interaction.py
│   ├── lookup_rxnorm.py
│   ├── get_patient_context.py
│   ├── parse_discharge_summary.py
│   ├── get_drug_education_handout.py
│   ├── get_renal_dosing_guidance.py
│   └── get_pharmacy_fill_history.py
├── fhir/                    # FHIR client + resource adapters
│   ├── client.py
│   └── resources.py
├── drug/                    # external drug API clients
│   ├── rxnav.py
│   ├── openfda.py
│   ├── medlineplus.py
│   └── surescripts.py       # P2 only
├── sharp/                   # SHARP context handling
│   ├── jwt.py               # signature + claims validation
│   ├── decorator.py         # @requires_sharp on every tool
│   └── redact.py            # PHI-redaction logging middleware
├── schemas.py               # Pydantic models (single source of truth)
└── errors.py                # Error envelope + canonical codes
```

---

## Server entrypoint

```python
# medrec_superpower/server.py
from mcp.server.fastmcp import FastMCP
from medrec_superpower.tools import (
    get_pre_admit_meds, get_discharge_meds, check_interaction,
    lookup_rxnorm, get_patient_context, parse_discharge_summary,
    get_drug_education_handout, get_renal_dosing_guidance,
    get_pharmacy_fill_history,
)

mcp = FastMCP("medrec-superpower")

# register tools
mcp.add_tool(get_pre_admit_meds.tool)
mcp.add_tool(get_discharge_meds.tool)
mcp.add_tool(check_interaction.tool)
# ... etc.

if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8765)
```

> Verify the exact `mcp` SDK API against current docs (use Context7) before implementing — the SDK is moving fast.

---

## Tool catalog

### 1. `get_pre_admit_meds` — P0

| | |
|---|---|
| **Phase** | P0 |
| **Purpose** | Return medications the patient was on before admission |
| **Inputs** | (none — `patient_id` from SHARP) |
| **Backed by** | FHIR `MedicationStatement?patient=<id>&effective-time=lt<encounter.start>` |
| **Returns** | `{ ok: true, data: list[MedRecord] }` |
| **Errors** | 403 (cross-patient), 404 (no meds found, returns `data: []` not error), 5xx (FHIR upstream) |
| **Idempotent** | yes |

```python
class MedRecord(BaseModel):
    rxcui: str
    display: str
    dose: str | None
    route: str | None
    frequency: str | None
    source_resource_id: str
    effective_period: tuple[date, date | None]
```

### 2. `get_discharge_meds` — P0

| | |
|---|---|
| **Phase** | P0 |
| **Purpose** | Return medications prescribed at discharge for the current encounter |
| **Inputs** | (none — `encounter_id` from SHARP) |
| **Backed by** | FHIR `MedicationRequest?encounter=<id>&intent=discharge` |
| **Returns** | `{ ok: true, data: list[MedRecord] }` |
| **Errors** | 403 (cross-encounter), 404 (returns `data: []`), 5xx |
| **Idempotent** | yes |

### 3. `check_interaction` — P0

| | |
|---|---|
| **Phase** | P0 |
| **Purpose** | Look up clinically-significant drug-drug interactions between two RxCUIs |
| **Inputs** | `rxcui_a: str`, `rxcui_b: str` |
| **Backed by** | RxNav `/interaction/list.json` + openFDA drug labels for cross-reference |
| **Returns** | `{ ok: true, data: { severity, mechanism, citations[] } }` |
| **Errors** | 503 from RxNav → `{ ok: true, data: { check_succeeded: false } }` (NEVER hallucinate) |
| **Idempotent** | yes (with 24h cache in P2) |

> NB: `check_succeeded: false` is **truthy data**, not an error envelope failure. Coordinator must surface to user.

### 4. `lookup_rxnorm` — P1

| | |
|---|---|
| **Phase** | P1 |
| **Purpose** | Normalize free-text drug name or NDC to RxCUI |
| **Inputs** | `text_or_ndc: str` |
| **Backed by** | RxNav `/approximateTerm.json` |
| **Returns** | `{ ok: true, data: { rxcui, normalized_name, candidates: [...] } }` |
| **Errors** | If similarity < 0.95 across candidates → return all, mark `normalized: false` |
| **Idempotent** | yes |

### 5. `get_patient_context` — P1

| | |
|---|---|
| **Phase** | P1 |
| **Purpose** | Demographics, allergies, conditions, recent labs (eGFR / LFT / INR) |
| **Inputs** | (none — from SHARP) |
| **Backed by** | FHIR `Patient`, `AllergyIntolerance`, `Condition`, `Observation?code=<eGFR/LFT/INR codes>` |
| **Returns** | `{ ok: true, data: PatientContext }` |
| **Errors** | partial: true if any sub-resource fails |
| **Idempotent** | yes |

```python
class PatientContext(BaseModel):
    patient_id: str
    age: int
    sex: Literal["M", "F", "O", "U"]
    eGFR: float | None              # mL/min/1.73m²
    LFT_AST: float | None
    LFT_ALT: float | None
    INR: float | None
    allergies: list[Allergy]
    conditions: list[Condition]
    pregnancy_status: Literal["none", "pregnant", "lactating", "unknown"] | None
```

### 6. `parse_discharge_summary` — P1

| | |
|---|---|
| **Phase** | P1 |
| **Purpose** | Extract structured medication-change events from a free-text discharge summary |
| **Inputs** | `doc_ref_id: str` (FHIR `DocumentReference` ID) |
| **Backed by** | FHIR `DocumentReference` → text → Claude Haiku → Pydantic-validated output |
| **Returns** | `{ ok: true, data: list[MedChangeEvent] }` |
| **Errors** | Pydantic validation failure → retry once with stricter prompt → return raw text + `partial: true` |
| **Idempotent** | yes (with content-hash cache) |

> The ONLY tool that uses an LLM internally. Output is schema-validated; on validation failure, the system surfaces partial data rather than hallucinating.

### 7. `get_drug_education_handout` — P1

| | |
|---|---|
| **Phase** | P1 |
| **Purpose** | Patient-facing drug information from MedlinePlus |
| **Inputs** | `rxcui: str` |
| **Backed by** | NLM MedlinePlus Connect (FHIR-style `mainSearchCriteria.v.cs=2.16.840.1.113883.6.88` for RxNorm) |
| **Returns** | `{ ok: true, data: { url, title, summary, last_updated } }` |
| **Errors** | 404 → `{ ok: true, data: { available: false } }` |
| **Idempotent** | yes (with 24h cache) |

### 8. `get_renal_dosing_guidance` — P2

| | |
|---|---|
| **Phase** | P2 |
| **Purpose** | Renal-adjusted dosing guidance for a drug given current eGFR |
| **Inputs** | `rxcui: str`, `egfr: float` |
| **Backed by** | openFDA structured drug labels (Section 8 — Use in Specific Populations) + KDIGO references |
| **Returns** | `{ ok: true, data: { adjustment, source, dose_modifier, recheck_recommendation } }` |
| **Errors** | If openFDA label has no renal section → `{ ok: true, data: { available: false } }` |
| **Idempotent** | yes |

### 9. `get_pharmacy_fill_history` — P2

| | |
|---|---|
| **Phase** | P2 |
| **Purpose** | Compare prescribed vs actually-filled meds — detect non-adherence |
| **Inputs** | (none — `patient_id` from SHARP) |
| **Backed by** | Surescripts FHIR endpoint OR simulated `MedicationDispense` resources |
| **Returns** | `{ ok: true, data: list[FillRecord] }` |
| **Errors** | Surescripts not connected → `{ ok: true, data: { available: false } }` |
| **Idempotent** | yes |

---

## Cross-cutting concerns

### SHARP enforcement (every tool)

```python
# medrec_superpower/sharp/decorator.py
def requires_sharp(fn):
    @functools.wraps(fn)
    async def wrapper(*args, sharp_context: SharpContext, **kwargs):
        # 1. Validate JWT signature
        # 2. Check expiry
        # 3. Check audience
        # 4. Bind patient_id, encounter_id from SHARP — NOT from kwargs
        if "patient_id" in kwargs and kwargs["patient_id"] != sharp_context.patient_id:
            raise HTTPError(403, "patient_id mismatch with SHARP scope")
        kwargs["patient_id"] = sharp_context.patient_id
        kwargs["encounter_id"] = sharp_context.encounter_id
        return await fn(*args, **kwargs)
    return wrapper
```

### Error envelope (every tool)

```python
class ErrorEnvelope(BaseModel):
    code: Literal[
        "BAD_REQUEST", "UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND",
        "UPSTREAM_ERROR", "TIMEOUT", "INTERNAL"
    ]
    message: str
    retryable: bool

class ToolResult(BaseModel):
    ok: bool
    data: Any | None = None
    error: ErrorEnvelope | None = None
    partial: bool = False
    missing: list[str] = []
```

### PHI redaction (logging)

`structlog` processor that walks the log event dict and replaces values whose keys match a PHI key list (`patient_id`, `mrn`, `name`, `dob`, `address`, `phone`, etc.) with `<redacted>`.

```python
# medrec_superpower/sharp/redact.py
PHI_KEYS = {"patient_id", "mrn", "name", "given", "family", "dob", ...}

def redact_processor(_, __, event_dict):
    for key in list(event_dict.keys()):
        if key in PHI_KEYS:
            event_dict[key] = "<redacted>"
    return event_dict
```

### Retries

`tenacity` with exponential backoff, capped at 3 attempts, only on transient codes (5xx, timeout). All retries are logged.

### Caching (P2)

24h LRU cache for:
- `lookup_rxnorm` (input → output)
- `get_drug_education_handout` (rxcui → handout)
- `get_renal_dosing_guidance` (rxcui+egfr → guidance)
- `parse_discharge_summary` (content hash → events)

NEVER cache `get_pre_admit_meds`, `get_discharge_meds`, `get_patient_context` — patient state changes.

---

## Local development

```bash
# install
uv sync

# run server
uv run python -m medrec_superpower.server
# binds 0.0.0.0:8765, HTTP+SSE

# expose for Prompt Opinion (if not already public)
ngrok http 8765

# manually exercise a tool with a SHARP context fixture
uv run python -m medrec_superpower.dev_repl
```

## Marketplace publishing

Per Prompt Opinion docs (verify in `github.com/prompt-opinion/` samples):

1. Register an account at `app.promptopinion.ai`
2. In Marketplace settings, add MCP server: `<your-public-url>` + capability tags
3. Submit for review — typically same-day for hackathon submissions

The MCP server should declare its **capability tags** in `server.py` so agents on the marketplace can discover it:

```python
mcp.metadata = {
    "capabilities": ["medrec.fhir_data", "medrec.drug_safety", "medrec.patient_education"],
    "domain": "healthcare",
    "phi_handling": "sharp_bound",
}
```
