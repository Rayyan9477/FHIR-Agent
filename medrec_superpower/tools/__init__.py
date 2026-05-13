"""MCP tools — each lives in its own module per CLAUDE.md naming rule."""

from __future__ import annotations

from medrec_superpower.tools.check_interaction import tool_check_interaction
from medrec_superpower.tools.get_discharge_meds import tool_get_discharge_meds
from medrec_superpower.tools.get_drug_education_handout import (
    tool_get_drug_education_handout,
)
from medrec_superpower.tools.get_patient_context import tool_get_patient_context
from medrec_superpower.tools.get_pre_admit_meds import tool_get_pre_admit_meds
from medrec_superpower.tools.lookup_rxnorm import tool_lookup_rxnorm
from medrec_superpower.tools.parse_discharge_summary import (
    tool_parse_discharge_summary,
)

__all__ = [
    "tool_check_interaction",
    "tool_get_discharge_meds",
    "tool_get_drug_education_handout",
    "tool_get_patient_context",
    "tool_get_pre_admit_meds",
    "tool_lookup_rxnorm",
    "tool_parse_discharge_summary",
]
