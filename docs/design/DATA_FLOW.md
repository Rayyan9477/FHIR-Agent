# DATA_FLOW.md — End-to-end Data Flow

> What data moves, when, and where it ends up. This is the demo path — the literal Metformin moment from §6 of the design spec.

---

## Demo scenario

**Patient**: P123 (Synthea-generated, post-CT-with-contrast)
**Encounter**: E456 (recent admission for elective imaging)
**User asks**: *"Should I still be taking my Metformin?"*

---

## T0 — Workspace launch

The patient (or simulated SMART launch) opens the Prompt Opinion workspace. The platform populates the SHARP context:

```jsonc
SHARP context @ T0:
{
  "patient_id":   "Patient/P123",
  "fhir_token":   "<opaque session JWT>",
  "encounter_id": "Encounter/E456",
  "user_role":    "patient",
  "issued_at":    "2026-05-11T14:22:00Z",
  "expires_at":   "2026-05-11T15:22:00Z"
}
```

This context rides every subsequent hop. See [SHARP_CONTEXT.md](SHARP_CONTEXT.md).

## T1 — User message

```
User → Coordinator:
  "Should I still be taking my Metformin?"
```

## T2 — Coordinator plans tool calls (parallel)

The Coordinator parses intent, recognizes a med-rec query, and dispatches **four MCP tool calls in parallel**:

```
Coordinator → MCP medrec-superpower (parallel):

  ├─ get_pre_admit_meds(patient_id="Patient/P123")
  │    → [
  │        { rxcui: "860975", display: "Metformin 1000 MG BID", ... },
  │        { rxcui: "314076", display: "Lisinopril 10 MG QD", ... },
  │        ...
  │      ]
  │
  ├─ get_discharge_meds(encounter_id="Encounter/E456")
  │    → [
  │        { rxcui: "200316", display: "Losartan 50 MG QD", ... },
  │        { rxcui: "617310", display: "Atorvastatin 40 MG QHS", ... }
  │        # NB: no Metformin
  │      ]
  │
  ├─ get_patient_context(patient_id="Patient/P123")
  │    → {
  │        age: 64, sex: "F",
  │        eGFR: 58,    # mildly reduced
  │        allergies: [{ substance: "ACE-I", reaction: "cough" }],
  │        conditions: ["E11.9 Type 2 diabetes", "I10 Hypertension"]
  │      }
  │
  └─ parse_discharge_summary(doc_ref_id="DocumentReference/D789")
       → [
           { drug: "Metformin", action: "HOLD",
             reason: "IV contrast for CT, hold 48h post-procedure",
             effective_date: "2026-05-09" },
           { drug: "Lisinopril", action: "STOP",
             reason: "ACE-induced cough — switching to ARB" },
           { drug: "Losartan", action: "START",
             reason: "BP control, ARB substitute for ACE-I" },
           { drug: "Atorvastatin", action: "START",
             reason: "ASCVD prevention, LDL elevated on labs" }
         ]
```

**SHARP enforcement**: each tool call carries `x-sharp-context: <jwt>`. If `patient_id` argument doesn't match SHARP scope → 403.

## T3 — Coordinator builds RegimenProposal

The Coordinator merges the four results into a typed `RegimenProposal`:

```python
RegimenProposal(
    patient_id="Patient/P123",
    encounter_id="Encounter/E456",
    pre_admit=[Metformin, Lisinopril, ...],
    discharge=[Losartan, Atorvastatin],
    changes=[
        MedChangeEvent(drug="Metformin", action=HOLD, reason="..."),
        MedChangeEvent(drug="Lisinopril", action=STOP, reason="..."),
        MedChangeEvent(drug="Losartan", action=START, reason="..."),
        MedChangeEvent(drug="Atorvastatin", action=START, reason="..."),
    ],
    patient_context=<as fetched>,
)
```

## T4 — Hand off to Drug Safety Specialist (P2+)

```
Coordinator → A2A → Drug Safety Specialist
  payload: RegimenProposal
  sharp_context: <propagated automatically by Prompt Opinion>
```

The Specialist runs deterministic checks:

```
Specialist → MCP medrec-superpower (parallel):

  ├─ check_interaction × C(n,2) over discharge regimen
  │    Pairs: (Losartan, Atorvastatin), (Losartan, Metformin*), ...
  │    *Metformin is HELD but checked for restart-time conflicts
  │    → all clinically-significant interactions: NONE
  │
  └─ get_renal_dosing_guidance(rxcui="860975" /metformin/, egfr=58)
       → {
           adjustment: "reduce restart dose to 500mg BID",
           recheck: "eGFR before restart",
           source: "FDA Metformin label (2024 revision)",
           citation_url: "https://labels.fda.gov/..."
         }
```

