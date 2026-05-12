# Architecture diagrams

High-contrast Mermaid renders, suitable for the demo deck, the README, and the
Devpost submission. Each section answers one question.

---

## 1. What does this project do?

Patient → multi-agent system → cited reconciliation report. Drug data never
originates in the model.

```mermaid
flowchart LR
    classDef user fill:#0ea5e9,stroke:#082f49,color:#fff,stroke-width:3px
    classDef agent fill:#22c55e,stroke:#14532d,color:#fff,stroke-width:3px
    classDef mcp fill:#f59e0b,stroke:#78350f,color:#000,stroke-width:3px
    classDef api fill:#ef4444,stroke:#7f1d1d,color:#fff,stroke-width:3px

    P([Patient or Clinician<br/>natural-language question]):::user
    A[Agentic AI<br/>Coordinator + Specialists + Educator]:::agent
    M[MCP Tool Layer<br/>7 deterministic tools]:::mcp
    S[(Authoritative Sources<br/>FHIR R4 + RxNav + MedlinePlus)]:::api

    P --> A
    A -- "open standards (MCP, A2A)" --> M
    M -- "no LLM-generated drug facts" --> S
    S --> M --> A
    A -- "cited 4-card report" --> P
```

### Why agents + MCP + FHIR matters for healthcare AI

| Concern | Without open standards | With this architecture |
|---|---|---|
| **Hallucinated drug facts** | "Trust the model" → unsafe at scale | Tools return structured envelopes; model never composes drug facts |
| **EHR integration** | One-off Epic / Cerner / Athena adapters | Speak FHIR R4 once; every workspace works |
| **Identity & consent** | Bespoke token handling per integration | SHARP-on-MCP + SMART scopes = the same primitive every agent uses |
| **Multi-step clinical workflow** | A single jumbo prompt with everything | A2A handoffs: Coordinator → Specialist → Educator, each with bounded authority |
| **Regulatory defensibility** | "We tested it" | Each safety rule is a Pydantic validator or middleware — auditable |

---

## 2. The multi-agent system

```mermaid
flowchart TB
    classDef user fill:#0ea5e9,stroke:#082f49,color:#fff,stroke-width:3px
    classDef coord fill:#22c55e,stroke:#14532d,color:#fff,stroke-width:3px
    classDef spec fill:#a855f7,stroke:#3b0764,color:#fff,stroke-width:3px
    classDef edu fill:#f59e0b,stroke:#78350f,color:#000,stroke-width:3px
    classDef tools fill:#06b6d4,stroke:#155e75,color:#fff,stroke-width:3px

    P([Patient]):::user
    C["<b>Reconciliation Coordinator</b><br/>(P0 · Gemini / Sonnet)<br/><br/>• Reads FHIR via MCP<br/>• Orchestrates the workflow<br/>• Renders the 4-card report"]:::coord
    S["<b>Drug Safety Specialist</b><br/>(P2 · Sonnet)<br/><br/>• Owns SafetyVerdict<br/>• status=hold is BINDING<br/>• Can block daily plan"]:::spec
    E["<b>Patient Educator</b><br/>(P1+ · Haiku)<br/><br/>• 6th-grade reading level<br/>• Mandatory citations<br/>• Never touches raw FHIR"]:::edu
    M["medrec-superpower<br/>7 MCP tools"]:::tools

    P -. natural-language question .-> C
    C ==> M
    C -. A2A: RegimenProposal .-> S
    S -. A2A: SafetyVerdict .-> C
    C -. A2A: ReconciliationReport .-> E
    E -. A2A: PatientNarrative .-> C
    C ==> P
```

**Authority boundaries are real, not aspirational:**

- Specialist's `SafetyVerdict.status="hold"` → Coordinator's `ReconciliationReport`
  Pydantic validator refuses to construct the report with a daily plan. (R5)
- Educator only consumes the structured report — never sees raw FHIR or PHI.
- Coordinator is the only agent that may call MCP tools that touch patient data.

---

## 3. SHARP context propagation

How identity flows without the LLM ever seeing it.

