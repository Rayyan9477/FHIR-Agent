"""Validator + round-trip tests for ``medrec_superpower.schemas``.

These tests certify the four mechanical safety gates described in
``docs/design/SAFETY.md``:

* R1 — patient mismatch surfaces as ``FORBIDDEN`` (validated at the
  decorator layer in Phase 2; the error factory shape is verified here)
* R3 — drug data never substituted by LLM (verified in Phase 5 tool tests)
* R5 — held regimens cannot ship with a daily plan (the
  ``hold_means_no_daily_plan`` model_validator)
* ``ok_xor_error`` — ``ToolResult`` is never both / neither
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from medrec_superpower import errors
from medrec_superpower.schemas import (
    DailyPlanEntry,
    ErrorEnvelope,
    MedChangeAction,
    MedChangeEvent,
    MedRecord,
    PatientContext,
    ReconciliationReport,
    SafetyFlag,
    SafetySeverity,
    SafetyStatus,
    SafetyVerdict,
    ToolResult,
)

UTC = timezone.utc


# --------------------------------------------------------------------------- helpers


def _flag(severity: SafetySeverity) -> SafetyFlag:
    return SafetyFlag(
        severity=severity,
        category="interaction",
        drugs_involved=["860975"],
        message=f"test {severity}",
        citation_url="https://labels.fda.gov/example",  # type: ignore[arg-type]
    )


def _verdict(status: SafetyStatus, severities: list[SafetySeverity]) -> SafetyVerdict:
    return SafetyVerdict(
        status=status,
        flags=[_flag(s) for s in severities],
        required_clinician_review=False,
        citations=[],
        reviewed_at=datetime(2026, 5, 11, 14, 22, tzinfo=UTC),
    )


def _report(*, safety_status: SafetyStatus, with_daily_plan: bool) -> ReconciliationReport:
    return ReconciliationReport(
        patient_id="Patient/P123",
        encounter_id="Encounter/E456",
        generated_at=datetime(2026, 5, 11, 14, 22, tzinfo=UTC),
        changes=[],
        safety=_verdict(safety_status, []),
        daily_plan=(
            [
                DailyPlanEntry(
                    days="all",
                    time_of_day="AM",
                    drug="Metformin",
                    rxcui="860975",
                    dose="500mg",
                )
            ]
            if with_daily_plan
            else None
        ),
    )


# --------------------------------------------------------------------------- SafetyVerdict


class TestSafetyVerdict:
    def test_hold_status_with_hold_flag_accepted(self) -> None:
        v = _verdict("hold", ["hold"])
        assert v.status == "hold"
        assert len(v.flags) == 1

    def test_hold_flag_without_hold_status_rejected(self) -> None:
        with pytest.raises(ValidationError, match="hold"):
            _verdict("caution", ["hold"])

    def test_clear_status_with_info_flag(self) -> None:
        v = _verdict("clear", ["info"])
        assert v.status == "clear"

    def test_caution_status_with_caution_flag(self) -> None:
        v = _verdict("caution", ["caution", "warn"])
        assert len(v.flags) == 2


# --------------------------------------------------------------- ReconciliationReport (R5)


class TestReconciliationReportR5:
    def test_hold_with_daily_plan_rejected(self) -> None:
        with pytest.raises(ValidationError, match="daily_plan"):
            _report(safety_status="hold", with_daily_plan=True)

    def test_hold_without_daily_plan_accepted(self) -> None:
        r = _report(safety_status="hold", with_daily_plan=False)
        assert r.daily_plan is None

    def test_caution_with_daily_plan_accepted(self) -> None:
        r = _report(safety_status="caution", with_daily_plan=True)
        assert r.daily_plan is not None
        assert len(r.daily_plan) == 1

    def test_clear_with_daily_plan_accepted(self) -> None:
        r = _report(safety_status="clear", with_daily_plan=True)
        assert r.daily_plan is not None

    def test_clear_without_daily_plan_accepted(self) -> None:
        # User-role=clinician path returns no daily plan even when clear.
        r = _report(safety_status="clear", with_daily_plan=False)
        assert r.daily_plan is None


# --------------------------------------------------------------------------- ToolResult


class TestToolResult:
    def test_ok_with_error_rejected(self) -> None:
        with pytest.raises(ValidationError, match="error present"):
            ToolResult[object](
                ok=True,
                error=ErrorEnvelope(code="INTERNAL", message="x", retryable=False),
            )

    def test_not_ok_without_error_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires error"):
            ToolResult[object](ok=False)

    def test_ok_with_data(self) -> None:
        r: ToolResult[str] = ToolResult[str](ok=True, data="hello")
        assert r.data == "hello"
        assert r.error is None
        assert r.partial is False

    def test_partial_with_data(self) -> None:
        r: ToolResult[str] = ToolResult[str](
            ok=True, data="partial", partial=True, missing=["egfr"]
        )
        assert r.partial is True
        assert r.missing == ["egfr"]

    def test_error_path(self) -> None:
        r: ToolResult[object] = ToolResult[object](ok=False, error=errors.forbidden("denied"))
        assert r.ok is False
        assert r.error is not None
        assert r.error.code == "FORBIDDEN"


# --------------------------------------------------------------------------- error factories


class TestErrorFactories:
    @pytest.mark.parametrize(
        ("fn", "expected_code", "expected_retryable"),
        [
            (lambda: errors.forbidden("x"), "FORBIDDEN", False),
            (lambda: errors.unauthorized("x"), "UNAUTHORIZED", False),
            (lambda: errors.not_found("x"), "NOT_FOUND", False),
            (lambda: errors.bad_request("x"), "BAD_REQUEST", False),
            (lambda: errors.upstream_error("x"), "UPSTREAM_ERROR", True),
            (lambda: errors.upstream_error("x", retryable=False), "UPSTREAM_ERROR", False),
            (lambda: errors.timeout("x"), "TIMEOUT", True),
            (lambda: errors.internal("x"), "INTERNAL", False),
            (lambda: errors.schema_validation("x"), "SCHEMA_VALIDATION", False),
        ],
    )
    def test_factory(
        self,
        fn: Any,
        expected_code: str,
        expected_retryable: bool,
    ) -> None:
        env = fn()
        assert isinstance(env, ErrorEnvelope)
        assert env.code == expected_code
        assert env.retryable is expected_retryable
        assert env.message == "x"


# --------------------------------------------------------------------------- MedChangeEvent


class TestMedChangeEvent:
    def test_round_trip_json(self) -> None:
        original = MedChangeEvent(
            drug_name="Metformin",
            rxcui="860975",
            action=MedChangeAction.HOLD,
            reason="IV contrast for CT, hold 48h post-procedure",
            effective_date=date(2026, 5, 9),
            source="discharge_summary",
        )
        roundtrip = MedChangeEvent.model_validate_json(original.model_dump_json())
        assert roundtrip == original

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            MedChangeEvent(
                drug_name="X",
                action=MedChangeAction.START,
                source="med_request",
                confidence=1.5,
            )


# --------------------------------------------------------------------------- PatientContext


class TestPatientContext:
    def test_egfr_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            PatientContext(patient_id="P/123", age=64, sex="F", egfr=-1)

    def test_egfr_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            PatientContext(patient_id="P/123", age=64, sex="F", egfr=300)

    def test_egfr_normal(self) -> None:
        c = PatientContext(patient_id="P/123", age=64, sex="F", egfr=58.0)
        assert c.egfr == 58.0

    def test_age_bounds(self) -> None:
        with pytest.raises(ValidationError):
            PatientContext(patient_id="P/123", age=-1, sex="F")
        with pytest.raises(ValidationError):
            PatientContext(patient_id="P/123", age=131, sex="F")


# --------------------------------------------------------------------------- Strict extra="forbid"


class TestStrictForbid:
    def test_unknown_field_rejected_on_med_record(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            MedRecord.model_validate(
                {
                    "rxcui": "860975",
                    "display": "Metformin",
                    "source_resource_id": "x",
                    "evil": "<script>alert(1)</script>",
                }
            )


# --------------------------------------------------------------------------- JSON Schema export


class TestJsonSchemaExport:
    def test_reconciliation_report_schema_shape(self) -> None:
        schema = ReconciliationReport.model_json_schema()
        assert schema["title"] == "ReconciliationReport"
        assert "properties" in schema
        for required_field in (
            "patient_id",
            "encounter_id",
            "generated_at",
            "changes",
            "safety",
            "schema_version",
        ):
            assert required_field in schema["properties"], required_field

    def test_safety_verdict_schema_shape(self) -> None:
        schema = SafetyVerdict.model_json_schema()
        assert schema["title"] == "SafetyVerdict"
        assert "status" in schema["properties"]
        # status is a Literal — must be enumerated
        assert "enum" in schema["properties"]["status"]
        assert set(schema["properties"]["status"]["enum"]) == {"clear", "caution", "hold"}


# Import via package surface to verify __all__ exports work.
def test_med_record_import_path() -> None:
    from medrec_superpower.schemas import MedRecord as ImportedMedRecord

    assert ImportedMedRecord is MedRecord
