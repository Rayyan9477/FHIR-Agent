"""Drug-API result schemas (Pydantic v2)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl

from medrec_superpower.schemas import StrictModel

InteractionSeverity = Literal["high", "moderate", "low", "unknown"]


class InteractionRecord(StrictModel):
    """A single drug-drug interaction finding."""

    severity: InteractionSeverity
    description: str = Field(min_length=1)
    citations: list[HttpUrl] = Field(default_factory=list)


class InteractionResult(StrictModel):
    """Outcome of an RxNav interaction check.

    ``check_succeeded == False`` is the **R3 mechanical** anti-hallucination
    signal — when set, the Coordinator MUST tell the user "I couldn't verify
    interactions, confirm with your pharmacist" rather than infer safety.
    """

    rxcui_a: str
    rxcui_b: str
    check_succeeded: bool
    interactions: list[InteractionRecord] = Field(default_factory=list)
    error_message: str | None = None


__all__ = ["InteractionRecord", "InteractionResult", "InteractionSeverity"]
