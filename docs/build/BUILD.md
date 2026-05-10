# BUILD.md — P0 Execution Playbook

> Single executable path from empty repo → submitted hackathon entry.
> Time budget: 6–10 focused hours. Submission deadline: **2026-05-11, 23:00 EDT**.

---

## How to read this doc

Every step has:

- **Path**: the file you're creating or editing
- **Time**: realistic estimate for a focused engineer
- **Source of truth**: the design doc to follow exactly — if the doc and your intuition disagree, the doc wins
- **Acceptance**: how you know the step is done

This file deliberately contains **no inline code**. Implementation comes from the design docs. If a step is ambiguous, the linked spec resolves it.

Mark each step `[x]` as you complete it.

---

## Pre-flight (≈ 30 min)

These are blockers — none of the build steps work until these are green.

- [ ] Prompt Opinion account active at https://app.promptopinion.ai
- [ ] Anthropic API key in `ANTHROPIC_API_KEY` env var
- [ ] Python 3.10+ (`python3 --version`)
- [ ] `uv` installed (`uv --version`) — see [reference/REFERENCES.md](../reference/REFERENCES.md) §Python ecosystem
- [ ] `ngrok` installed and authenticated (free tier OK)
- [ ] Devpost account ready at https://agents-assemble.devpost.com
- [ ] Cleared 6–10 contiguous hours

### Unknown-resolution checks (RISKS Q1/Q2 — DO NOT SKIP)

These are the two questions that can derail the build if discovered late. Resolve **before Step 9**:

- [ ] **Q1 — SHARP JWT format** confirmed (claim names, signing algo, JWKS endpoint)
  - Check: https://github.com/prompt-opinion/ samples, Discord https://discord.gg/JS2bZVruUg
  - Fallback: if no public docs, implement against the shape in [design/SHARP_CONTEXT.md](../design/SHARP_CONTEXT.md) §Context shape and adjust at integration time
- [ ] **Q2 — Marketplace registration flow** confirmed (manual UI vs CLI vs API)
  - Check: Marketplace settings in app.promptopinion.ai after sign-in
  - Capture: exact URL field, capability-tag field, review timeline

If either is still unanswered after 45 min of investigation, post in Discord and continue with the Synthea fixture path (Step 5) so Q1/Q2 don't block code progress.

---

## Build sequence

### Step 1 — Project scaffold (≈ 15 min)

**Paths**:
- `pyproject.toml`
- `medrec_superpower/__init__.py`
- `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`

**Source of truth**: [design/ARCHITECTURE.md](../design/ARCHITECTURE.md) §Technology stack, [../../CLAUDE.md](../../CLAUDE.md) §Stack

**Dependencies to add** (`uv add ...`):
- Runtime: `mcp`, `pydantic>=2`, `fastapi`, `httpx`, `tenacity`, `python-jose[cryptography]`, `structlog`
- Dev: `pytest`, `pytest-asyncio`, `ruff`, `mypy`

**Verify-with-Context7 before installing**: `mcp` Python SDK current install pattern + transport API (the SDK moves fast — see [../../CLAUDE.md](../../CLAUDE.md) §Use Context7 MCP for).

**Acceptance**:
- `uv sync` exits 0
- `uv run python -c "import mcp, pydantic, fastapi, httpx"` works
- `uv run ruff check .` runs (no findings yet — package is empty)

---

### Step 2 — Pydantic schemas (≈ 45 min)

**Path**: `medrec_superpower/schemas.py`

**Source of truth**: [design/SCHEMAS.md](../design/SCHEMAS.md) §Models — copy class definitions exactly, not loosely.

**P0 minimum subset** (everything else is P1+ and can be stubbed or omitted):
- `MedRecord`
- `MedChangeAction` (enum)
- `MedChangeEvent`
- `SafetyFlag`, `SafetyVerdict` (Coordinator does safety inline in P0 — produce minimal verdict to satisfy `ReconciliationReport`)
- `ReconciliationReport` + the `hold_means_no_daily_plan` model_validator
- `ErrorEnvelope`
- `ToolResult[T]` + the `ok_xor_error` model_validator

