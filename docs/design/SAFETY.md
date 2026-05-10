# SAFETY.md — Safety Rules & Error Handling

> Healthcare AI fails open at its peril. This document is the explicit list of every way the system is allowed to fail and what it must do.

---

## The five hard rules

| # | Rule | Enforced by |
|---|------|-------------|
| **R1** | Tool MUST return 403 if requested patient_id ≠ SHARP-bound patient_id | `@requires_sharp` decorator (V5) |
| **R2** | Tool MUST NOT log PHI in plaintext; redaction middleware on stdout/stderr | `redact_processor` in structlog config |
| **R3** | Drug data NEVER comes from the LLM. Tool failure → LLM must say so, not guess | Coordinator system prompt + Specialist authority |
| **R4** | Patient Educator output MUST cite at least one MedlinePlus or FDA label source per section | Pydantic validator on `NarrativeSection.citations` (non-empty) |
| **R5** | `SafetyVerdict.status="hold"` → Coordinator MUST refuse to render a daily plan and route to clinician escalation | Pydantic `@model_validator` on `ReconciliationReport` |

R5 is mechanically enforced — `ReconciliationReport(safety=hold, daily_plan=…)` raises `ValueError` before it can be sent to the user. See [SCHEMAS.md](SCHEMAS.md).

---

## Error-handling matrix

| Failure | Tool response | Coordinator response | User sees |
|---------|---------------|----------------------|-----------|
| FHIR 404 / partial fetch | `{ ok: true, partial: true, missing: ["..."] }` | Surface what's missing; ask user whether to continue | "I couldn't find your discharge summary. Continue with med lists only?" |
| FHIR 5xx | `{ ok: false, error: { code: UPSTREAM_ERROR, retryable: true } }` | Retry once with backoff; on second fail, surface | "FHIR is having trouble right now. Try again in a few seconds." |
| RxNav drug not found | `{ ok: true, data: { normalized: false, candidates: [...] } }` | Surface candidates, ask user to disambiguate | "Did you mean: Metformin HCl 500mg, Metformin XR 500mg…?" |
| `parse_discharge_summary` Pydantic validation fail | Retry once with stricter prompt → on second fail return raw text + `partial: true` | Mark report as "partial — clinician review required" | "I couldn't fully parse your discharge summary. Showing what I could extract." |
| `check_interaction` 5xx after retries | `{ ok: true, data: { check_succeeded: false } }` | MUST say so. Set `required_clinician_review = true` | "I couldn't verify drug interactions. Please confirm with your pharmacist." |
| SHARP token expired | `{ ok: false, error: { code: UNAUTHORIZED, retryable: false } }` (HTTP 401) | Refuse; user re-launches workspace | "Your session expired. Please relaunch from your patient portal." |
| Cross-patient access (V5) | HTTP 403 | Refuse; log alert | "This conversation is for a different patient." |
| `SafetyVerdict.status == "hold"` | n/a (Specialist returned cleanly) | Stop. Render escalation card. NO daily plan. | "⚠ Please review with your doctor before taking these. (Reason: …)" |
| Educator reading-level > grade 7 (twice) | Educator returns narrative anyway with `reading_level_grade` field | Coordinator surfaces with metadata flag; doesn't block | (no user-visible difference; logged) |
| LLM produces malformed structured output | Tool retries with stricter prompt; agent retries via Pydantic | If still malformed, fall back to inline rendering | (best-effort; never fabricates data) |

---

## What MUST NEVER happen

These would each be project-failure-grade incidents:

- LLM asserts a drug interaction not returned by `check_interaction`
- Tool returns data for a patient other than the SHARP-bound one
- PHI appears in any structured log
- Coordinator renders a daily plan when `SafetyVerdict.status == "hold"`
- Patient Educator output contains drug claims without a citation
- A tool silently retries without a cap
- A schema-validation failure is swallowed into a successful response

---

## Anti-hallucination test

```python
# tests/eval/anti_hallucination.py
"""
For each demo scenario, assert that every drug fact in the final
ReconciliationReport traces to a specific MCP tool call recorded in
the trace log. Any fact without a tool-call provenance fails.
"""
```

This runs in CI on every commit. It's the single most important test in the suite.

---

## Boundary validation summary

Per [ARCHITECTURE.md](ARCHITECTURE.md), there are four trust boundaries:

| Boundary | What we validate | What we don't trust |
|----------|------------------|---------------------|
| User → Coordinator | Prompt Opinion auth + SHARP launch | User-typed `patient_id` mentions in chat |
| Coordinator → MCP | SHARP JWT signature + audience + scope | LLM-constructed tool arguments referring to other patients |
| MCP → External | Per-source auth (FHIR token) | Cached/stale FHIR data older than 24h (we re-fetch) |
| Coordinator → Specialist/Educator | SHARP propagation by Prompt Opinion | A2A payload's `patient_id` (re-read from SHARP) |

---

## Escalation path

When `SafetyVerdict.status == "hold"` or `required_clinician_review == true`:

```
Coordinator output =
  ┌─ Hold notice ────────────────────────────────────────┐
  │ ⚠ This regimen needs clinician review before you     │
  │   self-administer. Reason: <flag.message>            │
  │                                                      │
  │ Recommended action:                                  │
  │   • Call your discharging clinician's office, or     │
  │   • Call your pharmacist                             │
  │                                                      │
  │ Citation: <flag.citation_url>                        │
  └──────────────────────────────────────────────────────┘
```

No daily plan. No 6th-grade narrative attempting to summarize the regimen as safe-to-take. Just escalation.

---

## Privacy posture

| Concern | Posture |
|---------|---------|
| PHI in logs | Redacted by `redact_processor`; allowlist-driven |
| PHI in error messages returned to caller | Permitted — error replies are over the same authenticated channel |
| PHI in LLM provider logs | Documented risk; mitigated by short-lived prompts and no PHI persistence |
| PHI in Synthea fixtures | None — synthetic data by design |
| Demo recording | Use Synthea fixture only; never real patient data |

---

## Demo failure-mode preparation

Before recording the demo video, run these scenarios to make sure the system fails *visibly*:

1. **Force a `check_interaction` 503** — confirm Coordinator says "I couldn't verify…"
2. **Force a `SafetyVerdict.status="hold"`** — confirm no daily plan rendered
3. **Submit chat with a different patient's name** — confirm SHARP scope enforcement (no exfiltration)
4. **Token expired** — confirm graceful relaunch message
5. **Educator output above grade 7** — confirm it's flagged in metadata

If any of these fails open, fix it before recording.
