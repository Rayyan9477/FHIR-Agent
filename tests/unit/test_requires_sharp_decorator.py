"""``@requires_sharp`` decorator tests — V5 enforcement (R1 mechanical)."""

from __future__ import annotations

import pytest

from medrec_superpower.sharp import (
    SharpContext,
    SharpForbidden,
    SharpUnauthorized,
    requires_sharp,
)


@requires_sharp
async def _example_tool(
    *,
    sharp_context: SharpContext,
    patient_id: str | None = None,
    encounter_id: str | None = None,
    extra: str = "",
) -> dict[str, object]:
    return {
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "extra": extra,
        "sharp_patient": sharp_context.patient_id,
    }


class TestRequiresSharp:
    async def test_binds_patient_and_encounter_from_sharp(
        self, sharp_context: SharpContext
    ) -> None:
        result = await _example_tool(sharp_context=sharp_context)
        assert result["patient_id"] == sharp_context.patient_id
        assert result["encounter_id"] == sharp_context.encounter_id

    async def test_matching_patient_kwarg_accepted(self, sharp_context: SharpContext) -> None:
        result = await _example_tool(
            sharp_context=sharp_context, patient_id=sharp_context.patient_id
        )
        assert result["patient_id"] == sharp_context.patient_id

    async def test_cross_patient_kwarg_rejected(self, sharp_context: SharpContext) -> None:
        with pytest.raises(SharpForbidden, match="patient_id mismatch"):
            await _example_tool(sharp_context=sharp_context, patient_id="Patient/PATTACKER")

    async def test_cross_encounter_kwarg_rejected(self, sharp_context: SharpContext) -> None:
        with pytest.raises(SharpForbidden, match="encounter_id mismatch"):
            await _example_tool(sharp_context=sharp_context, encounter_id="Encounter/EVIL")

    async def test_missing_sharp_context_rejected(self) -> None:
        with pytest.raises(SharpUnauthorized, match="missing sharp_context"):
            await _example_tool()  # type: ignore[call-arg]

    async def test_non_sharp_context_value_rejected(self) -> None:
        with pytest.raises(SharpUnauthorized):
            # Passing a wrong-typed sharp_context — defensive path.
            await _example_tool(sharp_context="not-a-context")  # type: ignore[arg-type]

    async def test_extra_kwargs_pass_through(self, sharp_context: SharpContext) -> None:
        result = await _example_tool(sharp_context=sharp_context, extra="hello")
        assert result["extra"] == "hello"
