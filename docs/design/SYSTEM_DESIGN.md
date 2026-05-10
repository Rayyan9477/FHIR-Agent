# SYSTEM_DESIGN.md — Why we built it this way

## The problem

Post-discharge medication reconciliation is the single most error-prone moment in modern healthcare:

- ~50% of post-discharge adverse events are medication-related
- Hospital-to-home transitions cause ~$26B in unplanned readmissions annually in the US
- Pharmacist-led reconciliation takes 20–40 minutes per patient — frequently skipped under load
- Patients leave the hospital with confusing prescription lists and no plain-language explanation of what changed and why

LLMs can plausibly help, but the naive approach (ask the LLM "what should this patient take?") is dangerous: drug interaction hallucination is a documented LLM failure mode that has produced real harm.

## The design choice that drives everything else

> **Determinism where it matters, LLMs where they're unique.**

Drug interactions, dosing adjustments, and patient education content come from authoritative APIs (RxNav, openFDA, MedlinePlus) — never from the LLM. The LLM does what only an LLM can: reason over messy free-text discharge summaries, normalize informal drug names, and produce 6th-grade reading-level narrative.

This is the answer to **judging criterion #3 (Feasibility)** and the reason this system can be defended to a Cleveland Clinic CMIO.

## Goals

| # | Goal | How |
|---|------|-----|
| G1 | Reconcile pre-admit and discharge med lists into a structured, citable report | FHIR + parse_discharge_summary + Coordinator |
| G2 | Deliver a regimen-safety verdict grounded in authoritative sources | Drug Safety Specialist + RxNav/openFDA |
| G3 | Translate the report into 6th-grade-reading-level patient material with citations | Patient Educator + MedlinePlus |
| G4 | Demonstrate end-to-end SHARP context propagation across MCP and A2A boundaries | SHARP validator + Prompt Opinion native A2A |
| G5 | Ship the MCP server and A2A agents to the Prompt Opinion Marketplace | Marketplace publishing |

## Non-goals

- **Real-time prescribing or e-prescribing.** Out of scope; medico-legal complexity.
- **Replacing clinician judgment.** When in doubt, escalate.
- **Insurance / prior-auth / RCM workflow.** Different problem domain.
- **HIPAA-production deployment.** We operate on synthetic data; productionization is a follow-on project.
- **Multi-language patient material.** P2.

## Judging-criteria mapping

| Criterion | How this design addresses it |
|-----------|------------------------------|
| **AI Factor** | LLMs do parsing of free-text discharge summaries, drug-name normalization, and 6th-grade narrative. Deterministic work delegated to authoritative APIs. The LLM is solving the parts that traditional software cannot. |
| **Potential Impact** | Med-rec errors → 50% of post-discharge AEs, $26B annual readmission cost. Targets a known, measurable, system-level pain point. |
| **Feasibility** | Every drug fact sourced from RxNav / openFDA / MedlinePlus; FHIR is real standard; SHARP is the platform's native context model; no PHI leaves the agent runtime. Defensible to a clinical CMIO. |

## Design principles

### 1. Authority boundaries are real

The Drug Safety Specialist can veto a regimen. The Coordinator cannot silently override. The Patient Educator does not have authority to recommend medical actions. These boundaries are encoded in:

- Pydantic schemas (e.g., `SafetyVerdict.status="hold"` triggers explicit Coordinator behavior)
- System prompts (each agent's prompt names the agent it can hand off to)
- Marketplace capability tags (Coordinator looks up specialists by capability, not name)

### 2. Schema is the contract

Every inter-agent message is a versioned Pydantic schema. We don't "trust the LLM to follow free-text instructions." We use tool-call schemas for structured output, and we validate. See [SCHEMAS.md](SCHEMAS.md).

### 3. SHARP context is sacred

Patient identity is never an LLM-controlled argument. The SHARP context is set at workspace launch, propagated by the platform, and validated at every tool call. See [SHARP_CONTEXT.md](SHARP_CONTEXT.md).

### 4. Failure surfaces, never hides

If a tool fails, the Coordinator says so explicitly. If interaction data is unavailable, the user is told to confirm with their pharmacist. We never let the LLM fabricate a fallback. See [SAFETY.md](SAFETY.md).

### 5. Smaller, well-bounded units

Each MCP tool has one job. Each agent has one authority. Each schema has one purpose. This makes the system easier for humans to audit and easier for AI to extend.

## Why three agents, not one and not five

Tested with one agent: works for P0, but the safety reasoning gets entangled with patient narrative — failures in one taint the other.

Tested with five agents (Coordinator + Reconciler + Interaction Specialist + Pharmacogenomics + Educator): too much orchestration overhead for the demo, and most boundaries don't have meaningful authority differences.

Three is the minimum that demonstrates real authority differentiation:
- **Coordinator** = workflow ownership
- **Specialist** = veto power on safety
- **Educator** = patient-voice ownership

## Why MCP + A2A + FHIR (and not just one)

| Standard | What it solves here |
|----------|---------------------|
| MCP | Reusable tool surface — any agent in the marketplace can call our 9 tools |
| A2A | Inter-agent collaboration with declared capabilities — Coordinator finds Specialist by tag, not name |
| FHIR | Clinical data interop — pre-admit / discharge / context resources |
| SHARP | Identity propagation — without it, every tool call would need patient_id as an argument the LLM controls (UNSAFE) |

Drop any one of these and the demo gets weaker. The hackathon's framing is correct: it's the **intersection** that matters.

## Trade-offs we accepted

| Trade-off | Choice | Reason |
|-----------|--------|--------|
| Live FHIR vs Synthea fixture | Synthea for P0 | Reliability of demo > realism |
| Pharmacist agent vs Drug Safety Specialist | Drug Safety Specialist | Pharmacist scope is broader; we want a veto-bound agent |
| Patient Educator as separate agent vs Coordinator inline | Separate (P1) | Reusability for marketplace; cleaner authority boundary |
| Sonnet for everything vs Haiku where possible | Haiku for Educator | Cost; translation is a Haiku-grade task |
| Caching layer vs no cache | No cache for P0 | Demo-time complexity; RxNav free tier is fine for demo load |
| Multi-language vs English-only | English-only | Scope; translation is solved later |

## Glossary

| Term | Meaning |
|------|---------|
| **FHIR** | Fast Healthcare Interoperability Resources, HL7 R4B |
| **MCP** | Model Context Protocol — Anthropic-led tool protocol for LLMs |
| **A2A** | Agent-to-Agent — inter-agent capability protocol (used by Prompt Opinion) |
| **SHARP** | Prompt Opinion's Extension Specs for propagating patient context across agent calls |
| **RxNav** | NLM's normalized drug nomenclature service (free, public) |
| **openFDA** | FDA's API for drug labels, warnings, and adverse events |
| **MedlinePlus Connect** | NLM's patient-facing health information service |
| **Synthea** | Open-source synthetic patient generator (MITRE) |
| **HAPI** | Public reference FHIR server (free sandbox) |
| **eGFR** | Estimated glomerular filtration rate — kidney function marker |
| **RCM** | Revenue Cycle Management — out of scope here, but the team's day-job domain |
