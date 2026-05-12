"""Drug-API result schemas (Pydantic v2)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl

from medrec_superpower.schemas import StrictModel

InteractionSeverity = Literal["high", "moderate", "low", "unknown"]


class RxNormMatch(StrictModel):
    """A single RxNav ``approximateTerm`` candidate.

    Returned by :meth:`RxNavClient.lookup_rxnorm` when resolving a free-text
    drug name to one or more RxCUIs. The caller picks by score / term type.
    """

    rxcui: str = Field(min_length=1)
    display: str = Field(min_length=1)
    score: float = Field(ge=0.0)
    term_type: str | None = None


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


__all__ = [
    "InteractionRecord",
    "InteractionResult",
    "InteractionSeverity",
    "RxNormMatch",
]
