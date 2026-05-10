# ARCHITECTURE.md — System Architecture

## High-level diagram

```
                  Prompt Opinion Workspace (A2A runtime)
                  ─────────────────────────────────────────
  Patient / Clinician (chat UI)
            │
            ▼
  ┌──────────────────────────────────────┐
  │   Reconciliation Coordinator (A2A)   │
  │   model: Claude Sonnet 4.6           │
  │   role: orchestrate, decide, render  │
  └──┬────────────────────────────┬──────┘
     │ MCP/HTTP+SSE               │ A2A
     ▼                            ▼
┌────────────────────────┐    ┌──────────────────────┐
│ medrec-superpower MCP  │    │  Drug Safety         │
│ Python · `mcp` SDK     │    │  Specialist (A2A)    │
│ FastAPI HTTP+SSE       │    │  model: Sonnet       │
│ 9 tools                │    └──────┬───────────────┘
└────────────┬───────────┘           │ A2A
             │                       ▼
             │            ┌────────────────────────┐
             │            │ Patient Educator (A2A) │
             │            │ model: Claude Haiku    │
             │            └────────────────────────┘
             │
   ┌─────────┼─────────┬───────────────────┐
   ▼         ▼         ▼                   ▼
FHIR R4B   RxNav    openFDA           MedlinePlus
HAPI       (NLM,    (drug labels,     Connect
sandbox    public,  warnings)         (NLM)
or SMART   free)
sim

  SHARP Extension rides every hop:
    { patient_id, fhir_token, encounter_id, user_role, expires_at }
```

## Layered view

```
┌─────────────────────────────────────────────────────────────┐
│ Presentation                                                │
│   Prompt Opinion chat UI (provided by platform)             │
├─────────────────────────────────────────────────────────────┤
│ Orchestration                                               │
│   Reconciliation Coordinator agent                          │
│   A2A handoff to Specialist + Educator                      │
├─────────────────────────────────────────────────────────────┤
│ Domain Reasoning                                            │
│   Drug Safety Specialist agent (clinical decisions)         │
│   Patient Educator agent (translation)                      │
├─────────────────────────────────────────────────────────────┤
│ Tool Surface                                                │
│   medrec-superpower MCP server (9 tools, HTTP+SSE)          │
├─────────────────────────────────────────────────────────────┤
│ Data Sources                                                │
│   FHIR R4B • RxNav • openFDA • MedlinePlus • Surescripts   │
├─────────────────────────────────────────────────────────────┤
│ Identity & Context                                          │
│   SHARP Extension Specs (patient_id, fhir_token, encounter)│
└─────────────────────────────────────────────────────────────┘
```

## Component responsibilities

| Component | Responsibility | Tech | Owns |
|-----------|----------------|------|------|
| Reconciliation Coordinator | Workflow, final report assembly | Sonnet 4.6 + Prompt Opinion A2A | `ReconciliationReport` |
| Drug Safety Specialist | Clinical safety verdict | Sonnet 4.6 + Prompt Opinion A2A | `SafetyVerdict` |
| Patient Educator | 6th-grade narrative | Haiku 4.5 + Prompt Opinion A2A | `PatientNarrative` |
| medrec-superpower MCP | Deterministic tool surface | Python 3.10+ · `mcp` SDK · FastAPI | All 9 tools, error envelopes |
| FHIR Adapter | FHIR R4B resource I/O | `fhir.resources` Python lib | `Patient`, `MedicationRequest`, etc. |
| Drug Knowledge Adapter | RxNav / openFDA / MedlinePlus clients | `httpx` + tenacity | Drug interaction, dosing, handouts |
| SHARP Validator | Token validation + scope check | Custom decorator | Auth on every tool call |

## Technology stack

### MCP server (`medrec-superpower`)

| Layer | Choice | Why |
|-------|--------|-----|
| Language | Python 3.10+ | Aligns with project rules; ML-ecosystem-compatible |
| MCP framework | Official `mcp` SDK | Reference implementation, future-proof |
| Transport | HTTP + SSE | Required for Prompt Opinion compatibility |
| Web | FastAPI | Async, Pydantic-native, `mcp` SDK plays well |
| Validation | Pydantic v2 | Boundary contracts, faster than v1 |
| HTTP client | httpx (async) + tenacity | Retries with backoff |
| FHIR client | `fhir.resources` | R4B resource models |
| Logging | structlog + redaction middleware | PHI-safe structured logs |
| Tests | pytest + pytest-asyncio | Standard |
| Lint/format | ruff + mypy | Project default |
| Packaging | uv + pyproject.toml | Modern, fast |

### A2A agents (Prompt Opinion)

| Layer | Choice | Why |
|-------|--------|-----|
| Runtime | Prompt Opinion A2A | Required by hackathon |
| Model — Coordinator | Claude Sonnet 4.6 | Best quality / cost for orchestration |
| Model — Safety Specialist | Claude Sonnet 4.6 | Clinical reasoning quality matters more than cost |
| Model — Educator | Claude Haiku 4.5 | Translation task, cheaper & faster |
| Config | YAML / Prompt Opinion no-code | Per platform docs |
| Schema validation | Pydantic schemas (mirrored) | Source-of-truth in MCP server, copied for agent configs |

## Deployment topology

```
                    ┌──────────────────────────────┐
                    │ Prompt Opinion Platform      │
                    │ (managed)                    │
                    │  - Hosts agents              │
                    │  - Routes A2A messages       │
                    │  - Maintains SHARP context   │
                    └────────────┬─────────────────┘
                                 │  HTTP+SSE
                                 ▼
              ┌──────────────────────────────────┐
              │ medrec-superpower MCP server     │
              │ (we host this)                   │
              │   Container or single Python proc│
              │   Public URL or tunnel (ngrok)   │
              │   Stateless                      │
              └─────────────┬────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
      FHIR sandbox      Drug APIs         Synthea
      (HAPI public      (RxNav, openFDA,  (local fixture
       or local)         MedlinePlus)      generator)
```

For P0 the MCP server runs on a single host (or `ngrok` tunnel) with Synthea fixture data — no live FHIR. P1 swaps in HAPI sandbox.

## Data flow boundaries

```
   ┌─────────────────────────────────────────────────────┐
   │  Trust boundary 1: User → Coordinator              │
   │  Validation: Prompt Opinion auth + SHARP launch    │
   ├─────────────────────────────────────────────────────┤
   │  Trust boundary 2: Coordinator → MCP               │
   │  Validation: SHARP JWT signature + audience +     │
   │              patient_id-in-scope                  │
   ├─────────────────────────────────────────────────────┤
   │  Trust boundary 3: MCP → External (FHIR/RxNav/...) │
   │  Validation: per-source auth (FHIR token, none for│
   │              RxNav/openFDA/MedlinePlus public)    │
   ├─────────────────────────────────────────────────────┤
   │  Trust boundary 4: Coordinator → Specialist/Edu   │
   │  Validation: SHARP propagation by Prompt Opinion   │
   └─────────────────────────────────────────────────────┘
```

See [SAFETY.md](SAFETY.md) for what happens at each boundary.

## Scaling notes

The system is intentionally stateless at the MCP layer. To scale:
- MCP server: horizontal — add replicas behind a load balancer
- Agents: managed by Prompt Opinion
- Per-source rate limits — the bottleneck is RxNav's free tier (~20 req/sec). For P0 demo it's fine; P2 would add a 24h cache layer.

See [PHASING.md](PHASING.md) for what's in scope when.
