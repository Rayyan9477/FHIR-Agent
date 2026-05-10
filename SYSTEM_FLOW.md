# SYSTEM_FLOW.md — Control & Orchestration Flow

> Where [DATA_FLOW.md](DATA_FLOW.md) shows what data moves, this file shows what *decisions* are made and *who* makes them. It's the agent decision tree.

## Top-level orchestration

```
                    ┌────────────────────────┐
                    │ User message arrives   │
                    │ (Coordinator)          │
                    └──────────┬─────────────┘
                               │
                               ▼
                ┌──────────────────────────────┐
                │ Is SHARP context valid?      │
                │ (signature, expiry, scope)   │
                └──┬─────────────────┬─────────┘
                   │ no              │ yes
                   ▼                 ▼
       ┌───────────────────┐    ┌───────────────────────────┐
       │ Reject: ask user  │    │ Coordinator plans tool    │
       │ to relaunch       │    │ calls (parallel where     │
       │ workspace         │    │ possible)                 │
       └───────────────────┘    └────────────┬──────────────┘
                                             │
                                             ▼
                                ┌────────────────────────────┐
                                │ All required data present? │
                                └──┬─────────────────┬───────┘
                                   │ no              │ yes
                                   ▼                 ▼
                         ┌──────────────────┐  ┌──────────────────┐
                         │ Surface partial  │  │ Build            │
                         │ data, ask user   │  │ RegimenProposal  │
                         │ whether to       │  └────────┬─────────┘
                         │ proceed          │           │
                         └──────────────────┘           ▼
                                              ┌──────────────────┐
                                              │ Phase >= P2?     │
                                              └─┬───────────┬────┘
                                                │ yes       │ no
                                                ▼           ▼
                                  ┌────────────────────┐ ┌──────────────┐
                                  │ Hand off to        │ │ Coordinator   │
                                  │ Drug Safety        │ │ does inline   │
                                  │ Specialist         │ │ safety check  │
                                  └────────┬───────────┘ └──────┬───────┘
                                           │                    │
                                           └─────────┬──────────┘
                                                     ▼
                                          ┌────────────────────┐
                                          │ SafetyVerdict      │
                                          │ status?            │
                                          └─┬───────┬──────────┘
                                            │ hold  │ clear/caution
                                            ▼       ▼
                                ┌───────────────┐ ┌─────────────────────┐
                                │ Render        │ │ Phase >= P1?        │
                                │ escalation    │ └──┬───────────────┬──┘
                                │ card          │    │ yes           │ no
                                │ (NO daily plan│    ▼               ▼
                                └───────────────┘ ┌──────────────┐  ┌──────────────┐
                                                  │ Hand off to  │  │ Coordinator  │
                                                  │ Patient      │  │ generates    │
                                                  │ Educator     │  │ narrative    │
                                                  └──────┬───────┘  │ inline       │
                                                         │          └──────┬───────┘
                                                         └────────┬────────┘
                                                                  ▼
                                                       ┌────────────────────┐
                                                       │ Coordinator        │
                                                       │ assembles 4-card   │
                                                       │ ReconciliationReport│
                                                       └────────┬───────────┘
                                                                ▼
                                                          User sees report
```

## Coordinator decision rules

### Rule C1 — When to hand off to Drug Safety Specialist (P2+)

```
IF phase >= P2
   AND len(regimen.changes) > 0
   AND any(change.action in {START, DOSE_CHANGE, ROUTE_CHANGE} for change in regimen.changes)
THEN
   delegate to Drug Safety Specialist
ELSE IF phase < P2
   run inline safety check using check_interaction tool
```

In P0 and P1 the Coordinator does the safety check inline, in fewer tool calls. In P2 the work moves to a separate agent so the verdict carries marketplace authority.

### Rule C2 — When to hand off to Patient Educator (P1+)

```
IF phase >= P1
   AND user_role == "patient"  # SHARP-derived
   AND SafetyVerdict.status != "hold"
THEN
   delegate to Patient Educator
ELSE IF phase < P1
   generate narrative inline using a sub-prompt
ELSE
   skip narrative (clinician audience or hold-status)
```

### Rule C3 — When to ask the user a clarifying question

```
IF parse_discharge_summary returns partial=true with > 1 ambiguous entry
   OR lookup_rxnorm returns multiple candidates with similarity < 0.95
THEN
   ask user (one question, multiple choice if possible)
ELSE
   continue with best-effort and surface uncertainty in report
```

### Rule C4 — When to refuse

```
IF SHARP token expired OR signature invalid
   THEN refuse with re-launch instruction
IF user_role == "patient" AND any tool returns 403
   THEN refuse with "this conversation is for a different patient"
IF SafetyVerdict.status == "hold"
   THEN render escalation card, NEVER render daily plan
```

## Specialist decision rules

### Rule S1 — Verdict status mapping

| Condition | status |
|-----------|--------|
| no flags above `info` severity | `clear` |
| any `caution` or `warn` severity flag | `caution` |
| any `hold`-severity flag | `hold` |
| `check_interaction` failed AND no other flags | `caution` (with `required_clinician_review=true`) |

### Rule S2 — When to set `required_clinician_review=true`

- Any `hold` severity flag
- Any tool returned `check_succeeded=false`
- Any allergy cross-reactivity match
- eGFR < 30 with renally-cleared drug in regimen

## Educator decision rules

### Rule E1 — When to skip narrative for a med change

```
IF change.action == HOLD
   AND change.reason is empty
THEN
   include change in report but mark narrative as "ask your doctor about: <drug>"
```

### Rule E2 — Reading-level enforcement

```
After generating narrative:
   compute Flesch-Kincaid grade level
   IF grade > 7
      regenerate once with stricter prompt
   IF still > 7
      include warning in metadata; ship anyway
```

## Failure-mode flow

```
                    ┌────────────────────┐
                    │ Tool call fails    │
                    └─────────┬──────────┘
                              │
                              ▼
              ┌───────────────────────────┐
              │ Failure type?             │
              └─┬─────┬────────┬──────────┘
                │     │        │
                │     │        │ permission (403)
                │     │        ▼
                │     │   Refuse, surface to user
                │     │
                │     │ transient (5xx, timeout)
                │     ▼
                │  Retry once with backoff
                │  Still fails: surface as partial
                │
                │ schema validation failure
                ▼
   Retry once with stricter prompt
   Still fails: return raw + flag partial
```

## Inter-agent message contract

Every A2A message contains:

```python
{
    "from": "agent_id",
    "to": "agent_id",
    "capability": "medrec.reconcile" | "medrec.safety_review" | "patient_education.translate",
    "payload": <Pydantic schema>,
    "sharp_context": <opaque, attached by platform>,
    "request_id": "<uuid>",
    "timestamp": <iso>,
}
```

The `payload` schema is determined by the `capability` — there's a registry mapping capability → expected schema (see [SCHEMAS.md](SCHEMAS.md)).

## Idempotency

- All MCP tools are idempotent (read-only)
- A2A messages carry `request_id` so the platform can deduplicate
- Coordinator output is idempotent given the same SHARP context + user message

## Concurrency

- Coordinator parallelizes tool calls in the planning phase (T2 in [DATA_FLOW.md](DATA_FLOW.md))
- Specialist parallelizes pairwise interaction checks (`check_interaction × C(n,2)`)
- A2A handoffs are sequential — Coordinator waits for Specialist verdict before invoking Educator
