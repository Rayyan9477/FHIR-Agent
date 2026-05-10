# FHIR-Agent — Post-Discharge Medication Reconciliation System

> Healthcare AI agents that reconcile pre-admit and discharge medication lists, deliver authoritative safety verdicts, and translate the result into 6th-grade-reading-level patient material.
>
> Built for the **Agents Assemble — The Healthcare AI Endgame** hackathon (Prompt Opinion / Darena Health, 2026).

---

## TL;DR

Patient leaves hospital → confused about which meds to keep, stop, or restart → an agent system pulls FHIR data, runs deterministic drug checks, and produces a clinician-defensible reconciliation report plus a plain-English daily plan.

```
Patient asks: "Should I still take my Metformin?"
        │
        ▼
  ┌──────────────────────────────────────────────────┐
  │ Reconciliation Coordinator (A2A, Sonnet)         │
  │  ├─ medrec-superpower MCP server (9 tools)       │
  │  ├─ Drug Safety Specialist (A2A, Sonnet)         │
  │  └─ Patient Educator (A2A, Haiku)                │
  └──────────────────────────────────────────────────┘
        │
        ▼
  Reconciliation report + safety verdict + daily plan + ask-your-doctor list
```

---

## Document Map

Read in this order if you're new:

| # | File | What it covers |
|---|------|----------------|
| 1 | [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) | Why this system exists; goals, non-goals, judging-criteria mapping |
| 2 | [ARCHITECTURE.md](ARCHITECTURE.md) | High-level architecture, technology stack, deployment topology |
| 3 | [AGENTS.md](AGENTS.md) | The three A2A agents — roles, prompts, authority boundaries |
| 4 | [MCP_SERVER.md](MCP_SERVER.md) | The 9 MCP tools — signatures, backing data sources, errors |
| 5 | [SCHEMAS.md](SCHEMAS.md) | Pydantic models that flow between components |
| 6 | [SHARP_CONTEXT.md](SHARP_CONTEXT.md) | SHARP extension propagation rules |
| 7 | [DATA_FLOW.md](DATA_FLOW.md) | End-to-end demo data flow (T0 → T7) |
| 8 | [SYSTEM_FLOW.md](SYSTEM_FLOW.md) | Control/orchestration flow, agent handoff rules |
| 9 | [SAFETY.md](SAFETY.md) | Safety rules R1-R5 and error-handling matrix |
| 10 | [TESTING.md](TESTING.md) | Unit, integration, and eval strategy |
| 11 | [PHASING.md](PHASING.md) | P0 (ship-tonight) / P1 / P2 with hard ship line |
| 12 | [DEMO.md](DEMO.md) | 3-min video script, screenshot plan |
| 13 | [RISKS.md](RISKS.md) | Open questions, failure modes, mitigation |
| 14 | [REFERENCES.md](REFERENCES.md) | External standards, APIs, sandboxes, prior art |
| 15 | [CLAUDE.md](CLAUDE.md) | Claude Code rules for this repo (read me before any AI-assisted work) |

---

## Repository Layout (planned)

```
FHIR-Agent/
├── docs/                       # this directory (human-facing)
│   └── *.md
├── medrec_superpower/          # Python MCP server package
│   ├── __init__.py
│   ├── server.py               # mcp SDK entrypoint, HTTP+SSE transport
│   ├── tools/                  # one file per MCP tool
│   ├── fhir/                   # FHIR client, resource adapters
│   ├── drug/                   # RxNav, openFDA, MedlinePlus clients
│   ├── sharp/                  # SHARP context validation, decorators
│   └── schemas.py              # Pydantic models
├── agents/                     # Prompt Opinion A2A agent configs (YAML/JSON)
│   ├── coordinator/
│   ├── drug_safety_specialist/
│   └── patient_educator/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── eval/
│       └── goldens/            # Synthea-generated scenarios
├── pyproject.toml              # uv / ruff / mypy / pytest config
└── README.md                   # this file (root) — points to docs/
```

---

## Quick Start (planned)

```bash
# Clone, install, run MCP server locally
uv sync
uv run python -m medrec_superpower.server  # binds 0.0.0.0:8765, HTTP+SSE

# Register on Prompt Opinion (manual step)
# 1. Sign in: https://app.promptopinion.ai
# 2. Add MCP server URL in Marketplace settings
# 3. Import the three agent configs from agents/

# Run tests
uv run pytest tests/unit -v
uv run pytest tests/integration -v
uv run python tests/eval/run_eval.py
```

---

## Status

- [ ] **P0** — MCP (3 tools) + Coordinator + fixture data + 90s demo video
- [ ] **P1** — + Patient Educator + 4 more MCP tools + HAPI sandbox
- [ ] **P2** — + Drug Safety Specialist as separate A2A + 2 more MCP tools + eval set

See [PHASING.md](PHASING.md) for the hard ship line.

## Hackathon Alignment

| Requirement | Where |
|-------------|-------|
| MCP server (Track 1) | [MCP_SERVER.md](MCP_SERVER.md) |
| A2A agent (Track 2) | [AGENTS.md](AGENTS.md) |
| SHARP Extension Specs | [SHARP_CONTEXT.md](SHARP_CONTEXT.md) |
| Published to Marketplace | covered in [DEMO.md](DEMO.md) checklist |
| Demo video < 3min | [DEMO.md](DEMO.md) |
| FHIR R4B | covered in [MCP_SERVER.md](MCP_SERVER.md) and [DATA_FLOW.md](DATA_FLOW.md) |
