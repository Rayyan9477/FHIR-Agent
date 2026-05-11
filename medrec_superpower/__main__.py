"""medrec-superpower entry point.

Run with::

    uv run python -m medrec_superpower

Or via the installed script::

    medrec-superpower
"""

from __future__ import annotations

import logging
import os
import sys

import structlog
import uvicorn

from medrec_superpower.server import build_http_app
from medrec_superpower.sharp import redact_processor


def _configure_logging() -> None:
    """Configure structlog with the PHI redact processor."""
    level_name = os.environ.get("MEDREC_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def main() -> int:
    _configure_logging()
    # Default to loopback; container deployments override via MEDREC_HOST=0.0.0.0
    # (see Dockerfile ENV). This keeps `python -m medrec_superpower` safe on dev
    # machines that may not be behind a firewall.
    host = os.environ.get("MEDREC_HOST", "127.0.0.1")
    port = int(os.environ.get("MEDREC_PORT", "8765"))

    app = build_http_app()
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=None,  # use our structlog instead of uvicorn's default
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
