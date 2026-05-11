"""PHI-redaction processor tests (R2 mechanical)."""

from __future__ import annotations

import json
from io import StringIO

import structlog

from medrec_superpower.sharp import PHI_KEYS, redact_processor


class TestRedactProcessor:
    def test_patient_id_redacted(self) -> None:
        event = {
            "event": "tool.called",
            "patient_id": "Patient/P123",
            "tool_name": "get_pre_admit_meds",
        }
        out = redact_processor(None, "info", event)
        assert out["patient_id"] == "<redacted>"
        assert out["tool_name"] == "get_pre_admit_meds"
        assert out["event"] == "tool.called"

    def test_known_phi_keys_redacted(self) -> None:
        event = {k: f"sensitive-{k}" for k in PHI_KEYS}
        event["safe"] = "keep-me"
        out = redact_processor(None, "info", event)
        for k in PHI_KEYS:
            assert out[k] == "<redacted>", k
        assert out["safe"] == "keep-me"

    def test_unknown_keys_passthrough(self) -> None:
        event = {"event": "x", "duration_ms": 42, "tool_name": "y"}
        out = redact_processor(None, "info", event)
        assert out == {"event": "x", "duration_ms": 42, "tool_name": "y"}

    def test_pipeline_serialises_redacted_json(self) -> None:
        """Smoke test: install in real structlog pipeline + capture output."""
        buf = StringIO()
        structlog.configure(
            processors=[
                redact_processor,
                structlog.processors.JSONRenderer(),
            ],
            logger_factory=structlog.PrintLoggerFactory(file=buf),
            cache_logger_on_first_use=False,
        )
        log = structlog.get_logger("test")
        log.info("tool.called", patient_id="Patient/P123", tool_name="get_pre_admit_meds")
        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["patient_id"] == "<redacted>"
        assert parsed["tool_name"] == "get_pre_admit_meds"
