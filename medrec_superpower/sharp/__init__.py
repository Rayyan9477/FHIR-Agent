"""SHARP identity package — JWT validation, decorator, redaction.

SHARP is Prompt Opinion's mechanism for propagating patient identity and
FHIR session credentials across multi-agent calls. We never accept
``patient_id`` as an LLM-controlled argument — it always comes from the
validated SHARP context. See ``docs/design/SHARP_CONTEXT.md``.
"""

from __future__ import annotations

from medrec_superpower.sharp.decorator import requires_sharp
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
    "JWKSResolver",
    "KeyResolver",
    "SharpContext",
    "SharpForbidden",
    "SharpUnauthorized",
    "StaticKeyResolver",
    "redact_processor",
    "requires_sharp",
    "validate_sharp",
]
