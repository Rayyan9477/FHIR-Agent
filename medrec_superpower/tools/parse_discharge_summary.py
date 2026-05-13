"""MCP tool: ``parse_discharge_summary``.

Extracts structured :class:`MedChangeEvent` records from the discharge
summary narrative. Bridges free-text clinical instructions ("HOLD Metformin
for 48 hours") to the structured schema the rest of the system reasons
over — without trusting the LLM to do the extraction.

R3 mechanical: the regex is the *only* place we look at discharge text.
The LLM never sees the raw narrative; it consumes the structured output.
If regex parsing finds nothing, the tool returns ``data=[]`` with
``partial=True`` so the Coordinator knows to fall back to the
pre-admit/discharge MedRequest comparison.
"""

from __future__ import annotations

import re
import time

import structlog

from medrec_superpower import errors
from medrec_superpower.fhir import FhirClient
from medrec_superpower.schemas import MedChangeAction, MedChangeEvent, ToolResult
from medrec_superpower.sharp import SharpContext, requires_sharp

logger = structlog.get_logger(__name__)

_TOOL_NAME = "parse_discharge_summary"

# Map the action verb to our MedChangeAction enum. Order matters — longer
# verbs first so "DOSE CHANGE" beats "DOSE" alone.
_ACTION_PATTERNS: list[tuple[str, MedChangeAction]] = [
    (r"DOSE\s*[-_]?\s*CHANGE", MedChangeAction.DOSE_CHANGE),
    (r"ROUTE\s*[-_]?\s*CHANGE", MedChangeAction.ROUTE_CHANGE),
    (r"RESTART", MedChangeAction.START),  # restart counts as start in our schema
    (r"HOLD", MedChangeAction.HOLD),
    (r"STOP", MedChangeAction.STOP),
    (r"DISCONTINUE", MedChangeAction.STOP),
    (r"START", MedChangeAction.START),
]

# Compiled OR of every action verb plus an immediate drug-name capture.
#
# IGNORECASE applies only to the verb alternation (via inline ``(?i:...)``)
# so we tolerate "HOLD"/"Hold"/"hold" but the drug-name pattern remains
# strictly case-sensitive — drugs in clinical text are Titlecase, and
# without this restriction the lookahead would happily eat trailing
# lowercase words like "again" / "next" into the drug name.
_ACTION_REGEX = re.compile(
    r"\b(?i:(?P<action>" + "|".join(p for p, _ in _ACTION_PATTERNS) + r"))\b"
    r"[\s:\-]+(?P<drug>[A-Z][A-Za-z\-]+(?:\s+[A-Z][A-Za-z\-]+)?)"
)

# Map verb → enum once at module load.
_ACTION_LOOKUP = {
    pat.replace("\\s*[-_]?\\s*", " ").replace("\\s+", " "): action
    for pat, action in _ACTION_PATTERNS
}


def _classify_verb(raw: str) -> MedChangeAction:
    normalised = re.sub(r"[\s\-_]+", " ", raw.strip().upper())
    if "RESTART" in normalised:
        return MedChangeAction.START
    if "DOSE" in normalised and "CHANGE" in normalised:
        return MedChangeAction.DOSE_CHANGE
    if "ROUTE" in normalised and "CHANGE" in normalised:
        return MedChangeAction.ROUTE_CHANGE
    if "DISCONTINUE" in normalised or normalised == "STOP":
        return MedChangeAction.STOP
    if normalised == "HOLD":
        return MedChangeAction.HOLD
    return MedChangeAction.START  # safe default — never silently mis-classify


def _extract_clause(text: str, match: re.Match[str]) -> str:
    """Pull the sentence containing the match for the ``reason`` field."""
    start = match.start()
    # Look backwards for sentence start: previous "." or "\n" + space.
    pre = text.rfind(".", 0, start)
    nl = text.rfind("\n", 0, start)
    sentence_start = max(pre, nl) + 1
    end = match.end()
    # Sentence end: next "." or "\n".
    next_period = text.find(".", end)
    next_nl = text.find("\n", end)
    candidates = [p for p in (next_period, next_nl) if p != -1]
    sentence_end = min(candidates) if candidates else len(text)
    return text[sentence_start:sentence_end].strip()


def parse_changes(text: str) -> list[MedChangeEvent]:
    """Extract :class:`MedChangeEvent`s from a discharge-summary narrative.

    Defensive: returns an empty list rather than partial garbage if the
    text has an unexpected shape. The Coordinator falls back to comparing
    structured Med* resources in that case.
    """
    events: list[MedChangeEvent] = []
    seen: set[tuple[str, str]] = set()
    for match in _ACTION_REGEX.finditer(text):
        verb = match.group("action")
        drug = match.group("drug").strip()
        if not drug:
            continue
        action = _classify_verb(verb)
        key = (drug.lower(), action.value)
        if key in seen:
            continue
        seen.add(key)
        reason = _extract_clause(text, match)
        events.append(
            MedChangeEvent(
                drug_name=drug,
                action=action,
                reason=reason or None,
                source="discharge_summary",
                confidence=0.75,  # regex extraction is heuristic; not 1.0
            )
        )
    return events


@requires_sharp
async def tool_parse_discharge_summary(
    *,
    sharp_context: SharpContext,
    fhir_client: FhirClient,
    patient_id: str,
    encounter_id: str,
) -> ToolResult[list[MedChangeEvent]]:
    """Return structured :class:`MedChangeEvent`s from the discharge summary."""
    del sharp_context, patient_id  # decorator-bound; not used directly
    started_at = time.perf_counter()
    try:
        text = await fhir_client.get_discharge_summary_text(encounter_id)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception(
            "tool.parse_discharge_summary.upstream_error",
            encounter_id=encounter_id,
            error=str(exc),
        )
        return ToolResult[list[MedChangeEvent]](
            ok=False,
            error=errors.upstream_error(f"FHIR read failed: {exc}"),
        )
    duration_ms = int((time.perf_counter() - started_at) * 1000)

    if not text:
        logger.info(
            "tool.parse_discharge_summary.no_document",
            tool_name=_TOOL_NAME,
            duration_ms=duration_ms,
        )
        return ToolResult[list[MedChangeEvent]](
            ok=True,
            data=[],
            partial=True,
            missing=["discharge_summary_document"],
        )

    events = parse_changes(text)
    logger.info(
        "tool.parse_discharge_summary.success",
        tool_name=_TOOL_NAME,
        event_count=len(events),
        text_length=len(text),
        duration_ms=duration_ms,
    )
    return ToolResult[list[MedChangeEvent]](
        ok=True,
        data=events,
        partial=len(events) == 0,
        missing=["med_change_events"] if not events else [],
    )


__all__ = ["parse_changes", "tool_parse_discharge_summary"]
