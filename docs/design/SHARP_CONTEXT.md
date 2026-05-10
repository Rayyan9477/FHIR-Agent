# SHARP_CONTEXT.md — SHARP Extension Specs propagation

> SHARP is Prompt Opinion's mechanism for carrying patient identity, FHIR session credentials, and encounter scope across multi-agent calls without ever putting them under LLM control.
>
> **The single most important thing for hackathon judges to see.**

---

## What SHARP is and isn't

| It IS | It ISN'T |
|-------|----------|
| A signed token (JWT) attached to every A2A and MCP call | A field the LLM constructs from chat |
| Set once, at workspace launch, by the platform | Free-form text the agent can edit |
| Validated at every tool boundary | Trusted because the agent says so |
| Propagated automatically by the Prompt Opinion runtime | Carried as a tool argument |
| The thing that lets us call MCP tools without `patient_id` arguments | A replacement for FHIR security tokens (it carries one) |

---

## Context shape

```jsonc
{
  "patient_id":   "Patient/P123",          // FHIR-style logical reference
  "encounter_id": "Encounter/E456",        // current encounter
  "fhir_token":   "<opaque session JWT>",  // for upstream FHIR calls
  "user_role":    "patient" | "clinician" | "pharmacist",
  "issued_at":    "2026-05-11T14:22:00Z",  // RFC3339
  "expires_at":   "2026-05-11T15:22:00Z",  // typically launch +1h
  "issuer":       "promptopinion.ai",      // for sig verification
  "audience":     "medrec-superpower"      // verifies tool intent
}
```

The whole structure is signed by the Prompt Opinion platform. We verify the signature on every tool call.

---

## Lifecycle

### 1. Launch (T0)

When the user opens a workspace (or a SMART-on-FHIR launch redirects in), the platform:
- Authenticates the user
- Establishes the FHIR session against the EHR (or sandbox)
- Mints the SHARP JWT with the claims above
- Stores it in the workspace runtime

Our code never produces a SHARP token; we only consume them.

### 2. Coordinator → MCP

Every MCP tool call carries the token in an HTTP header:

```http
POST /tools/get_pre_admit_meds HTTP/1.1
Host: medrec-superpower.example.com
Content-Type: application/json
x-sharp-context: eyJhbGciOi...

{}   // body has no patient_id — that's the point
```

The MCP server's `@requires_sharp` decorator validates and binds the token before any tool body runs.

### 3. Coordinator → A2A specialist

When the Coordinator hands off to the Drug Safety Specialist or Patient Educator, **the platform attaches the SHARP token to the A2A message automatically.** We don't put it in the payload.

The receiving agent reads its own SHARP context — it does not trust a `patient_id` in the payload.

### 4. Specialist → MCP

The Specialist calls MCP tools with the same SHARP token (propagated by the platform). Same `@requires_sharp` validation runs again — defense in depth.

### 5. Expiry

If the token's `expires_at` is past, every tool returns 401. The Coordinator surfaces this to the user as "session expired, please relaunch the workspace." We don't try to refresh tokens; that's the platform's job.

---

## Validation rules (executed by `@requires_sharp`)

| # | Rule | If violated |
|---|------|-------------|
| V1 | JWT signature must verify against the platform's public key | 401 |
| V2 | `expires_at` must be in the future (allow 30s clock skew) | 401 |
| V3 | `audience` must equal the MCP server's identifier (`medrec-superpower`) | 401 |
| V4 | `issuer` must be `promptopinion.ai` | 401 |
| V5 | If a tool argument names a `patient_id` and it differs from `sharp.patient_id` → reject | 403 |
| V6 | If `user_role == "patient"` and the tool requires clinician scope → reject | 403 |

---

## Safety rules (referenced from [SAFETY.md](SAFETY.md))

These compose with V1–V6:

| # | Rule |
|---|------|
| **R1** | Tool MUST return 403 if requested patient_id ≠ SHARP-bound patient_id |
| **R2** | Tool MUST NOT log PHI in plaintext; redaction middleware on stdout/stderr |
| **R3** | Drug data NEVER comes from the LLM. Tool failure → LLM must say so, not guess |
| **R4** | Patient Educator output MUST cite at least one MedlinePlus or FDA label source |
| **R5** | `SafetyVerdict.status="hold"` → Coordinator MUST refuse to render a daily plan and route to clinician escalation |

---

## What this prevents

| Attack | Prevention |
|--------|-----------|
| Prompt-injection sending the agent to fetch data on **another patient** | `patient_id` not in LLM control; SHARP-bound. V5 rejects. |
| Stolen FHIR token reused after session expiry | V2 — expiry rejection |
| Replay of a SHARP token against a different MCP server | V3 — audience mismatch |
| Fake SHARP token forged by malicious caller | V1 — signature verification |
| Patient role escalating into clinician-scoped queries | V6 — role check |
| PHI leaking into logs / error reports | R2 — redaction middleware |

---

## Implementation notes

```python
# medrec_superpower/sharp/jwt.py
from jose import jwt, JWTError

PROMPT_OPINION_PUBLIC_KEY = ...  # fetched from /.well-known JWKS

def validate_sharp(raw: str) -> SharpContext:
    try:
        claims = jwt.decode(
            raw,
            key=PROMPT_OPINION_PUBLIC_KEY,
            audience="medrec-superpower",
            issuer="promptopinion.ai",
            algorithms=["RS256"],
            options={"require": ["exp", "iss", "aud", "iat"]},
        )
    except JWTError as e:
        raise SharpUnauthorized(str(e))

    return SharpContext(
        patient_id   = claims["patient_id"],
        encounter_id = claims.get("encounter_id"),
        fhir_token   = claims["fhir_token"],
        user_role    = claims["user_role"],
        issued_at    = datetime.fromtimestamp(claims["iat"], tz=timezone.utc),
        expires_at   = datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
    )
```

```python
# medrec_superpower/sharp/decorator.py
def requires_sharp(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        raw = current_request_headers.get("x-sharp-context")
        if not raw:
            raise SharpUnauthorized("missing x-sharp-context header")
        sharp = validate_sharp(raw)

        # V5
        if "patient_id" in kwargs and kwargs["patient_id"] != sharp.patient_id:
            raise SharpForbidden("patient_id mismatch with SHARP scope")

        # bind from SHARP, never from caller
        kwargs.update(
            patient_id=sharp.patient_id,
            encounter_id=sharp.encounter_id,
            fhir_token=sharp.fhir_token,
            user_role=sharp.user_role,
        )
        return await fn(*args, **kwargs)
    return wrapper
```

> Verify the exact JWT claim names against current Prompt Opinion docs — they may use different keys than this draft.

---

## Demo callout (IMPORTANT for the video)

In the 3-minute demo video, **explicitly show**:

1. SHARP context populated at workspace launch (1 frame screenshot of context)
2. The same context flowing into MCP calls (network log or trace)
3. The same context flowing across A2A handoff (Coordinator → Specialist)

Judges will look for this. It is the differentiator from "any LLM with tools" entries. See [DEMO.md](../build/DEMO.md).
