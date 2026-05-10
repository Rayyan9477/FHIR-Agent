# PHASING.md — P0 / P1 / P2 Roadmap

> Three phases with hard scope lines. The line between P0 and P1 is the **submission floor** — anything below is incomplete; anything above is bonus.

---

## At a glance

```
P0 ────────────[ HARD SHIP LINE ]────────── P1 ─────────────────── P2
              ↑ submission must pass here
```

| Phase | What's in | Effort (focused hrs) | Outcome |
|-------|-----------|----------------------|---------|
| P0 | 3 MCP tools + 1 A2A agent + Synthea fixture + 90s demo | 6–10 | Submittable |
| P1 | + 4 MCP tools + Patient Educator agent + HAPI sandbox + 4-card report | +12–16 | Strong submission |
| P2 | + 2 MCP tools + Drug Safety Specialist agent + eval set + capability registry | +20–30 | Marketplace-grade |

---

## P0 — Submit-tonight scope

The minimum that constitutes a valid hackathon submission across both Track 1 (MCP) and Track 2 (A2A).

### MCP server (3 tools)

- `get_pre_admit_meds`
- `get_discharge_meds`
- `check_interaction`

### A2A agent (1)

- **Reconciliation Coordinator** only — handles all reasoning inline
  - System prompt explicitly says "I cannot verify if data is missing"
  - Renders single Markdown report card (not the 4-card layout)

### Data

- **Hard-coded Synthea fixture** for one demo patient (`Patient/P123`)
- No live FHIR server
- Pre-recorded discharge summary text in fixture (skip `parse_discharge_summary`)

### SHARP

- Token signature + patient_id scope validation only
- One signed test token in the fixtures
- Demo workspace launches with this token

### Output

- Single Markdown report
- No daily plan generation (or: hard-coded plan in fixture)

### Tests

- 1 unit test per tool
- 1 e2e test against fixture
- SHARP scope enforcement test

### Demo

- 90-second video, scripted
- One scenario: "Should I still be taking my Metformin?"
- Show: SHARP context populated → tool calls in trace → report rendered

### Marketplace

- MCP server + Coordinator both listed
- Capability tags: `medrec.fhir_data`, `medrec.reconcile`

### What's NOT in P0

- ❌ Patient Educator (Coordinator does inline 6th-grade text)
- ❌ Drug Safety Specialist (Coordinator runs `check_interaction` inline)
- ❌ `parse_discharge_summary` (use fixture)
- ❌ `lookup_rxnorm` (RxCUIs already in fixture)
- ❌ `get_patient_context` (use fixture)
- ❌ `get_drug_education_handout` (skip MedlinePlus link)
- ❌ Visual 4-card report (Markdown is fine)
- ❌ Live HAPI sandbox
- ❌ Eval set
- ❌ Capability registry — hardcode the agent ID

### Definition of done (P0)

```
✅ MCP server starts and responds to tool calls
✅ Coordinator agent published on Prompt Opinion
✅ MCP server registered in Marketplace
✅ Workspace launch produces SHARP context
✅ End-to-end Metformin scenario runs <30s
✅ 90s demo video uploaded
✅ Devpost submission complete with project repo link
```

---

## P1 — Strong submission scope

Builds on P0 with the components that make the demo land hardest.

### Adds (MCP, 4 tools)

- `lookup_rxnorm`
- `get_patient_context`
- `parse_discharge_summary`
- `get_drug_education_handout`

### Adds (A2A, 1 agent)

- **Patient Educator** agent
  - System prompt + Haiku model
  - Uses `get_drug_education_handout` for citations
  - Flesch-Kincaid post-check (regenerate once if > grade 7)

### Data

- Live HAPI FHIR sandbox connection
- Multiple Synthea-generated patients available

### Output

- 4-card report (changes / safety / daily plan / questions)
- Cited narrative for each med change

### Tests

