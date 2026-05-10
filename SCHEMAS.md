# SCHEMAS.md — Data Contracts (Pydantic v2)

> Single source of truth: `medrec_superpower/schemas.py`. Every inter-component message validates against these models. The agent configs on Prompt Opinion mirror the JSON schema generated from these.

---

## Schema map

```
                              ┌────────────────────────┐
   Coordinator inputs ───────▶│ User message (str)     │
                              └────────────────────────┘
                                          │
                                          ▼
   Tool outputs (parallel) ──┬──▶ list[MedRecord]    (get_pre_admit_meds)
                             ├──▶ list[MedRecord]    (get_discharge_meds)
                             ├──▶ PatientContext     (get_patient_context)
                             └──▶ list[MedChangeEvent] (parse_discharge_summary)
                                          │
                                          ▼
   Coordinator builds ──────▶  RegimenProposal
                                          │
                                          ▼ A2A
   Specialist returns ──────▶  SafetyVerdict
                                          │
                                          ▼
   Coordinator merges ─────▶  ReconciliationReport
                                          │
                                          ▼ A2A
   Educator returns ────────▶  PatientNarrative
                                          │
                                          ▼
                               (rendered to user)
```

---

## Versioning

Every top-level inter-agent schema carries `schema_version: Literal["1.0"]`. Schema evolution is **explicit** — bumping the version is a deliberate change, not an accident.

When the version bumps, the receiving agent rejects mismatched payloads at the boundary. We never silently coerce.

---

## Models

### `MedRecord` — single medication

```python
class MedRecord(BaseModel):
    rxcui: str                                          # RxNorm Concept ID
    display: str                                        # human label
    dose: str | None = None                             # "10 MG"
    route: Literal["PO", "IV", "IM", "SC", "TOPICAL", "OTHER"] | None = None
    frequency: str | None = None                        # "BID", "QHS", "PRN"
    source_resource_id: str                             # FHIR resource id
    effective_period: tuple[date, date | None] | None = None
```

### `MedChangeAction` — what happened to a medication

```python
class MedChangeAction(str, Enum):
    START        = "start"
    STOP         = "stop"
    HOLD         = "hold"
    DOSE_CHANGE  = "dose_change"
    ROUTE_CHANGE = "route_change"
    NO_CHANGE    = "no_change"
```

### `MedChangeEvent` — atomic reconciliation finding

```python
class MedChangeEvent(BaseModel):
    drug_name: str                                      # human-readable
    rxcui: str | None = None                            # may be None if lookup_rxnorm couldn't resolve
    action: MedChangeAction
    old_dose: str | None = None
    new_dose: str | None = None
    reason: str | None = None                           # extracted from discharge summary
    effective_date: date | None = None
    source: Literal["discharge_summary", "med_request", "med_statement"]
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
```

### `PatientContext` — patient state for safety reasoning

```python
class Allergy(BaseModel):
    substance: str
    rxcui: str | None = None
    reaction: str | None = None
    severity: Literal["mild", "moderate", "severe"] | None = None

class Condition(BaseModel):
    code: str                                           # ICD-10 or SNOMED
    display: str
    clinical_status: Literal["active", "remission", "resolved"]

class PatientContext(BaseModel):
    patient_id: str                                     # SHARP-validated, never LLM-controlled
    age: int = Field(ge=0, le=130)
    sex: Literal["M", "F", "O", "U"]
    eGFR: float | None = Field(default=None, ge=0, le=200)        # mL/min/1.73m²
    LFT_AST: float | None = None
    LFT_ALT: float | None = None
    INR: float | None = None
    allergies: list[Allergy] = []
    conditions: list[Condition] = []
    pregnancy_status: Literal["none", "pregnant", "lactating", "unknown"] | None = None
    schema_version: Literal["1.0"] = "1.0"
```

### `RegimenProposal` — Coordinator → Specialist

```python
class RegimenProposal(BaseModel):
    patient_id: str                                     # SHARP-validated
    encounter_id: str
    pre_admit: list[MedRecord]
    discharge: list[MedRecord]
    changes: list[MedChangeEvent]
    patient_context: PatientContext
    generated_at: datetime
    schema_version: Literal["1.0"] = "1.0"
```

### `SafetyFlag` — single safety finding

```python
class SafetyFlag(BaseModel):
    severity: Literal["info", "caution", "warn", "hold"]
    category: Literal["interaction", "renal", "hepatic", "allergy", "pregnancy", "duplicate_therapy"]
    drugs_involved: list[str]                           # rxcui list
    message: str
    citation_url: HttpUrl
```

