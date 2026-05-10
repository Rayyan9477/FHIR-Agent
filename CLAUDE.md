# CLAUDE.md — Project rules for Claude Code in FHIR-Agent

> This file applies to all Claude-Code-assisted work inside this repository. It overrides default behavior. Read before generating, editing, or refactoring code.

## 1. Hard rules

### NEVER commit or push
- Never run `git commit` or `git push` for any reason.
- You may run: `git add`, `git diff`, `git status`, `git log`, `git stash`, `git branch`, `git checkout`.
- Tell the user when work is ready; the user commits manually.

### Never invent drug data
- Drug interactions, dosing, and patient education content come from authoritative APIs (RxNav, openFDA, MedlinePlus) — never from the LLM.
- If a tool fails to return data, the agent must say so explicitly. Never silently substitute LLM output for tool output.
- This is the #1 safety rule. See [SAFETY.md](SAFETY.md) Rule R3.

### Never accept `patient_id` as an LLM-controlled argument
- Patient identity comes from the SHARP context only. Tools and agents read it from the context, never from a tool argument the LLM constructed.
- Cross-patient access must return HTTP 403. See [SHARP_CONTEXT.md](SHARP_CONTEXT.md).

### Never log PHI in plaintext
- All structured logs use the redaction middleware in `medrec_superpower/sharp/redact.py`.
- Any new field that might carry PHI gets added to the redaction allowlist before merge.

## 2. Language, tools, and conventions

### Stack
- **Python 3.10+** — type hints everywhere
- **`uv`** for dependency management
- **`ruff format`** for formatting, **`ruff check`** for linting
- **`mypy`** strict mode for type checking
- **`pydantic` v2** for boundary validation; **`dataclasses`** for internal data
- **`mcp`** SDK for the MCP server
- **`pytest`** + **`pytest-asyncio`** for tests
- **FastAPI** under the MCP HTTP+SSE transport
- Prefer **`Protocol`** over **`ABC`** for structural typing

### Naming and structure
- Every MCP tool gets its own file: `medrec_superpower/tools/<tool_name>.py`
- Each tool exports a single `async def tool_<name>(...)` callable
- FHIR resource adapters live in `medrec_superpower/fhir/`
- Drug knowledge clients (RxNav, openFDA, MedlinePlus) live in `medrec_superpower/drug/`
- Pydantic schemas live in **one** module: `medrec_superpower/schemas.py`

### Errors
- All MCP tools return a structured envelope:
  ```python
  {"ok": bool, "data": ..., "error": Optional[ErrorEnvelope], "partial": Optional[bool]}
  ```
- Never raise opaque exceptions across the MCP boundary.

## 3. AI-assistance guidance

### Use Sequential Thinking MCP for
- Multi-component design decisions (MCP server + agent topology)
- Debugging multi-agent SHARP propagation issues
- Trade-offs between deterministic tools vs LLM reasoning

### Use Context7 MCP for
- Looking up the **current** `mcp` SDK API (the protocol moves fast)
- Checking FHIR R4B resource shapes
- Verifying Pydantic v2 syntax (don't use v1 patterns)

### Use Explore Agent for
- Finding all callers of a tool before changing its signature
- Mapping how SHARP context flows through the codebase
- Checking which agents reference a given Pydantic schema

### Agent teams pattern
For tasks that span > 2 modules, dispatch parallel agents:
```
Task: "Add get_renal_dosing_guidance tool"
├── Agent 1 (Explore): Find all places get_patient_context is used as a reference for tool patterns
├── Agent 2 (Explore): Find current eGFR observation handling
└── After both:
    └── Agent 3 (worktree): Implement the new tool following the patterns
```

## 4. Things to avoid

- **No mocks for FHIR in integration tests.** Use HAPI sandbox or recorded fixtures, not mock objects. Mocks have hidden you from real-world FHIR weirdness many times before.
- **No "helpful" defaults that paper over missing data.** If `eGFR` is missing, the tool returns `{partial: true, missing: ["egfr"]}`. The agent decides what to do with that.
- **No silent retries.** Retries are explicit, capped, and logged.
- **No JSON schema stuffing into system prompts.** Agents output structured JSON via tool-call schema, not by trusting the LLM to follow free-text instructions.
- **No mixing of patient identifiers in logs.** One log record = one patient (or none).
- **No trailing summaries of edits.** The user reads diffs.

## 5. Hackathon-specific

- Submission requires: MCP server URL + at least one A2A agent published to Prompt Opinion Marketplace + 3-min demo video + project repo link.
- The single most-impressive thing for judges is **SHARP context propagation working**. Don't deprioritize it for features.
- Drug Safety Specialist is **P2**, not P0. Don't over-build the safety agent at the expense of finishing P0.

## 6. When in doubt

- Read the spec: `/home/rayyan9477/docs/superpowers/specs/2026-05-11-medrec-superpower-design.md`
- Read [PHASING.md](PHASING.md) — if it's not in P0 and P0 isn't done, don't build it
- Ask the user before starting destructive operations
