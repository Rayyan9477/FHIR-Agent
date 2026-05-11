"""Pydantic data contracts for medrec-superpower.

Single source of truth for inter-component messages. Mirrors
``docs/design/SCHEMAS.md``. Any field change must bump ``schema_version``
on the affected top-level model (see SCHEMAS.md §Schema-evolution policy).

Clinical field names in the design doc (``eGFR``, ``LFT_AST``, ``INR``) are
expressed here in snake_case (``egfr``, ``lft_ast``, ``inr``) to satisfy
PEP 8 / ruff ``N`` rules. JSON wire format uses the snake_case names.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

T = TypeVar("T")


class StrictModel(BaseModel):
    """Base for every inter-component model.

    ``extra="forbid"`` is the security default — any unknown field at the
    boundary indicates either schema drift or an attempted injection and
    must surface as a validation error, never silently dropped.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        frozen=False,
    )


# --- Atomic types -----------------------------------------------------------


class MedChangeAction(str, Enum):
    """What happened to a medication between pre-admit and discharge."""

    START = "start"
    STOP = "stop"
    HOLD = "hold"
    DOSE_CHANGE = "dose_change"
    ROUTE_CHANGE = "route_change"
    NO_CHANGE = "no_change"


Route = Literal["PO", "IV", "IM", "SC", "TOPICAL", "OTHER"]
SourceKind = Literal["discharge_summary", "med_request", "med_statement"]


class MedRecord(StrictModel):
    """Single medication record, regardless of source resource."""

    rxcui: str = Field(min_length=1)
    display: str = Field(min_length=1)
    dose: str | None = None
    route: Route | None = None
    frequency: str | None = None
    source_resource_id: str = Field(min_length=1)
    effective_period: tuple[date, date | None] | None = None


class MedChangeEvent(StrictModel):
    """Atomic reconciliation finding for one drug."""

    drug_name: str = Field(min_length=1)
    rxcui: str | None = None
    action: MedChangeAction
    old_dose: str | None = None
    new_dose: str | None = None
    reason: str | None = None
    effective_date: date | None = None
    source: SourceKind
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


# --- Patient context --------------------------------------------------------


AllergySeverity = Literal["mild", "moderate", "severe"]
ClinicalStatus = Literal["active", "remission", "resolved"]
Sex = Literal["M", "F", "O", "U"]
PregnancyStatus = Literal["none", "pregnant", "lactating", "unknown"]


class Allergy(StrictModel):
    """Patient allergy record."""

    substance: str = Field(min_length=1)
    rxcui: str | None = None
    reaction: str | None = None
    severity: AllergySeverity | None = None


class Condition(StrictModel):
    """Patient condition record."""

    code: str = Field(min_length=1)
    display: str = Field(min_length=1)
    clinical_status: ClinicalStatus


class PatientContext(StrictModel):
    """Patient state used for safety reasoning.

    ``patient_id`` is SHARP-validated upstream — this model never receives
    an LLM-controlled value.
    """

    patient_id: str = Field(min_length=1)
    age: int = Field(ge=0, le=130)
    sex: Sex
    egfr: float | None = Field(default=None, ge=0, le=200)
    lft_ast: float | None = Field(default=None, ge=0)
    lft_alt: float | None = Field(default=None, ge=0)
    inr: float | None = Field(default=None, ge=0)
    allergies: list[Allergy] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    pregnancy_status: PregnancyStatus | None = None
    schema_version: Literal["1.0"] = "1.0"


# --- Coordinator -> Specialist ---------------------------------------------


class RegimenProposal(StrictModel):
    """Coordinator-to-Specialist message: the proposed regimen + context."""

    patient_id: str = Field(min_length=1)
    encounter_id: str = Field(min_length=1)
    pre_admit: list[MedRecord]
    discharge: list[MedRecord]
    changes: list[MedChangeEvent]
    patient_context: PatientContext
    generated_at: datetime
    schema_version: Literal["1.0"] = "1.0"


# --- Safety -----------------------------------------------------------------


SafetySeverity = Literal["info", "caution", "warn", "hold"]
SafetyCategory = Literal[
    "interaction",
    "renal",
    "hepatic",
    "allergy",
    "pregnancy",
    "duplicate_therapy",
]
SafetyStatus = Literal["clear", "caution", "hold"]


class SafetyFlag(StrictModel):
    """One safety finding."""

    severity: SafetySeverity
    category: SafetyCategory
    drugs_involved: list[str] = Field(min_length=1)
    message: str = Field(min_length=1)
    citation_url: HttpUrl