```mermaid
sequenceDiagram
    autonumber
    participant U as 👤 Patient
    participant PO as 🟣 Prompt Opinion
    participant C as 🟢 Coordinator (LLM)
    participant MW as 🟠 ASGI middleware
    participant MCP as 🟡 medrec-superpower
    participant F as 🔴 Workspace FHIR

    U->>PO: "Should I take Metformin?"
    PO->>C: User message (no patient_id)
    Note over PO,C: 🛡️ LLM never sees identity.<br/>Tool signature is literally get_pre_admit_meds()
    C->>MW: HTTP POST /mcp/ tools/call
    Note over PO,MW: ✉️ X-Patient-ID<br/>X-FHIR-Server-URL<br/>X-FHIR-Access-Token
    MW->>MW: Capture into ContextVar
    MW->>MCP: Route to tool handler
    MCP->>MCP: current_request_context()
    MCP->>F: GET /MedicationRequest?patient=<id><br/>Authorization: Bearer <token>
    F-->>MCP: FHIR Bundle
    MCP-->>C: ToolResult{ok, data, error, partial, missing}
    C-->>U: Reconciliation report with citations
```

**Capability handshake** (one-time at MCP server registration):

```mermaid
sequenceDiagram
    autonumber
    participant U as 👤 Operator
    participant PO as 🟣 Prompt Opinion
    participant MCP as 🟡 medrec-superpower

    U->>PO: Add MCP server (URL + name)
    PO->>MCP: initialize
    MCP-->>PO: capabilities.extensions["ai.promptopinion/fhir-context"]<br/>= {scopes: [Patient.rs, MedicationRequest.rs, ...]}
    PO->>U: "This MCP server requests these scopes. Approve?"
    U->>PO: Approve
    PO->>PO: From now on, inject X-FHIR-* headers on every tool call
```

---

## 4. The MCP tool layer

7 tools, 3 authoritative sources, 0 LLM-originated drug facts.

```mermaid
flowchart LR
    classDef tool fill:#06b6d4,stroke:#155e75,color:#fff,stroke-width:3px
    classDef local fill:#a855f7,stroke:#3b0764,color:#fff,stroke-width:3px
    classDef api fill:#ef4444,stroke:#7f1d1d,color:#fff,stroke-width:3px

    subgraph Tools[7 MCP Tools]
        direction TB
        T1["<b>get_patient_context</b><br/>demographics + conditions<br/>allergies + labs"]:::tool
        T2["<b>get_pre_admit_meds</b><br/>active before encounter"]:::tool
        T3["<b>get_discharge_meds</b><br/>prescribed at discharge"]:::tool
        T4["<b>parse_discharge_summary</b><br/>regex extraction of<br/>HOLD/STOP/START events"]:::local
        T5["<b>lookup_rxnorm</b><br/>name → RxCUI candidates"]:::tool
        T6["<b>check_interaction</b><br/>drug-drug interactions"]:::tool
        T7["<b>get_drug_education_handout</b><br/>MedlinePlus URL"]:::tool
    end

    F[("<b>Workspace FHIR R4</b><br/>via Prompt Opinion<br/>━━━━━━━━<br/>Patient · Encounter<br/>Condition · AllergyIntolerance<br/>Observation · MedicationStatement<br/>MedicationRequest · DocumentReference")]:::api
    R[("<b>RxNav</b><br/>nlm.nih.gov<br/>━━━━━━━━<br/>approximateTerm<br/>interaction/list")]:::api
    L[("<b>MedlinePlus</b><br/>nlm.nih.gov<br/>━━━━━━━━<br/>curated RxCUI map<br/>+ search fallback")]:::api

    T1 --> F
    T2 --> F
    T3 --> F
    T4 --> F
    T5 --> R
    T6 --> R
    T7 --> L
```

**Every tool returns the same envelope:**

```python
ToolResult[T] {
    ok: bool                  # success XOR error
    data: T | None
    error: ErrorEnvelope | None
    partial: bool             # data may be incomplete (e.g. missing labs)
    missing: list[str]        # field names absent from data
}
```

`ok_xor_error` is a Pydantic model_validator. The model can't return
`ok=true, error=present` — it raises a `ValidationError` before the value
crosses the MCP boundary.

---

## 5. The 5 safety rules, mechanically enforced

Each rule is a piece of code, not a vibe.