### `SafetyVerdict` — Specialist → Coordinator

```python
class SafetyVerdict(BaseModel):
    status: Literal["clear", "caution", "hold"]
    flags: list[SafetyFlag]
    required_clinician_review: bool
    citations: list[HttpUrl]
    reviewed_at: datetime
    reviewer_agent: str = "drug_safety_specialist"
    schema_version: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def status_consistent_with_flags(self):
        if any(f.severity == "hold" for f in self.flags) and self.status != "hold":
            raise ValueError("status must be 'hold' if any flag has severity 'hold'")
        return self
```

### `DailyPlanEntry` — one row of the daily plan

```python
class DailyPlanEntry(BaseModel):
    days: list[Literal["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]] | Literal["all"]
    time_of_day: Literal["AM", "MIDDAY", "PM", "QHS", "PRN"]
    drug: str
    rxcui: str | None
    dose: str
    notes: str | None = None                            # e.g. "with food"
```

### `NarrativeSection` — one drug's patient-facing copy

```python
class NarrativeSection(BaseModel):
    drug: str
    rxcui: str | None
    action_label: str                                   # "On pause for 2 days", "New prescription", etc.
    text: str                                           # 6th-grade reading level
    citations: list[HttpUrl]
```

### `PatientNarrative` — Educator → Coordinator

```python
class PatientNarrative(BaseModel):
    sections: list[NarrativeSection]
    questions: list[str]                                # 2–4 items
    citations: list[HttpUrl]
    reading_level_grade: float | None = None            # Flesch-Kincaid
    schema_version: Literal["1.0"] = "1.0"
```

### `ReconciliationReport` — final output

```python
class ReconciliationReport(BaseModel):
    patient_id: str                                     # SHARP-validated
    encounter_id: str
    generated_at: datetime
    changes: list[MedChangeEvent]
    safety: SafetyVerdict
    daily_plan: list[DailyPlanEntry] | None             # absent when safety.status == "hold"
    patient_narrative: PatientNarrative | None          # populated by Educator
    questions_for_doctor: list[str]                     # union of Educator + Specialist questions
    schema_version: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def hold_means_no_daily_plan(self):
        if self.safety.status == "hold" and self.daily_plan is not None:
            raise ValueError("daily_plan must be None when safety.status == 'hold'")
        return self
```

### `ToolResult` — every MCP tool returns this envelope

```python
class ErrorEnvelope(BaseModel):
    code: Literal[
        "BAD_REQUEST", "UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND",
        "UPSTREAM_ERROR", "TIMEOUT", "INTERNAL", "SCHEMA_VALIDATION"
    ]
    message: str
    retryable: bool

T = TypeVar("T")

class ToolResult(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ErrorEnvelope | None = None
    partial: bool = False
    missing: list[str] = []

    @model_validator(mode="after")
    def ok_xor_error(self):
        if self.ok and self.error is not None:
            raise ValueError("ok=True but error present")
        if not self.ok and self.error is None:
            raise ValueError("ok=False requires error")
        return self
```

---

## Capability registry

The Coordinator looks up specialists by capability tag, not by hardcoded agent ID. This is the registry mapping capability → expected payload schema:

```python
CAPABILITY_SCHEMAS: dict[str, type[BaseModel]] = {
    "medrec.safety_review":          RegimenProposal,        # input
    "patient_education.translate":   ReconciliationReport,   # input
    "medrec.reconcile":              str,                    # input: free-text user message
}
```

---

## JSON Schema export

For Prompt Opinion agent configs we export each Pydantic model to JSON Schema:

```bash
uv run python -m medrec_superpower.schemas --export agents/schemas/
```

Produces:
- `agents/schemas/regimen_proposal.json`
- `agents/schemas/safety_verdict.json`
- `agents/schemas/reconciliation_report.json`
- `agents/schemas/patient_narrative.json`

These are referenced from the agent YAML configs as the contract for tool-call output schemas.

---

## Schema-evolution policy

| Change type | Action |
|-------------|--------|
| Add optional field | minor — no version bump needed |
| Add required field | bump `schema_version` to "1.1", agents must opt in |
| Remove field | bump `schema_version` to "2.0", coordinated rollout |
| Change field type | bump `schema_version` to "2.0", coordinated rollout |
| Rename field | always major bump, deprecation period |
