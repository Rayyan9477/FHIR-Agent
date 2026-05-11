"""PHI redaction for ``structlog``.

The :func:`redact_processor` is mounted in the structlog pipeline. It walks
each log event and replaces any value whose key matches the PHI allowlist
with the literal ``"<redacted>"``. The allowlist is intentionally broad —
adding a key here is cheap, missing one risks an R2 incident.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Final

PHI_KEYS: Final[frozenset[str]] = frozenset(
    {
        "patient_id",
        "encounter_id",
        "mrn",
        "name",
        "given",
        "family",
        "dob",
        "date_of_birth",
        "address",
        "phone",
        "email",
        "ssn",
        "fhir_token",
        "sharp_context",
        "authorization",
        "x_sharp_context",
    }
)

_REDACTED = "<redacted>"


def redact_processor(
    _logger: object,
    _method_name: str,
    event_dict: MutableMapping[str, object],
) -> MutableMapping[str, object]:
    """structlog processor — mutates ``event_dict`` to redact PHI by key.

    Returns the same dict for chaining (structlog convention).
    """
    for key in list(event_dict.keys()):
        if key in PHI_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


__all__ = ["PHI_KEYS", "redact_processor"]