```mermaid
flowchart LR
    classDef rule fill:#dc2626,stroke:#7f1d1d,color:#fff,stroke-width:3px
    classDef enforce fill:#16a34a,stroke:#14532d,color:#fff,stroke-width:3px
    classDef result fill:#0ea5e9,stroke:#082f49,color:#fff,stroke-width:2px

    R1["<b>R1</b><br/>Patient identity is<br/>SHARP-bound"]:::rule
    R2["<b>R2</b><br/>No PHI in<br/>plaintext logs"]:::rule
    R3["<b>R3</b><br/>Drug data only<br/>from authoritative APIs"]:::rule
    R4["<b>R4</b><br/>Every drug claim<br/>cites MedlinePlus / FDA"]:::rule
    R5["<b>R5</b><br/>safety hold<br/>blocks daily plan"]:::rule

    E1["@requires_sharp decorator<br/>+ ASGI middleware"]:::enforce
    E2["structlog<br/>redact_processor"]:::enforce
    E3["ToolResult.check_succeeded<br/>= false on every failure"]:::enforce
    E4["get_drug_education_handout<br/>RxCUI → URL lookup"]:::enforce
    E5["Pydantic<br/>model_validator"]:::enforce

    O1["Cross-patient kwarg<br/>→ HTTP 403"]:::result
    O2["Allowlist of<br/>14 PHI keys"]:::result
    O3["LLM cannot substitute<br/>upstream failure"]:::result
    O4["LLM cannot<br/>compose URLs"]:::result
    O5["hold + daily_plan<br/>→ ValidationError"]:::result

    R1 --> E1 --> O1
    R2 --> E2 --> O2
    R3 --> E3 --> O3
    R4 --> E4 --> O4
    R5 --> E5 --> O5
```

---

## 6. Why open standards matter for healthcare AI

```mermaid
flowchart TB
    classDef problem fill:#dc2626,stroke:#7f1d1d,color:#fff,stroke-width:3px
    classDef standard fill:#16a34a,stroke:#14532d,color:#fff,stroke-width:3px
    classDef outcome fill:#0ea5e9,stroke:#082f49,color:#fff,stroke-width:3px

    subgraph Problems[The healthcare AI problem]
        P1[40+ year FHIR<br/>data silos]:::problem
        P2[LLMs that<br/>hallucinate drugs]:::problem
        P3[Custom adapters<br/>per EHR vendor]:::problem
        P4[Identity stitched<br/>per integration]:::problem
        P5[Single-prompt<br/>jumbo agents]:::problem
    end

    subgraph Standards[Open standards stack]
        S1[<b>FHIR R4</b><br/>data interop]:::standard
        S2[<b>MCP</b><br/>tool protocol]:::standard
        S3[<b>SHARP-on-MCP</b><br/>identity injection]:::standard
        S4[<b>SMART scopes</b><br/>fine-grained authz]:::standard
        S5[<b>A2A</b><br/>agent-to-agent]:::standard
    end

    subgraph Outcomes[What this enables]
        O1[Any EHR speaks to<br/>any agent]:::outcome
        O2[Drug facts come<br/>from RxNav / FDA only]:::outcome
        O3[Specialised agents<br/>compose without glue]:::outcome
        O4[Patients control<br/>which scopes are granted]:::outcome
        O5[Regulator can<br/>audit each rule]:::outcome
    end

    P1 ==> S1 ==> O1
    P2 ==> S2 ==> O2
    P3 ==> S2 ==> O1
    P4 ==> S3 ==> O4
    P4 ==> S4 ==> O4
    P5 ==> S5 ==> O3
```

---

## 7. Request → response, one frame

The full path of a single tool call, end to end.

```mermaid
flowchart LR
    classDef user fill:#0ea5e9,stroke:#082f49,color:#fff,stroke-width:3px
    classDef po fill:#a855f7,stroke:#3b0764,color:#fff,stroke-width:3px
    classDef llm fill:#22c55e,stroke:#14532d,color:#fff,stroke-width:3px
    classDef mw fill:#f59e0b,stroke:#78350f,color:#000,stroke-width:3px
    classDef tool fill:#06b6d4,stroke:#155e75,color:#fff,stroke-width:3px
    classDef api fill:#ef4444,stroke:#7f1d1d,color:#fff,stroke-width:3px

    U([👤 User]):::user
    PO[🟣 Prompt Opinion<br/>workspace + LLM]:::po
    CO[🟢 Coordinator<br/>system prompt]:::llm
    MW1[🟠 SharpContextMiddleware<br/>headers → ContextVar]:::mw
    MW2[🟠 ExtensionsRewrite<br/>experimental → extensions]:::mw
    TW[🔵 @mcp.tool wrapper<br/>+ @requires_sharp]:::tool
    PC[🔵 PoFhirClient<br/>per-request httpx]:::tool
    F[(🔴 Workspace FHIR)]:::api
    RX[(🔴 RxNav)]:::api
    ML[(🔴 MedlinePlus)]:::api

    U -- chat --> PO -- prompt --> CO
    CO -- "tools/call" --> MW1 --> MW2 --> TW
    TW --> PC --> F
    TW --> RX
    TW --> ML
    F --> PC --> TW --> CO --> PO --> U
```

**Every box is testable in isolation.** That's why the test suite hits 85%
coverage without contrived stubs — each layer has a clean contract with the
next.
