# REFERENCES.md — External Standards, APIs, Sandboxes

> Every external dependency. Linked from the spec, the README, and the demo video credits.

---

## Hackathon

| Resource | URL |
|----------|-----|
| Devpost — Agents Assemble | https://agents-assemble.devpost.com/ |
| Hackathon resources page | https://agents-assemble.devpost.com/resources |
| Getting-started video (orientation) | https://youtu.be/Qvs_QK4meHc |
| Discord (community support) | https://discord.gg/JS2bZVruUg |
| Submission deadline | 2026-05-11, 11:00 PM EDT |

---

## Prompt Opinion platform

| Resource | URL |
|----------|-----|
| Platform homepage | https://www.promptopinion.ai/ |
| App / sign-in | https://app.promptopinion.ai |
| About / standards-first positioning | https://www.promptopinion.ai/about |
| Sample projects (GitHub org) | https://github.com/prompt-opinion |
| Parent organization | Darena Health (https://darenasolutions.com) |

> Note: SHARP Extension Specs documentation, A2A capability registry behavior, and Marketplace publishing flow specifics are NOT documented on the public landing pages — read GitHub samples + Discord for current behavior. Verify before implementing (see [RISKS.md](RISKS.md) Q1–Q7).

---

## Standards

| Standard | URL | Use here |
|----------|-----|----------|
| Model Context Protocol (MCP) | https://modelcontextprotocol.io | The Superpower track |
| MCP Python SDK | https://github.com/modelcontextprotocol/python-sdk | Server implementation |
| Agent-to-Agent (A2A) | (verify on Prompt Opinion docs) | Coordinator ↔ Specialist ↔ Educator |
| FHIR R4B | https://hl7.org/fhir/R4B/ | Clinical data interop |
| SMART on FHIR | https://hl7.org/fhir/smart-app-launch/ | Workspace launch model |

### FHIR resources we use

| Resource | URL | Tool |
|----------|-----|------|
| `Patient` | https://hl7.org/fhir/R4B/patient.html | `get_patient_context` |
| `MedicationStatement` | https://hl7.org/fhir/R4B/medicationstatement.html | `get_pre_admit_meds` |
| `MedicationRequest` | https://hl7.org/fhir/R4B/medicationrequest.html | `get_discharge_meds` |
| `MedicationDispense` | https://hl7.org/fhir/R4B/medicationdispense.html | `get_pharmacy_fill_history` |
| `AllergyIntolerance` | https://hl7.org/fhir/R4B/allergyintolerance.html | `get_patient_context` |
| `Condition` | https://hl7.org/fhir/R4B/condition.html | `get_patient_context` |
| `Observation` (eGFR/LFT/INR) | https://hl7.org/fhir/R4B/observation.html | `get_patient_context` |
| `DocumentReference` | https://hl7.org/fhir/R4B/documentreference.html | `parse_discharge_summary` |
| `Encounter` | https://hl7.org/fhir/R4B/encounter.html | encounter-scoped queries |

### Code systems

| System | Use |
|--------|-----|
| RxNorm | drug nomenclature (`rxcui`) |
| LOINC | observation codes (eGFR=33914-3, ALT=1742-6, INR=6301-6) |
| SNOMED CT | conditions, allergies |
| ICD-10 | conditions (alternative) |
| NDC | pharmacy fill records |

---

## Drug knowledge APIs

| API | URL | Use | Auth | Cost |
|-----|-----|-----|------|------|
| RxNav (NLM) | https://rxnav.nlm.nih.gov/ | `lookup_rxnorm`, `check_interaction` | none | free |
| RxNav `/approximateTerm` | https://lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.getApproximateMatch.html | drug-name normalization | none | free |
| RxNav `/interaction/list` | https://lhncbc.nlm.nih.gov/RxNav/APIs/api-Interaction.findInteractionsFromList.html | interaction lookup | none | free (deprecated 2024) → use openFDA fallback |
| openFDA Drug | https://open.fda.gov/apis/drug/ | drug labels, warnings, renal sections | none | free, rate-limited |
| openFDA Drug Label | https://open.fda.gov/apis/drug/label/ | structured label parsing | none | free |
| MedlinePlus Connect | https://medlineplus.gov/connect/overview.html | patient education handouts | none | free |
| MedlinePlus Drug Info | https://medlineplus.gov/druginfo/meds/ | URL pattern fallback | none | free |
| Surescripts (P2 only) | (commercial) | `get_pharmacy_fill_history` | API key | paid; simulate for hackathon |

### RxNav interaction API note

The free RxNav drug-interaction API was deprecated in early 2024. For P0/P1, use the older API while it's still up; for P2, plan a fallback to **openFDA drug label `drug_interactions` section** parsing. Document this in the demo: "interaction data sourced from RxNav (deprecation pending) with openFDA fallback".

---

## Sandboxes & test data

| Sandbox | URL | Use |
|---------|-----|-----|
| HAPI FHIR Public Test Server | https://hapi.fhir.org/baseR4 | live FHIR for P1+ |
| Synthea (synthetic patient generator) | https://synthea.mitre.org/ | demo + eval fixtures |
| Synthea sample data | https://synthetichealth.github.io/synthea-sample-data/ | pre-baked scenarios |
| SMART on FHIR App Gallery | https://gallery.smarthealthit.org/ | reference apps |
| Logica Sandbox | https://sandbox.logicahealth.org/ | alternative HAPI sandbox |

---

## Models

| Model | API | Use |
|-------|-----|-----|
| Claude Sonnet 4.6 | https://docs.anthropic.com/ | Coordinator, Drug Safety Specialist |
| Claude Haiku 4.5 | https://docs.anthropic.com/ | Patient Educator, parse_discharge_summary |

(Pricing and capabilities subject to change; verify in current docs.)

---

## Python ecosystem

| Library | URL | Why |
|---------|-----|-----|
| `mcp` | https://github.com/modelcontextprotocol/python-sdk | MCP server SDK |
| `pydantic` v2 | https://docs.pydantic.dev/ | Schema validation |
| `fastapi` | https://fastapi.tiangolo.com/ | HTTP transport for MCP |
| `httpx` | https://www.python-httpx.org/ | Async HTTP client |
| `tenacity` | https://tenacity.readthedocs.io/ | Retry logic |
| `fhir.resources` | https://github.com/nazrulworld/fhir.resources | FHIR R4B Python models |
| `python-jose` | https://github.com/mpdavis/python-jose | JWT validation for SHARP |
| `structlog` | https://www.structlog.org/ | Structured logging + redaction |
| `pytest` + `pytest-asyncio` | https://pytest.org | testing |
| `ruff` | https://docs.astral.sh/ruff/ | lint + format |
| `mypy` | https://mypy-lang.org/ | type checking |
| `uv` | https://docs.astral.sh/uv/ | package management |

---

## Clinical references (for Drug Safety Specialist authority)

| Reference | URL | Use |
|-----------|-----|-----|
| KDIGO Clinical Practice Guidelines | https://kdigo.org/guidelines/ | renal dosing |
| Beers Criteria (potentially inappropriate meds in elderly) | https://www.guidelinecentral.com/share/summary/64ff03dafa72b#section-society | (P3 candidate) |
| CPIC pharmacogenomics guidelines | https://cpicpgx.org/ | (post-hackathon) |
| FDA Drug Label Database | https://labels.fda.gov/ | citation source for warnings |

---

## Prior art & inspiration

| Project | Note |
|---------|------|
| `clinicalmem` | https://github.com/star-ga/clinicalmem — also targeting this hackathon, persistent clinical memory + MCP |
| Clinical scribes (Abridge, Nuance DAX) | not direct competitors, but influence patient-narrative format |
| MedRec FHIR profile | (search HL7) — informs `MedChangeEvent` shape |

---

## Glossary cross-link

See [SYSTEM_DESIGN.md](../design/SYSTEM_DESIGN.md) §Glossary for term definitions (FHIR, MCP, A2A, SHARP, RxNav, openFDA, etc.).
