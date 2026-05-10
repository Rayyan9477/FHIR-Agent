# FHIR-Agent — Post-Discharge Medication Reconciliation System

> Healthcare AI agents that reconcile pre-admit and discharge medication lists, deliver authoritative safety verdicts, and translate the result into 6th-grade-reading-level patient material.
>
> Built for **Agents Assemble — The Healthcare AI Endgame** hackathon (Prompt Opinion / Darena Health, 2026).

---

## Submission deadline: 2026-05-11, 23:00 EDT

**Status**: documentation complete, implementation not started. Begin with [docs/build/BUILD.md](docs/build/BUILD.md).

| Phase | Status | What it gets you |
|------|--------|------------------|
| P0 | not started | Valid hackathon submission (3 tools + 1 agent + fixture + 90s video) |
| P1 | not started | Strong submission (+ Patient Educator, live HAPI, 4-card report) |
| P2 | not started | Marketplace-grade (+ Drug Safety Specialist, eval set, registry) |

The P0/P1 line is the **submission floor**. See [docs/build/PHASING.md](docs/build/PHASING.md).

---

## TL;DR

Patient leaves hospital → confused about which meds to keep, stop, or restart → an agent system pulls FHIR data, runs deterministic drug checks (RxNav / openFDA / MedlinePlus — never the LLM), and produces a clinician-defensible reconciliation report plus a plain-English daily plan.

```
Patient asks: "Should I still take my Metformin?"
        │
        ▼
  ┌──────────────────────────────────────────────────┐
  │ Reconciliation Coordinator (A2A, Sonnet)         │
  │  ├─ medrec-superpower MCP server (9 tools)       │
  │  ├─ Drug Safety Specialist (A2A, Sonnet)     P2  │
  │  └─ Patient Educator (A2A, Haiku)            P1  │
  └──────────────────────────────────────────────────┘
        │
        ▼
  Report + safety verdict + daily plan + ask-your-doctor list
```

---

## Where to look

| You want to… | Start here |
|---|---|
| Build today, ship before deadline | [docs/build/BUILD.md](docs/build/BUILD.md) |
| Understand the full system | [docs/INDEX.md](docs/INDEX.md) |
| Look up an external API | [docs/reference/REFERENCES.md](docs/reference/REFERENCES.md) |
| See open risks / unanswered questions | [docs/reference/RISKS.md](docs/reference/RISKS.md) |
| Work with Claude Code in this repo | [CLAUDE.md](CLAUDE.md) |

---

## Repository layout

```
FHIR-Agent/
├── README.md                     ← you are here
├── CLAUDE.md                     Claude Code rules for this repo
├── docs/
│   ├── INDEX.md                  Reading guide by audience
│   ├── build/                    Execution
│   │   ├── BUILD.md              P0 playbook (start here)
│   │   ├── PHASING.md            P0 / P1 / P2 scope
│   │   ├── TESTING.md            Unit, integration, eval strategy
│   │   └── DEMO.md               3-min video script + recording guide
│   ├── design/                   The system
│   │   ├── SYSTEM_DESIGN.md      Why this exists, goals, judging criteria
│   │   ├── ARCHITECTURE.md       Components, stack, deployment topology
│   │   ├── AGENTS.md             Three A2A agents + authority boundaries
│   │   ├── MCP_SERVER.md         The 9 MCP tools
│   │   ├── SCHEMAS.md            Pydantic models (single source of truth)
│   │   ├── SHARP_CONTEXT.md      Identity propagation rules
│   │   ├── DATA_FLOW.md          End-to-end Metformin demo path (T0 → T7)
│   │   ├── SYSTEM_FLOW.md        Control + agent decision rules
│   │   └── SAFETY.md             Safety rules R1–R5 + error matrix
│   └── reference/                External
│       ├── REFERENCES.md         Standards, APIs, sandboxes, libs
│       └── RISKS.md              Risk register + open questions
└── medrec_superpower/            (to be created — see docs/build/BUILD.md)
    ├── server.py                 MCP entrypoint, HTTP+SSE
    ├── tools/                    One file per MCP tool
    ├── fhir/                     FHIR client + adapters
    ├── drug/                     RxNav, openFDA, MedlinePlus clients
    ├── sharp/                    SHARP JWT validation + decorator + redaction
    └── schemas.py                Pydantic models (mirrors docs/design/SCHEMAS.md)
```

---

## Hackathon alignment

| Requirement | Where to find it |
|---|---|
| MCP server (Track 1) | [docs/design/MCP_SERVER.md](docs/design/MCP_SERVER.md) |
| A2A agent (Track 2) | [docs/design/AGENTS.md](docs/design/AGENTS.md) |
| SHARP Extension Specs | [docs/design/SHARP_CONTEXT.md](docs/design/SHARP_CONTEXT.md) |
| Marketplace publish step | [docs/build/BUILD.md](docs/build/BUILD.md) §Marketplace |
| Demo video < 3 min | [docs/build/DEMO.md](docs/build/DEMO.md) |
| FHIR R4B | [docs/design/MCP_SERVER.md](docs/design/MCP_SERVER.md), [docs/design/DATA_FLOW.md](docs/design/DATA_FLOW.md) |

---

## Non-negotiable rules

These are restated from [CLAUDE.md](CLAUDE.md) and [docs/design/SAFETY.md](docs/design/SAFETY.md):

1. **Drug data never comes from the LLM** — RxNav / openFDA / MedlinePlus only. Tool failure → say so, never substitute (R3).
2. **`patient_id` from SHARP only**, never from LLM-controlled args. Cross-patient → HTTP 403 (R1).
3. **No PHI in plaintext logs** — redaction middleware on every log line (R2).
4. **`SafetyVerdict.status="hold"` → no daily plan** — mechanically enforced by Pydantic validator (R5).
5. **Never `git commit` / `git push`** — that's the user's job.

---

## Quick start (planned — after BUILD.md is executed)

```bash
uv sync
uv run python -m medrec_superpower.server  # binds 0.0.0.0:8765, HTTP+SSE
ngrok http 8765                            # expose for Prompt Opinion

# tests
uv run pytest tests/unit -v
uv run pytest tests/integration -v
uv run python tests/eval/run_eval.py
```
