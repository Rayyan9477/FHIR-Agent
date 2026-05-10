# Documentation Index

> Reading order depends on what you need to do. Three audiences:

---

## Build (executing now)

If you are running the build today, read in this exact order:

1. [build/BUILD.md](build/BUILD.md) — P0 execution playbook with step-by-step file map and acceptance checks
2. [build/PHASING.md](build/PHASING.md) — what's in P0, what's not, what's deferred
3. [build/TESTING.md](build/TESTING.md) — test patterns for each tool + anti-hallucination gate
4. [build/DEMO.md](build/DEMO.md) — 90s demo script (P0) / 3-min script (P1+) and recording guide

Cross-refs to design docs as needed.

---

## Design (understanding the system)

Read top-to-bottom for the full picture, or jump to a specific concern:

| # | Doc | Covers |
|---|-----|--------|
| 1 | [design/SYSTEM_DESIGN.md](design/SYSTEM_DESIGN.md) | Why this exists; goals, non-goals, judging-criteria mapping; the *determinism where it matters* principle |
| 2 | [design/ARCHITECTURE.md](design/ARCHITECTURE.md) | High-level diagram, component responsibilities, tech stack, deployment topology, trust boundaries |
| 3 | [design/AGENTS.md](design/AGENTS.md) | Three A2A agents — Coordinator (P0), Educator (P1), Specialist (P2) — with prompts and authority boundaries |
| 4 | [design/MCP_SERVER.md](design/MCP_SERVER.md) | All 9 MCP tools — signatures, backing data sources, error envelopes, SHARP enforcement |
| 5 | [design/SCHEMAS.md](design/SCHEMAS.md) | Pydantic v2 models (`MedRecord`, `ReconciliationReport`, `SafetyVerdict`, `ToolResult`, etc.) — single source of truth |
| 6 | [design/SHARP_CONTEXT.md](design/SHARP_CONTEXT.md) | SHARP JWT shape, propagation rules (V1–V6), implementation sketch — **the single most important demo element** |
| 7 | [design/DATA_FLOW.md](design/DATA_FLOW.md) | End-to-end Metformin demo (T0 → T7) — exact data shape for fixtures |
| 8 | [design/SYSTEM_FLOW.md](design/SYSTEM_FLOW.md) | Control flow + Coordinator decision rules C1–C4 + Specialist S1–S2 + Educator E1–E2 |
| 9 | [design/SAFETY.md](design/SAFETY.md) | Hard rules R1–R5, error-handling matrix, anti-hallucination test |

---

## Reference (looking things up)

- [reference/REFERENCES.md](reference/REFERENCES.md) — every external dependency (RxNav, openFDA, MedlinePlus, HAPI, Synthea, Python libs, models)
- [reference/RISKS.md](reference/RISKS.md) — risk register (K1–K17), open questions (Q1–Q8), risk-reduction checklist
- [../CLAUDE.md](../CLAUDE.md) — Claude Code rules for this repo

---

## Source-of-truth rule

When two docs disagree, this table wins:

| Topic | Authoritative doc |
|---|---|
| Pydantic schemas | [design/SCHEMAS.md](design/SCHEMAS.md) |
| MCP tool signatures + behavior | [design/MCP_SERVER.md](design/MCP_SERVER.md) |
| SHARP JWT shape & validation rules | [design/SHARP_CONTEXT.md](design/SHARP_CONTEXT.md) |
| Hard safety rules R1–R5 | [design/SAFETY.md](design/SAFETY.md) |
| Agent system prompts + authority | [design/AGENTS.md](design/AGENTS.md) |
| Demo data shape | [design/DATA_FLOW.md](design/DATA_FLOW.md) |
| Coordinator/Specialist/Educator decision rules | [design/SYSTEM_FLOW.md](design/SYSTEM_FLOW.md) |
| P0 / P1 / P2 scope | [build/PHASING.md](build/PHASING.md) |
| Execution order + acceptance criteria | [build/BUILD.md](build/BUILD.md) |
| External URLs, API endpoints, libs | [reference/REFERENCES.md](reference/REFERENCES.md) |
| Unknown answers / blockers | [reference/RISKS.md](reference/RISKS.md) |

---

## How to use this index with Claude Code

When you ask Claude Code to implement something, point it at the relevant **source-of-truth doc** first. Example:

> "Implement `medrec_superpower/schemas.py` per [docs/design/SCHEMAS.md](design/SCHEMAS.md). P0 minimum is `MedRecord`, `MedChangeEvent`, `ReconciliationReport`, `ErrorEnvelope`, `ToolResult`."

Claude has the spec at hand and won't drift.

---

## MCP servers + skills available in this workspace

Per [CLAUDE.md](../CLAUDE.md) §AI-assistance guidance:

| Resource | When to use it |
|---|---|
| **Context7 MCP** | Verify current `mcp` Python SDK API, `pydantic` v2 syntax, `fhir.resources` shapes — the SDK moves fast, training data is stale |
| **Sequential Thinking MCP** | Multi-component design questions (e.g. SHARP propagation across A2A boundary) |
| **Explore agent** | Map all callers of a tool before changing its signature |
| **ICD-10 / NPI / CMS Coverage / PubMed MCPs** | Healthcare-specific lookups during P2 expansion (renal dosing, citations) |
| **superpowers:test-driven-development** | When writing a new tool, before implementation |
| **superpowers:verification-before-completion** | Before claiming a step in BUILD.md is done |
