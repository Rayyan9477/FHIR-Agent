# DEMO.md — 3-Minute Demo Video Script & Production Guide

> Single most-judged artifact. Spend disproportionate effort here.

---

## Format constraints (per Devpost)

- Length: **strictly under 3 minutes** (judges stop watching at the timer)
- Must show the project running on Prompt Opinion
- Must be hosted somewhere judges can play it (YouTube unlisted preferred; Loom acceptable)
- Submission requires the URL in the Devpost form

---

## Story arc

| Beat | Goal | Time |
|------|------|------|
| Hook | Make judges sit up | 0:00–0:15 |
| Problem | Frame the pain | 0:15–0:30 |
| Architecture flash | Show 3 standards in action | 0:30–0:45 |
| The Metformin moment | Live demo | 0:45–2:00 |
| Safety verdict + report | Payoff | 2:00–2:30 |
| Marketplace + interop | Ecosystem story | 2:30–2:50 |
| Closing | What's next | 2:50–3:00 |

---

## Script (annotated)

### 0:00–0:15 — Hook

> **Voiceover** (over a single screen showing the patient's confused medication list):
> "Half of post-discharge adverse events are medication errors. Patients leave the hospital with a list like this — and no idea what changed."

**Visual**: a real-looking discharge medication list (from Synthea) shown with confusing changes — old meds crossed out in red, new meds in green, one labeled "HOLD" with no explanation. Patient question on screen: *"Should I still be taking my Metformin?"*

### 0:15–0:30 — Problem & solution framing

> **Voiceover**:
> "We built a multi-agent system that reconciles pre-admit and discharge meds, runs authoritative drug-safety checks, and explains everything in plain English. Built on MCP, A2A, and FHIR — no vendor lock-in."

**Visual**: title card — *"medrec-superpower — built on MCP + A2A + FHIR"*

### 0:30–0:45 — Architecture flash

> **Voiceover**:
> "One MCP server with [N] tools. Two collaborating A2A agents — a Coordinator and a Patient Educator. SHARP context propagates the patient ID and FHIR token across every hop, so identity is never under LLM control."

**Visual**: the architecture diagram from [ARCHITECTURE.md](ARCHITECTURE.md), animated to show data flow. Highlight SHARP context as it crosses each boundary.

### 0:45–2:00 — Live demo (the longest beat)

**Setup**: Already inside Prompt Opinion workspace, SHARP context shown at top of screen.

> **Voiceover**:
> "Here's the SHARP context — patient ID, FHIR token, encounter — all set at workspace launch."

**Action**: User types "Should I still be taking my Metformin?" and submits.

**Visual**: split screen — chat on left, network trace / agent log on right.

> **Voiceover** (during loading):
> "The Coordinator dispatches four MCP calls in parallel — pre-admit meds, discharge meds, patient context, discharge summary. Notice: every call carries the SHARP token. No patient ID typed by the LLM."

**Visual**: the four tool calls appear in the trace; SHARP token visible on each.

> **Voiceover**:
> "It finds the discharge summary says 'HOLD Metformin 48 hours after CT contrast' — that's why it's not on the new list. And the patient's eGFR is 58."

**Visual**: structured `MedChangeEvent` for Metformin highlighted; eGFR pulled from `get_patient_context`.

> **Voiceover**:
> "Drug interactions are not invented by the LLM — they come from RxNav and openFDA. This is the answer to the safety question every clinician will ask."

**Visual**: `check_interaction` calls firing, showing RxNav as the source.

### 2:00–2:30 — Safety verdict + report

**Visual**: the 4-card report renders.

> **Voiceover**:
> "The result is a four-card report. What changed and why. The safety verdict — caution, not hold, with the renal-dose adjustment cited from the FDA label. The patient's daily plan with a delayed Metformin restart at a lower dose. And the questions to ask their doctor — generated with citations from MedlinePlus."

**Visual**: zoom on each card briefly; highlight the FDA label citation and MedlinePlus URLs.

### 2:30–2:50 — Marketplace + ecosystem

**Visual**: switch to Prompt Opinion Marketplace listing.

> **Voiceover**:
> "Both the MCP server and the Coordinator are published on the Prompt Opinion Marketplace. Any agent on the platform can call our tools — by capability tag, not name — so the ecosystem grows without changing our code."

**Visual**: marketplace listing showing capability tags `medrec.fhir_data`, `medrec.reconcile`.

### 2:50–3:00 — Closing

> **Voiceover**:
> "Determinism where it matters, LLMs where they're unique. Built for the Healthcare AI Endgame."

**Visual**: title card with project name + repo URL.

---

## Production checklist

### Before recording

- [ ] All P0 / P1 features verified working end-to-end at < 20s wall clock
- [ ] Anti-hallucination eval at 100%
- [ ] Demo failure-mode tests from [SAFETY.md](SAFETY.md) all green
- [ ] Browser tabs cleaned up; no notification banners; bookmarks bar hidden
- [ ] Resolution set to 1920×1080 minimum
- [ ] Microphone test (no echo, no fan noise)
- [ ] Synthea fixture loaded — verify the patient name shown is generic (no real-PII concern)
- [ ] Marketplace listing live and reachable from clean browser session

### Recording tools

- **Loom** (fastest, hosted) — good for first cut
- **OBS Studio** + manual upload to YouTube unlisted — better quality
- Use a script teleprompter app for voiceover smoothness

### Editing

- Trim to under 3:00 — judges have a hard cutoff
- Add captions (auto-generated then proof-read)
- Music: stock track at low volume; nothing distracting
- Title card with project name + repo + Devpost URL

### Upload

- YouTube: unlisted, allow embedding
- Description: link to repo, link to Devpost submission
- Title format: *"medrec-superpower — Healthcare AI Endgame Submission"*

---

## Screenshots for Devpost gallery

In addition to the video, Devpost lets you upload images:

1. Architecture diagram (cleanest version)
2. The 4-card report screenshot
3. Marketplace listing screenshot
4. SHARP context propagation diagram
5. The Metformin discharge-summary parse (showing the structured event)
6. Eval results table (if P2)

Each image gets a 1-line caption.

---

## Pitfalls observed in past hackathon videos

- **Talking head intro** — judges have seen 200; cut to demo by 0:15
- **Architecture monologue** — keep it under 15 seconds
- **Live coding mid-demo** — never; pre-record and edit
- **Slow loading on screen** — speed up demo segments by 1.25x in editing
- **No payoff frame** — always end on the report screenshot, not the architecture diagram
- **Background music too loud** — music should be inaudible during voiceover
- **Forgetting to call out the unique thing** — judges saw 100 "agents call tools" demos; they want to see SHARP context propagation specifically

---

## Backup plan if live demo breaks

If the network or Prompt Opinion has trouble during recording:

- Pre-record the live segment from 0:45–2:30 as a separate clip
- Stitch it in during editing
- Voiceover narrates over the clip
- Keep the 0:00–0:45 and 2:30–3:00 segments live-recorded for "humanity"
