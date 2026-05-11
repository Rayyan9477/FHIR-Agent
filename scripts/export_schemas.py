#!/usr/bin/env python
"""Export top-level Pydantic JSON Schemas for Prompt Opinion agent configs.

The Coordinator (and future Specialist / Educator) agent YAML configs
reference these JSON Schema files as the contract for tool-call output
schemas. Run after every change to a top-level inter-agent model.

Usage::

    uv run python scripts/export_schemas.py
    uv run python scripts/export_schemas.py --out agents/schemas
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from medrec_superpower.schemas import (
    PatientNarrative,
    ReconciliationReport,
    RegimenProposal,
    SafetyVerdict,
)

SCHEMAS: dict[str, type[BaseModel]] = {
    "regimen_proposal.json": RegimenProposal,
    "safety_verdict.json": SafetyVerdict,
    "reconciliation_report.json": ReconciliationReport,
    "patient_narrative.json": PatientNarrative,
}


def export(out_dir: Path) -> list[Path]:
    """Write JSON Schemas to ``out_dir``. Returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in SCHEMAS.items():
        path = out_dir / name
        schema = model.model_json_schema()
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("agents/schemas"),
        help="output directory (default: agents/schemas)",
    )
    args = parser.parse_args()
    written = export(args.out)
    for p in written:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
