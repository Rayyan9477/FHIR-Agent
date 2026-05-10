# RISKS.md — Risks & Open Questions

> Things we don't fully control. Each entry has a mitigation, an investigation step, or both. Update as the build progresses.

---

## Risk register

| ID | Risk | Likelihood | Impact | Mitigation / Investigation |
|----|------|:----------:|:------:|----------------------------|
| K1 | Prompt Opinion MCP transport quirks (HTTP+SSE expectations differ from spec) | Med | High | Read GitHub samples (`github.com/prompt-opinion`) and platform docs early. Test against Marketplace registration before P0 freeze. |
| K2 | SHARP Extension Specs JWT format unstable | Med | High | Verify claim names + signature algorithm in current platform docs. Treat header format as configuration; do not hard-code in tool implementations. |
| K3 | FHIR sandbox availability for live demo | Low | High | P0 uses Synthea fixture; HAPI added in P1 only after P0 ships. Have local HAPI Docker image as backup. |
| K4 | LLM cost during demo (Sonnet × multiple agents on every test run) | Med | Med | Cap demo turns; use Haiku for Educator; cache `parse_discharge_summary` outputs by content hash. |
| K5 | Discharge-summary parsing accuracy on free text | High | Med | Pydantic-validated retry loop; surface "partial" rather than fabricate. Pre-record a clean discharge summary in the demo fixture. |
| K6 | RxNav rate limits | Low | Med | Free tier ~20 req/sec; add `tenacity` retry + 24h local cache (P2). For P0 demo, single-patient load is fine. |
| K7 | A2A handoff latency | Low | Low | Profile in P1; parallelize Coordinator's safety + education handoffs where possible. |
| K8 | Marketplace publishing process timing | Med | High | Submit P0 to Marketplace early in the build, not last. Resolve any review delay. |
| K9 | Prompt Opinion platform outage during recording | Low | Med | Have a pre-recorded backup of the live demo segment. |
| K10 | Judges interpret "AI Factor" criterion as "novel ML model" | Med | Med | Demo voiceover explicitly names what only an LLM can do here (free-text parsing, drug-name normalization, 6th-grade narrative) — leans into the criterion. |
| K11 | Patient Educator output drifts above 6th-grade reading level | Med | Low | Flesch-Kincaid post-check; regenerate once. Acceptable to ship slightly above grade 7 with a metadata flag (logged, not user-visible). |
| K12 | Anti-hallucination check is too strict and blocks legitimate paraphrasing | Low | Med | Provenance is at the *fact* level, not the word level. Educator can rephrase; it can't introduce new drug claims. Test cases include paraphrasing. |
| K13 | MedlinePlus Connect API changes URL or schema | Low | Low | Pin to a known API version; have a fallback to `medlineplus.gov/druginfo/meds/` URL pattern. |
| K14 | Marketplace requires HTTPS endpoint, dev environment uses HTTP | High | Med | Use `ngrok http` for development; document the URL in Marketplace listing. Production uses Caddy/Nginx + Let's Encrypt. |
| K15 | Pydantic model validation fails on unexpected FHIR fields | Med | Low | Use `model_config = ConfigDict(extra="ignore")` on FHIR-derived models; let unknown fields pass through. |
| K16 | Demo recording exposes accidental real-looking PHI | Med | Critical | Use Synthea exclusively; verify patient name doesn't match any real person; use distinctively synthetic names like "Aaron Rodriguez Synthea-7". |
| K17 | Demo timer overrun (> 3 min) | Med | Med | Edit aggressively; cut hook to 10s if needed; speed up demo segment 1.25x in editing. |

---

## Open questions

### Q1. Does Prompt Opinion's SHARP token use opaque JWT or a custom format?

**Why it matters**: validation code shape depends on this.
**Investigation**: read platform docs + samples; ask in Discord (`https://discord.gg/JS2bZVruUg`).
**Decision needed by**: end of P0.

### Q2. How does Prompt Opinion handle MCP tool registration — manual marketplace UI, CLI, or API?

**Why it matters**: affects build automation and submission timeline.
**Investigation**: marketplace docs; sample repos.
**Decision needed by**: midway through P0.

### Q3. Does A2A in Prompt Opinion expose capability tags for runtime discovery, or are agent IDs hardcoded?

**Why it matters**: P2 capability registry depends on this.
**Investigation**: A2A spec section in platform docs.
**Decision needed by**: start of P2 (not blocking P0 / P1).

### Q4. Is the FHIR sandbox patient set reset periodically?

**Why it matters**: long-running demo could lose data; need to know whether to recreate patients on each session.
**Investigation**: HAPI sandbox docs; or run our own local HAPI.
**Decision needed by**: P1.

### Q5. Are there judge expectations for an "innovation" angle beyond MCP/A2A/FHIR usage?

**Why it matters**: shapes story arc in demo video.
**Investigation**: judge bios; past Prompt Opinion / Darena Health hackathons.
**Decision needed by**: before recording.

### Q6. Will Prompt Opinion provide a hosted MCP gateway, or do submitters self-host?

**Why it matters**: deployment topology + URL stability.
**Investigation**: platform docs.
**Decision needed by**: P0 finalization.

### Q7. Can multiple A2A agents share the same MCP server, or does each agent need its own?

**Why it matters**: scoping of marketplace listings.
**Investigation**: A2A spec.
**Decision needed by**: P2.

### Q8. What's the patient-clinician toggle UX in Prompt Opinion?

**Why it matters**: `user_role` claim in SHARP needs to map to a user-facing concept; affects which tools/features are exposed.
**Investigation**: workspace docs.
**Decision needed by**: P1.

---

## Risk-reduction checklist (run before P0 freeze)

```
[ ] K1: MCP server registers cleanly in Marketplace (not just runs locally)
[ ] K2: SHARP token format verified; sample valid + invalid tokens in fixtures
[ ] K3: Synthea fixture yields complete data without external dependencies
[ ] K8: Marketplace listing approved (not just submitted)
[ ] K14: HTTPS endpoint accessible from public internet
[ ] K16: All Synthea fixtures verified PHI-safe with distinct names
```

If any of these is unchecked at P0 freeze, hold the submission and resolve.

---

## Things explicitly accepted as risks (no mitigation)

| Risk | Why accepted |
|------|--------------|
| LLM cost during eval suite | Acceptable for hackathon scope; production would batch + cache |
| Single-region MCP deployment | Demo doesn't need geo-redundancy |
| No load testing | Demo is single-user; scaling is P3 problem |
| Synthea data not perfectly reflective of real EHR weirdness | Acceptable trade-off vs PHI-safety guarantee |
| English-only patient narrative | Multi-language is post-hackathon |
