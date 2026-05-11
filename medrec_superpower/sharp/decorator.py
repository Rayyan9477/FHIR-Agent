"""``@requires_sharp`` decorator — V5 enforcement at the tool boundary.

Decorated functions must accept a ``sharp_context: SharpContext`` keyword.
At runtime, the decorator:

1. Verifies the context is present (defensive — the server middleware
   should always inject it before this layer; missing means a coding bug).
2. If the call also passes ``patient_id`` / ``encounter_id`` kwargs,
   rejects mismatches with :class:`SharpForbidden` (V5 — R1 mechanical).
3. Otherwise binds ``patient_id`` / ``encounter_id`` from the SHARP
   context into the call kwargs, so tools can read them uniformly.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar, cast

from medrec_superpower.sharp.jwt import (
    SharpContext,
    SharpForbidden,
    SharpUnauthorized,
)

P = ParamSpec("P")
R = TypeVar("R")


def requires_sharp(
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Enforce SHARP V5 on an async tool function.

    The wrapped function MUST accept ``sharp_context: SharpContext`` as a
    keyword argument. Signature is preserved across the decorator.
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        # ParamSpec.kwargs is type-opaque to mypy; cast to a plain mapping
        # for introspection. The cast does not change the underlying dict —
        # mutations here mutate the dict passed downstream.
        kw = cast("dict[str, object]", kwargs)

        sharp = kw.get("sharp_context")
        if not isinstance(sharp, SharpContext):
            raise SharpUnauthorized("missing sharp_context")

        # V5 — patient scope
        supplied_patient = kw.get("patient_id")
        if supplied_patient is not None and supplied_patient != sharp.patient_id:
            raise SharpForbidden(
                "patient_id mismatch with SHARP scope: "
                f"supplied={supplied_patient!r} sharp={sharp.patient_id!r}"
            )

        # V5 — encounter scope (same logic as patient_id)
        supplied_encounter = kw.get("encounter_id")
        if supplied_encounter is not None and supplied_encounter != sharp.encounter_id:
            raise SharpForbidden(
                "encounter_id mismatch with SHARP scope: "
                f"supplied={supplied_encounter!r} sharp={sharp.encounter_id!r}"
            )

        # Bind from SHARP if not supplied — tools read these uniformly
        kw.setdefault("patient_id", sharp.patient_id)
        kw.setdefault("encounter_id", sharp.encounter_id)

        return await fn(*args, **kwargs)

    return wrapper


__all__ = ["requires_sharp"]