**P1/P2 schemas to skip** (`PatientContext`, `RegimenProposal`, `DailyPlanEntry`, `NarrativeSection`, `PatientNarrative`) — leave for later phases.

**Verify-with-Context7**: Pydantic v2 `model_validator` syntax (do not use v1 `@validator` patterns).

**Acceptance**:
- `tests/unit/test_pydantic_schemas.py` covers:
  - `test_safety_verdict_hold_status_consistent_with_flags`
  - `test_reconciliation_report_hold_means_no_daily_plan`
  - `test_tool_result_ok_xor_error`
  - `test_med_change_event_round_trip_json`
- All pass.

---

### Step 3 — SHARP validation (≈ 45 min)

**Paths**:
- `medrec_superpower/sharp/__init__.py`
- `medrec_superpower/sharp/jwt.py`
- `medrec_superpower/sharp/decorator.py`
- `medrec_superpower/sharp/redact.py`

**Source of truth**: [design/SHARP_CONTEXT.md](../design/SHARP_CONTEXT.md) §Implementation notes + §Validation rules

**P0 validation scope** (drop V4, V6 for P0):
- V1 — JWT signature verifies against platform public key
- V2 — `expires_at` in future (30s clock skew)
- V3 — `audience == "medrec-superpower"`
- V5 — if `patient_id` kwarg ≠ SHARP-bound → 403

**Fixture**: one valid signed test token in `tests/fixtures/sharp/valid_p123.jwt`. Generate with a local test RSA keypair so unit tests don't need the platform.

**Redaction**: `redact_processor` for `structlog` — allowlist from [design/SAFETY.md](../design/SAFETY.md) §Privacy posture.

**Acceptance**:
- `tests/unit/test_sharp_validation.py` covers:
  - valid token → SharpContext returned with correct claims
  - expired token → 401 / SharpUnauthorized
  - invalid signature → 401
  - audience mismatch → 401
  - cross-patient kwarg → 403 / SharpForbidden
- `tests/unit/test_redact_logging.py` confirms `patient_id` redacted in log output

---

### Step 4 — Synthea fixture (P123) (≈ 30 min)

**Paths**:
- `tests/fixtures/synthea/P123.json` (Patient + Encounter + MedicationStatement + MedicationRequest + DocumentReference)
- `tests/fixtures/synthea/loader.py` (helper that returns shaped Python dicts)

**Source of truth**: [design/DATA_FLOW.md](../design/DATA_FLOW.md) §T2 — copy the exact RxCUIs, doses, frequencies, and discharge-summary text shown there.

**Must contain**:
- Pre-admit meds: Metformin 1000 BID (rxcui 860975), Lisinopril 10 QD (rxcui 314076)
- Discharge meds: Losartan 50 QD (rxcui 200316), Atorvastatin 40 QHS (rxcui 617310)
- DocumentReference free text with HOLD-Metformin language
- Patient name distinctly synthetic — e.g. "Aaron Rodriguez Synthea-7" (RISKS K16)

**Acceptance**:
- `loader.load_p123()` returns dict with `pre_admit`, `discharge`, `discharge_summary_text` keys
- All 4 meds have valid RxCUIs
- Name is recognizably synthetic

---

### Step 5 — Error envelope helpers (≈ 15 min)

**Path**: `medrec_superpower/errors.py`

**Source of truth**: [design/MCP_SERVER.md](../design/MCP_SERVER.md) §Error envelope + [design/SAFETY.md](../design/SAFETY.md) §Error-handling matrix

**Build**: factory functions for each `code` literal (`forbidden(msg)`, `not_found(msg)`, `upstream_error(msg, retryable=True)`, etc.) that return `ToolResult` instances. Keeps tool code clean.

**Acceptance**: each factory produces a valid `ToolResult` that passes the `ok_xor_error` validator.

---

### Step 6 — Tool: `get_pre_admit_meds` (≈ 30 min)

**Path**: `medrec_superpower/tools/get_pre_admit_meds.py`

**Source of truth**: [design/MCP_SERVER.md](../design/MCP_SERVER.md) §1