class SafetyVerdict(StrictModel):
    """Specialist-to-Coordinator: the binding verdict on a regimen.

    ``status="hold"`` is binding — see ``ReconciliationReport`` validator
    that mechanically refuses a daily plan when held (R5).
    """

    status: SafetyStatus
    flags: list[SafetyFlag] = Field(default_factory=list)
    required_clinician_review: bool
    citations: list[HttpUrl] = Field(default_factory=list)
    reviewed_at: datetime
    reviewer_agent: str = "drug_safety_specialist"
    schema_version: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def status_consistent_with_flags(self) -> SafetyVerdict:
        """A `hold` severity flag forces `hold` status."""
        if any(f.severity == "hold" for f in self.flags) and self.status != "hold":
            raise ValueError("status must be 'hold' when any flag has severity 'hold'")
        return self


# --- Patient narrative ------------------------------------------------------


DayOfWeek = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TimeOfDay = Literal["AM", "MIDDAY", "PM", "QHS", "PRN"]


class DailyPlanEntry(StrictModel):
    """One row of the patient's daily plan."""

    days: list[DayOfWeek] | Literal["all"]
    time_of_day: TimeOfDay
    drug: str = Field(min_length=1)
    rxcui: str | None = None
    dose: str = Field(min_length=1)
    notes: str | None = None


class NarrativeSection(StrictModel):
    """One drug's patient-facing explanation.

    Citations are mandatory — every patient-facing drug claim must trace to
    a MedlinePlus or FDA-label source (R4).
    """

    drug: str = Field(min_length=1)
    rxcui: str | None = None
    action_label: str = Field(min_length=1)
    text: str = Field(min_length=1)
    citations: list[HttpUrl] = Field(min_length=1)


class PatientNarrative(StrictModel):
    """Educator-to-Coordinator: 6th-grade narrative with citations."""

    sections: list[NarrativeSection] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    citations: list[HttpUrl] = Field(default_factory=list)
    reading_level_grade: float | None = Field(default=None, ge=0)
    schema_version: Literal["1.0"] = "1.0"


# --- Final report -----------------------------------------------------------


class ReconciliationReport(StrictModel):
    """Coordinator's final output to the user."""

    patient_id: str = Field(min_length=1)
    encounter_id: str = Field(min_length=1)
    generated_at: datetime
    changes: list[MedChangeEvent]
    safety: SafetyVerdict
    daily_plan: list[DailyPlanEntry] | None = None
    patient_narrative: PatientNarrative | None = None
    questions_for_doctor: list[str] = Field(default_factory=list)
    schema_version: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def hold_means_no_daily_plan(self) -> ReconciliationReport:
        """R5 mechanical: held regimens cannot ship with a daily plan."""
        if self.safety.status == "hold" and self.daily_plan is not None:
            raise ValueError("daily_plan must be None when safety.status == 'hold' (R5)")
        return self


# --- Tool result envelope ---------------------------------------------------


ErrorCode = Literal[
    "BAD_REQUEST",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "NOT_FOUND",
    "UPSTREAM_ERROR",
    "TIMEOUT",
    "INTERNAL",
    "SCHEMA_VALIDATION",
]


class ErrorEnvelope(StrictModel):
    """Structured error returned across the MCP boundary."""

    code: ErrorCode
    message: str = Field(min_length=1)
    retryable: bool


class ToolResult(BaseModel, Generic[T]):
    """Universal envelope every MCP tool returns.

    Generic ``T`` is the success-path payload type. On error ``data`` is
    ``None`` and ``error`` is populated. The ``ok_xor_error`` validator
    enforces these are mutually exclusive — never both, never neither.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    ok: bool
    data: T | None = None
    error: ErrorEnvelope | None = None
    partial: bool = False
    missing: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ok_xor_error(self) -> ToolResult[T]:
        if self.ok and self.error is not None:
            raise ValueError("ok=True but error present")
        if not self.ok and self.error is None:
            raise ValueError("ok=False requires error envelope")
        return self


# --- Capability registry ----------------------------------------------------


# Maps Prompt Opinion capability tag to the expected input schema for an agent
# that advertises that capability. Used by the Coordinator at A2A handoff to
# validate payload shape before sending — defence in depth on top of the
# platform's own routing.
CAPABILITY_SCHEMAS: dict[str, type[BaseModel]] = {
    "medrec.safety_review": RegimenProposal,
    "patient_education.translate": ReconciliationReport,
}


__all__ = [
    "CAPABILITY_SCHEMAS",
    "Allergy",
    "AllergySeverity",
    "ClinicalStatus",
    "Condition",
    "DailyPlanEntry",
    "DayOfWeek",
    "ErrorCode",
    "ErrorEnvelope",
    "MedChangeAction",
    "MedChangeEvent",
    "MedRecord",
    "NarrativeSection",
    "PatientContext",
    "PatientNarrative",
    "PregnancyStatus",
    "ReconciliationReport",
    "RegimenProposal",
    "Route",
    "SafetyCategory",
    "SafetyFlag",
    "SafetySeverity",
    "SafetyStatus",
    "SafetyVerdict",
    "Sex",
    "SourceKind",
    "StrictModel",
    "TimeOfDay",
    "ToolResult",
]