Allergy cross-check (Specialist reasoning, not a tool):
- patient has ACE-I cough → Losartan is an ARB → cross-reactivity is rare and acceptable

Specialist returns:

```python
SafetyVerdict(
    status="caution",
    flags=[
        SafetyFlag(
            severity="caution",
            category="renal",
            message="Metformin restart dose should be reduced to 500mg BID due to eGFR 58. Recheck eGFR before restarting.",
            citation_url="https://labels.fda.gov/..."
        )
    ],
    required_clinician_review=False,
    citations=["https://labels.fda.gov/...", "https://kdigo.org/..."]
)
```

## T5 — Coordinator merges verdict

```python
ReconciliationReport(
    patient_id="Patient/P123",
    encounter_id="Encounter/E456",
    generated_at="2026-05-11T14:22:43Z",
    changes=[<from T3>],
    safety=<from T4>,
    daily_plan=None,           # filled in T6/T7
    patient_narrative=None,    # filled in T6
    questions_for_doctor=[],   # filled in T6
    schema_version="1.0",
)
```

## T6 — Hand off to Patient Educator (P1+)

```
Coordinator → A2A → Patient Educator
  payload: ReconciliationReport
  sharp_context: <propagated>
```

```
Educator → MCP medrec-superpower (parallel for each new/changed drug):
  ├─ get_drug_education_handout(rxcui="860975" /metformin/)
  │    → { url: "https://medlineplus.gov/druginfo/meds/a696005.html",
  │        summary: "Metformin lowers blood sugar..."}
  ├─ get_drug_education_handout(rxcui="200316" /losartan/)
  └─ get_drug_education_handout(rxcui="617310" /atorvastatin/)
```

Educator generates `PatientNarrative`:

```python
PatientNarrative(
    sections=[
        NarrativeSection(
            drug="Metformin",
            action_label="On pause for 2 days",
            text=(
                "Your Metformin is on pause for 48 hours after your CT scan. "
                "The contrast dye + Metformin can stress your kidneys. "
                "Your kidney lab today (eGFR 58) is mildly low, so it's safer to wait. "
                "Restart Friday morning at a lower dose (500mg twice a day) — your doctor will confirm."
            ),
            citations=["https://medlineplus.gov/druginfo/meds/a696005.html"],
        ),
        NarrativeSection(drug="Lisinopril", ...),
        NarrativeSection(drug="Losartan", ...),
        NarrativeSection(drug="Atorvastatin", ...),
    ],
    questions=[
        "Should we recheck my kidney function before I restart Metformin?",
        "Are there any side effects from Losartan I should watch for?",
    ],
    citations=[<all MedlinePlus URLs>],
)
```

## T7 — Coordinator assembles 4-card report

```
┌─ What changed ──────────────────────────┐  ┌─ Safety verdict (Specialist) ────┐
│ • Metformin: HELD  (CT contrast, 48h)   │  │ Status: CAUTION                  │
│ • Lisinopril → Losartan  (ACE-I cough)  │  │ ⚠ eGFR 58 → reduce Metformin    │
│ • Atorvastatin: NEW  (ASCVD prevention) │  │   restart dose to 500mg BID      │
│                                         │  │ source: FDA label, KDIGO         │
└─────────────────────────────────────────┘  └──────────────────────────────────┘
┌─ Your daily plan ───────────────────────┐  ┌─ Ask your doctor ────────────────┐
│  Mon-Wed AM:  Losartan 50mg             │  │ • "Should we recheck eGFR        │
│  Mon-Wed PM:  Atorvastatin 40mg         │  │    before restarting Metformin?" │
│  Thu+         RESTART Metformin 500mg   │  │ • "Any side effects from         │
│               (recheck kidney first)    │  │    Losartan I should watch for?" │
└─────────────────────────────────────────┘  └──────────────────────────────────┘
```

User sees this inline.

---

## Latency targets

| Phase | Target |
|-------|--------|
| T2 — first paint after user message | < 2s |
| T2 — all tool calls complete | < 5s |
| T4 — Specialist verdict | < 3s (parallel interaction checks) |
| T6 — Educator narrative | < 4s (Haiku, parallelized handouts) |
| **End-to-end** | **< 20s** for full 4-card report |

## Idempotency

The same `(SHARP_context, user_message)` produces the same `ReconciliationReport` (modulo `generated_at`). All tools are read-only; no state mutates anywhere. This makes the demo reproducible.

## What never appears in this flow

- Patient identifier as an LLM-controlled argument (always SHARP-derived)
- Drug interaction asserted by the LLM without a tool call
- PHI in plaintext logs (redacted by middleware)
- Unbounded retry loops
- Free-text "trust me" outputs without a citation
