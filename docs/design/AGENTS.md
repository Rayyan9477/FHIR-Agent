# AGENTS.md — A2A Agents in FHIR-Agent

> Three agents collaborate via Prompt Opinion's A2A protocol. Each has a distinct **authority** and a typed **output schema**. That's what makes this real A2A and not LLM cosplay.

---

## Agent inventory

| Agent | Phase | Model | Authority | Output |
|-------|-------|-------|-----------|--------|
| Reconciliation Coordinator | P0 | Claude Sonnet 4.6 | Owns the workflow & final report | `ReconciliationReport` |
| Drug Safety Specialist | P2 | Claude Sonnet 4.6 | Can **veto** a regimen | `SafetyVerdict` |
| Patient Educator | P1 | Claude Haiku 4.5 | Owns patient-facing narrative | `PatientNarrative` |

All three are configured on Prompt Opinion (no-code) but reference the same MCP server + shared Pydantic schemas (see [SCHEMAS.md](SCHEMAS.md)).

---

## 1. Reconciliation Coordinator

### Role
Orchestrates the entire reconciliation. The user-facing agent — patient or clinician chats with this one. Calls MCP tools, hands off to specialists, assembles the final report.

### System prompt (sketch)

> You are a medication-reconciliation assistant for post-discharge patients. For each medication change between pre-admit and discharge lists, identify the type of change (start / stop / hold / dose_change / route_change), the clinical reason, and any safety flags.
>
> Use the `medrec-superpower` MCP tools to gather data — never invent drug information. For safety reasoning beyond pairwise interactions, hand the proposed regimen to the `Drug Safety Specialist` agent. For patient-facing output, hand the structured `ReconciliationReport` to the `Patient Educator` agent.
>
> If `SafetyVerdict.status == "hold"`, refuse to render a daily plan. Render a clinician-escalation card instead.
>
> Always include citations from the tools that produced each fact.

### Tools (MCP)
Calls every tool except `get_drug_education_handout` (that's owned by Educator).

### Inputs / Outputs
- **Input**: free-text user message + SHARP context
- **Output**: `ReconciliationReport` (rendered to user as a 4-card report; see [DATA_FLOW.md](DATA_FLOW.md))

### Phasing
- **P0**: handles everything inline (no Educator, no Specialist) — produces a Markdown report
- **P1**: delegates patient-facing text to Educator
- **P2**: delegates safety reasoning to Drug Safety Specialist

---

## 2. Drug Safety Specialist (P2)

### Role
Independent clinical-reasoning agent. Receives a proposed regimen and patient context; runs deterministic checks (interactions, renal dosing, allergy cross-reactivity, pregnancy gating); returns a verdict that the Coordinator cannot silently override.

### System prompt (sketch)

> You are a clinical pharmacology safety reviewer. Given a proposed regimen and the patient's pre-admit list, allergies, conditions, eGFR, and demographics, evaluate every clinically-significant risk and return a `SafetyVerdict`.
>
> Use only deterministic tools for drug data: `check_interaction`, `get_renal_dosing_guidance`, `get_patient_context`. Do not infer interactions or dosing from your own knowledge — if a tool fails, set `flags[].severity = "warn"` and `required_clinician_review = true`.
>
> Status semantics:
> - `clear`: no flags above `info` severity
> - `caution`: at least one `warn` or `caution` flag, regimen still safe with monitoring
> - `hold`: at least one `hold` severity flag — regimen MUST NOT be self-administered without clinician review

### Tools (MCP)
- `check_interaction`
- `get_renal_dosing_guidance`
- `get_patient_context` (read-only confirmation)

### Inputs / Outputs
- **Input**: `RegimenProposal` (subset of `ReconciliationReport.changes`) + SHARP context
- **Output**: `SafetyVerdict`

### Authority boundary
- Coordinator MUST surface every flag to the user — no filtering.
- `status="hold"` is binding: Coordinator must refuse to render a daily plan and route to escalation.

---

## 3. Patient Educator (P1)

### Role
Translation specialist. Takes a structured report and produces 6th-grade-reading-level narrative with sourced citations.

### System prompt (sketch)

> You translate medication reconciliation reports into language a patient with a 6th-grade reading level can understand. For each medication change, write one short paragraph explaining what changed, why, and what the patient should do.
>
> For each new or changed drug, call `get_drug_education_handout(rxcui)` and cite the MedlinePlus URL it returns.
>
> Produce a "Questions to ask your doctor" list with 2–4 items.
>
> Never invent drug information. If a handout isn't available, say so and suggest the patient ask their pharmacist.

### Tools (MCP)
- `get_drug_education_handout`

### Inputs / Outputs
- **Input**: `ReconciliationReport` (after Specialist verdict is merged)
- **Output**: `PatientNarrative { sections: list[NarrativeSection], questions: list[str], citations: list[HttpUrl] }`

### Reading-level guarantee
- The output is post-processed by the Educator with a Flesch-Kincaid check — sections above grade 7 are regenerated once.

---

## How they collaborate

```
User
  │
  ▼
┌──────────────────┐
│ Coordinator      │
│ — gathers data   │
│ — drafts changes │
└──┬───────────────┘
   │  RegimenProposal
   ▼
┌──────────────────┐
│ Safety Specialist│
│ — runs checks    │
│ — returns verdict│
└──┬───────────────┘
   │  SafetyVerdict
   ▼
┌──────────────────┐
│ Coordinator      │
│ — merges verdict │
│ — assembles report│
└──┬───────────────┘
   │  ReconciliationReport
   ▼
┌──────────────────┐
│ Patient Educator │
│ — translates     │
│ — adds citations │
└──┬───────────────┘
   │  PatientNarrative
   ▼
Coordinator → renders 4-card report to user
```

Every arrow above carries the SHARP context (see [SHARP_CONTEXT.md](SHARP_CONTEXT.md)).

---

## Marketplace registration

Each agent is published as its own marketplace entry, with its capability tag:

| Agent | Capability tag |
|-------|----------------|
| Reconciliation Coordinator | `medrec.reconcile` |
| Drug Safety Specialist | `medrec.safety_review` |
| Patient Educator | `patient_education.translate` |

The Coordinator looks up specialists by capability tag, not by hardcoded agent ID — so the marketplace can grow without changing the Coordinator.