**P0 backing**: Synthea fixture from Step 4 (no live FHIR yet — that's P1)

**Must**:
- Decorate with `@requires_sharp` (Step 3)
- Read `patient_id` from SHARP, never from kwargs
- Return `ToolResult[list[MedRecord]]`
- Log via `structlog` with PHI redaction

**Acceptance**: `tests/unit/test_tool_get_pre_admit_meds.py`
- happy path: returns 2 meds for P123 (Metformin, Lisinopril)
- cross-patient kwarg → 403 envelope
- unknown patient → `ok=true, data=[]` (not an error per spec)

---

### Step 7 — Tool: `get_discharge_meds` (≈ 30 min)

**Path**: `medrec_superpower/tools/get_discharge_meds.py`

**Source of truth**: [design/MCP_SERVER.md](../design/MCP_SERVER.md) §2

**Identical pattern to Step 6** but reads `encounter_id` from SHARP and returns discharge regimen.

**Acceptance**: returns 2 meds (Losartan, Atorvastatin); cross-encounter 403; empty 200.

---

### Step 8 — Tool: `check_interaction` (real RxNav call) (≈ 45 min)

**Path**:
- `medrec_superpower/tools/check_interaction.py`
- `medrec_superpower/drug/rxnav.py`

**Source of truth**: [design/MCP_SERVER.md](../design/MCP_SERVER.md) §3, [reference/REFERENCES.md](../reference/REFERENCES.md) §Drug knowledge APIs

**Critical behavior** (R3): RxNav 5xx → return `{ok: true, data: {check_succeeded: false}}`. **Never hallucinate**. The Coordinator's job is to surface this to the user explicitly.

**RxNav endpoint**: `/interaction/list.json` — note this API was deprecated early 2024; document fallback in [reference/REFERENCES.md](../reference/REFERENCES.md) §RxNav interaction API note.

**Retries**: `tenacity` exponential backoff, max 3 attempts, only on 5xx + timeout.

**Acceptance**: `tests/unit/test_tool_check_interaction.py`
- known interaction (warfarin 11289 + ibuprofen 5640) → severity present
- no interaction (metformin 860975 + losartan 200316) → severity null
- RxNav 503 → `ok=true, data.check_succeeded=false` (this is the hallucination-prevention test)

---

### Step 9 — MCP server entrypoint (≈ 20 min)

**Path**: `medrec_superpower/server.py`

**Source of truth**: [design/MCP_SERVER.md](../design/MCP_SERVER.md) §Server entrypoint

**Verify-with-Context7**: current `FastMCP` constructor + `mcp.run(transport=...)` signature for the Python SDK. Do not trust memory.

**Must**:
- Register the 3 P0 tools
- Bind 0.0.0.0:8765, HTTP+SSE transport
- Declare capability tags `medrec.fhir_data`, `medrec.reconcile` in server metadata

**Acceptance**:
- `uv run python -m medrec_superpower.server` listens on 8765
- `curl http://localhost:8765/` returns 200 (or SSE-appropriate response)
- MCP tool listing endpoint shows all 3 tools

---

### Step 10 — Coordinator agent config (≈ 45 min)

**Path**: `agents/coordinator/agent.yaml` (or whichever format Prompt Opinion uses — verify in Q2)

**Source of truth**: [design/AGENTS.md](../design/AGENTS.md) §1 Reconciliation Coordinator

**Must include**:
- Model: Claude Sonnet 4.6
- System prompt from [design/AGENTS.md](../design/AGENTS.md) §1 (sketch) — adapted for P0 (no Educator/Specialist handoff; render Markdown report inline)
- Tools: 3 MCP tools wired via the server URL from Step 11
- Output schema: `ReconciliationReport` JSON Schema (export from `schemas.py` via `model_json_schema()`)
- Capability tag: `medrec.reconcile`

**Acceptance**: agent loads in Prompt Opinion workspace, successfully calls all 3 MCP tools.

---

### Step 11 — Public URL + Marketplace listing (≈ 30 min)

**Setup**:
- `ngrok http 8765` → capture HTTPS URL
- Sign in to https://app.promptopinion.ai
- In Marketplace settings: add MCP server URL + capability tags
- Submit MCP server + Coordinator agent for marketplace review

**Source of truth**: [design/MCP_SERVER.md](../design/MCP_SERVER.md) §Marketplace publishing + Q2 resolution

**Critical**: marketplace review can take time. Submit **early in the build**, not last. K8 in [reference/RISKS.md](../reference/RISKS.md).

**Acceptance**:
- ngrok URL HTTPS-accessible from a clean browser (K14)
- Marketplace listing **approved** (not just submitted) — K8
- Coordinator agent visible in marketplace with the `medrec.reconcile` tag

---

### Step 12 — End-to-end test (≈ 20 min)

**Path**: `tests/integration/test_p0_metformin.py`

**Source of truth**: [design/DATA_FLOW.md](../design/DATA_FLOW.md) §T0–T7 (simplified for P0 — no Specialist, no Educator)

**Scenario**:
- SHARP context for P123/E456
- User message: "Should I still be taking my Metformin?"
- Expected: `ReconciliationReport` returned, includes Metformin in `changes` with action HOLD-or-similar, citations present from RxNav

**Acceptance**:
- Test runs against the live MCP server (booted in test fixture)
- Completes in < 30s wall clock
- Report validates against `ReconciliationReport` schema

---

### Step 13 — Demo video (≈ 60 min including recording + editing) (≈ 60 min)

**Source of truth**: [build/DEMO.md](DEMO.md) §Script + §Production checklist

**P0 cut**: 90 seconds (the P0 ship floor) — not the full 3-minute version.

**Must show on screen**:
1. SHARP context populated at workspace launch
2. Three MCP tool calls in the trace, each with the SHARP header visible
3. The final `ReconciliationReport` rendered as Markdown
4. The Prompt Opinion marketplace listing

**Don't show**: code; localhost in URLs; real-looking patient names (verify Synthea name is distinctly synthetic — K16).

**Hosting**: YouTube unlisted (allow embedding) — capture the URL for Devpost.

**Acceptance**:
- Total length under 90s for P0 (under 3:00 for P1+)
- SHARP context visible at least once
- Three tool calls visible at least once
- Final report visible at the end
- URL is reachable from a fresh browser session

---

### Step 14 — Devpost submission (≈ 30 min)

**URL**: https://agents-assemble.devpost.com/

**Required fields**:
- [ ] Project name: *medrec-superpower*
- [ ] Tagline (1 line) — copy from [README.md](../../README.md) TL;DR
- [ ] Project repo URL (this repo, public)
- [ ] MCP server URL (the ngrok HTTPS endpoint from Step 11)
- [ ] Coordinator agent marketplace link (from Step 11)
- [ ] Demo video URL (from Step 13)
- [ ] Long description: 2–3 paragraphs — adapt from [design/SYSTEM_DESIGN.md](../design/SYSTEM_DESIGN.md) §The problem + §Design principles
- [ ] Screenshots: architecture diagram, report rendering, marketplace listing — see [build/DEMO.md](DEMO.md) §Screenshots for Devpost gallery

**Acceptance**: form fully submitted before **2026-05-11 23:00 EDT**. Save the confirmation email.

---

## Final pre-submit checklist

Run all of these green before you click Submit on Devpost:

```
[ ] uv run ruff check .          # 0 findings
[ ] uv run ruff format --check . # clean
[ ] uv run mypy medrec_superpower # 0 errors
[ ] uv run pytest tests/unit      # all pass
[ ] uv run pytest tests/integration  # all pass

[ ] R1 — cross-patient kwarg returns 403 (tested)
[ ] R2 — sample log line shows <redacted> for patient_id (eyeballed)
[ ] R3 — RxNav 503 path returns check_succeeded=false (tested)
[ ] R5 — Pydantic refuses ReconciliationReport(safety=hold, daily_plan=[...]) (tested)

[ ] K1 — MCP server registered in Marketplace, listing visible
[ ] K2 — SHARP token format verified (matches platform shape)
[ ] K3 — Synthea fixture loads with no external deps
[ ] K8 — Marketplace listing APPROVED (not just submitted)
[ ] K14 — ngrok HTTPS URL reachable from public internet
[ ] K16 — fixture patient name is distinctly synthetic
[ ] K17 — demo video < 90s (P0) or < 3:00 (P1+)

[ ] Demo video plays from a logged-out browser tab
[ ] Repo README shows current status accurately
[ ] Devpost form complete; deadline > 2h away
```

If any item is `[ ]`, hold the submission and resolve it.

---

## Hour budget summary

| Step | Time |
|------|-----:|
| Pre-flight + Q1/Q2 resolution | 30–60 min |
| 1. Scaffold | 15 min |
| 2. Schemas | 45 min |
| 3. SHARP validation | 45 min |
| 4. Synthea fixture | 30 min |
| 5. Error helpers | 15 min |
| 6. get_pre_admit_meds | 30 min |
| 7. get_discharge_meds | 30 min |
| 8. check_interaction | 45 min |
| 9. server.py | 20 min |
| 10. Coordinator agent | 45 min |
| 11. ngrok + Marketplace | 30 min |
| 12. E2E test | 20 min |
| 13. Demo video | 60 min |
| 14. Devpost submission | 30 min |
| **Total (focused)** | **~7.5 hours** |
| **Slack buffer** | +1.5 hours |

If at any point you're > 30 min behind on a step, escalate to "what's the minimum that ships?" — running over on one step eats the buffer that protects the demo video.

---

## What's explicitly NOT in P0

Don't build any of this today, even if it feels easy. From [build/PHASING.md](PHASING.md):

- ❌ Patient Educator agent (Coordinator generates Markdown inline)
- ❌ Drug Safety Specialist agent (Coordinator runs `check_interaction` inline)
- ❌ `lookup_rxnorm` tool (RxCUIs hard-coded in fixture)
- ❌ `parse_discharge_summary` tool (use the fixture's prerecorded summary text)
- ❌ `get_patient_context` tool (use fixture)
- ❌ `get_drug_education_handout` tool
- ❌ `get_renal_dosing_guidance` tool
- ❌ `get_pharmacy_fill_history` tool
- ❌ Live HAPI FHIR sandbox (P1)
- ❌ 4-card visual report (Markdown is fine)
- ❌ Eval set (P2)
- ❌ Capability registry (hardcode the Coordinator's agent ID)
- ❌ Caching layer
- ❌ Multi-language Educator

Track your discipline against this list. P0 closing on time > P1 features that don't ship.

---

## Source-of-truth pointers (quick reference)

| Building… | Read first |
|-----------|------------|
| Pydantic class | [design/SCHEMAS.md](../design/SCHEMAS.md) |
| MCP tool | [design/MCP_SERVER.md](../design/MCP_SERVER.md) + decorator from [design/SHARP_CONTEXT.md](../design/SHARP_CONTEXT.md) |
| SHARP validator | [design/SHARP_CONTEXT.md](../design/SHARP_CONTEXT.md) §Implementation notes |
| Agent config | [design/AGENTS.md](../design/AGENTS.md) + [design/SYSTEM_FLOW.md](../design/SYSTEM_FLOW.md) §Inter-agent message contract |
| Fixture data | [design/DATA_FLOW.md](../design/DATA_FLOW.md) §T2 |
| Error envelope | [design/MCP_SERVER.md](../design/MCP_SERVER.md) §Error envelope + [design/SAFETY.md](../design/SAFETY.md) §Error-handling matrix |
| Test pattern | [build/TESTING.md](TESTING.md) |
| Demo recording | [build/DEMO.md](DEMO.md) |
| External API | [reference/REFERENCES.md](../reference/REFERENCES.md) |
| Open question / known risk | [reference/RISKS.md](../reference/RISKS.md) |

---

## Using Claude Code on this build

Pattern that works well: open Claude Code in this repo, then for each step say:

> "Implement Step 2 from `docs/build/BUILD.md`. Spec is `docs/design/SCHEMAS.md`. Build only the P0 minimum subset listed in BUILD §Step 2."

Claude reads CLAUDE.md, follows the project rules, and stops when it hits the acceptance criteria. Per [CLAUDE.md](../../CLAUDE.md):

- Use **Context7 MCP** for fresh `mcp` SDK + Pydantic v2 syntax (Steps 1, 2, 9)
- Use **Sequential Thinking MCP** for SHARP propagation questions (Step 3, 10)
- Use **Explore agent** to map callers when changing schema (rare in P0; common in P1+)

Never let Claude run `git commit` or `git push` — final commit is the user's manual step.