- Full unit coverage on the 4 new tools
- Integration test against HAPI sandbox
- Specialist red-team scenarios (run inline by Coordinator since Specialist is P2)

### Definition of done (P1)

P0 ✅ plus:

```
✅ Educator generates < grade 7 narrative
✅ Each med change cites at least one MedlinePlus URL
✅ HAPI sandbox path works for arbitrary Synthea patient
✅ Discharge summary parsing handles 3+ format variants
✅ 4-card report renders cleanly
```

---

## P2 — Full vision scope

Marketplace-grade product. Adds the differentiation pieces.

### Adds (MCP, 2 tools)

- `get_renal_dosing_guidance`
- `get_pharmacy_fill_history`

### Adds (A2A, 1 agent)

- **Drug Safety Specialist** agent (now distinct from Coordinator)
  - Owns SafetyVerdict
  - Has authority to set `status="hold"` and block daily plan
  - Listed independently on Marketplace (capability `medrec.safety_review`)

### Adds (infra)

- Capability registry — Coordinator looks up specialists by tag, not ID
- 24h LRU caching on `lookup_rxnorm`, handout, renal guidance, parse_discharge_summary
- Eval set with 12 Synthea scenarios + LLM-as-judge + anti-hallucination gate

### Adds (presentation)

- Web demo page (optional, hosted on Vercel)
- API documentation auto-generated from FastAPI
- Marketplace assets (logo, capability description, demo GIF)

### Definition of done (P2)

P1 ✅ plus:

```
✅ Drug Safety Specialist independently published on Marketplace
✅ Coordinator discovers Specialist by capability tag
✅ Eval suite passes (≥90% structural, ≥4.0/5 narrative)
✅ Anti-hallucination gate at 100%
✅ Renal dosing guidance correctly adjusts Metformin restart dose
✅ Pharmacy fill history detects non-adherence in test scenarios
```

---

## Extension points designed in (for post-P2)

- **Multi-language Educator**: add a model parameter, fork the agent for `es-MX`, `zh-CN`, etc.
- **Adherence Risk agent**: wraps `get_pharmacy_fill_history` + new `predict_adherence_risk` tool
- **Cost-aware Substitution tool**: GoodRx + formulary lookup (a P3 conversation)
- **Pharmacogenomics Specialist**: CYP450 interactions, requires CPIC database integration
- **Clinician handoff agent**: produces structured note for PCP, bidirectional PCP/pharmacist comms

None of these block on P2 design — they slot in via the capability registry.

---

## Effort budget breakdown (rough)

| Task | P0 | P1 (incremental) | P2 (incremental) |
|------|----|-----------------:|-----------------:|
| MCP scaffold + 1 tool template | 2h | — | — |
| FHIR client + 2 tools | 1h | — | — |
| `check_interaction` + RxNav | 1h | — | — |
| Coordinator agent config | 1h | — | — |
| SHARP validation | 1h | — | — |
| Synthea fixture + demo data | 1h | — | — |
| Demo video recording | 1h | 1h | 1h |
| Marketplace publish | 1h | — | — |
| 4 more MCP tools | — | 6h | — |
| Patient Educator agent | — | 2h | — |
| HAPI integration | — | 2h | — |
| 4-card report rendering | — | 2h | — |
| Drug Safety Specialist agent | — | — | 4h |
| 2 more MCP tools | — | — | 4h |
| Capability registry | — | — | 2h |
| Caching layer | — | — | 2h |
| Eval set (12 goldens + judge) | — | — | 8h |
| Anti-hallucination harness | — | — | 4h |
| Marketplace polish | — | — | 4h |
| **Total** | **~9h** | **~13h** | **~28h** |

---

## Decision log placeholder

When phasing decisions are revisited, log here:

| Date | Decision | Reason |
|------|----------|--------|
| 2026-05-11 | P0 = 3 tools + Coordinator + fixture | Submission deadline 2026-05-11 EOD |

(Append future decisions to this table.)
