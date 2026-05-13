"""SHARP identity package — JWT validation, decorator, redaction, headers.

SHARP is Prompt Opinion's mechanism for propagating patient identity and
FHIR session credentials across multi-agent calls. We never accept
``patient_id`` as an LLM-controlled argument — it always comes from the
validated SHARP context. Two transports are supported:

1. **Prompt Opinion protocol** — HTTP headers per
   https://docs.promptopinion.ai/fhir-context/mcp-fhir-context. Gated by
   the ``ai.promptopinion/fhir-context`` capability extension on
   ``initialize`` (see ``server.py``). See :mod:`.headers`.
2. **Dev JWT** — RS256-signed token in ``X-Sharp-Token`` for offline use.

See ``docs/design/SHARP_CONTEXT.md``.
"""

from __future__ import annotations

from medrec_superpower.sharp.decorator import requires_sharp
from medrec_superpower.sharp.extensions_middleware import (
    InitializeResponseRewriteMiddleware,
)
from medrec_superpower.sharp.headers import (
    RequestSharpContext,
    SharpContextMiddleware,
    current_request_context,
)
from medrec_superpower.sharp.jwt import (
    SharpContext,
    SharpForbidden,
    SharpUnauthorized,
    validate_sharp,
)
from medrec_superpower.sharp.keys import (
    JWKSResolver,
    KeyResolver,
    StaticKeyResolver,
)
from medrec_superpower.sharp.redact import PHI_KEYS, redact_processor

__all__ = [
    "PHI_KEYS",
    "InitializeResponseRewriteMiddleware",
    "JWKSResolver",
    "KeyResolver",
    "RequestSharpContext",
    "SharpContext",
    "SharpContextMiddleware",
    "SharpForbidden",
    "SharpUnauthorized",
    "StaticKeyResolver",
    "current_request_context",
    "redact_processor",
    "requires_sharp",
    "validate_sharp",
]
