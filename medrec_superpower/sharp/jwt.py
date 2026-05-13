"""SHARP JWT validation.

Implements validation rules V1–V3 (signature, expiry, audience) from
``docs/design/SHARP_CONTEXT.md``. V5 (cross-patient kwarg rejection) is
enforced in :mod:`medrec_superpower.sharp.decorator`. V4 (issuer cosmetic
check) and V6 (role gating) are deferred to P1.

All validation failures map to :class:`SharpUnauthorized` (HTTP 401).
``patient_id`` mismatches at decorator time map to :class:`SharpForbidden`
(HTTP 403).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import jwt
from pydantic import Field

from medrec_superpower.schemas import StrictModel
from medrec_superpower.sharp.keys import KeyResolver

_UTC = timezone.utc

UserRole = Literal["patient", "clinician", "pharmacist"]


class SharpContext(StrictModel):
    """The validated SHARP claims set, ready for tools to consume.

    This is **only** ever produced by :func:`validate_sharp`. Tools must
    never construct one from caller-supplied data.
    """

    patient_id: str = Field(min_length=1)
    encounter_id: str = Field(min_length=1)
    fhir_token: str | None = None
    user_role: UserRole = "patient"
    issued_at: datetime
    expires_at: datetime
    issuer: str = "promptopinion.ai"
    audience: str = "medrec-superpower"


class SharpError(Exception):
    """Base class for SHARP validation errors. Never raised directly."""


class SharpUnauthorized(SharpError):
    """V1/V2/V3 failure — token bad, expired, or wrong audience. HTTP 401."""


class SharpForbidden(SharpError):
    """V5 failure — caller's args reach across SHARP scope. HTTP 403."""


_CLOCK_SKEW_SECONDS = 30
_REQUIRED_CLAIMS = ("exp", "iat", "aud", "iss", "patient_id", "encounter_id")


async def validate_sharp(
    token: str,
    *,
    key_resolver: KeyResolver,
    audience: str | None = "medrec-superpower",
    issuer: str | None = "promptopinion.ai",
) -> SharpContext:
    """Decode + validate a SHARP JWT and return a :class:`SharpContext`.

    :param token: Raw JWT string from the ``x-sharp-context`` header.
    :param key_resolver: Returns the verifying public key for a given ``kid``.
        Tests use :class:`StaticKeyResolver`; prod uses :class:`JWKSResolver`.
    :param audience: Expected ``aud`` claim (V3). Pass ``None`` to skip the
        audience check — used during the hackathon demo when Prompt Opinion
        emits a token whose ``aud`` value is platform-internal.
    :param issuer: Expected ``iss`` claim. Pass ``None`` to skip the issuer
        check (same demo escape hatch).
    :raises SharpUnauthorized: any validation failure.
    """
    if not token or not isinstance(token, str):
        raise SharpUnauthorized("empty or non-string token")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise SharpUnauthorized(f"malformed token header: {exc}") from exc

    kid = header.get("kid")
    try:
        public_key = await key_resolver.resolve(kid)
    except KeyError as exc:
        raise SharpUnauthorized(f"unknown signing key (kid={kid!r})") from exc

    # PyJWT's ``options`` parameter is typed as a TypedDict ``Options`` —
    # building it incrementally (we may or may not set ``verify_aud``/
    # ``verify_iss``) does not satisfy that shape under strict mypy, so
    # collapse it to ``object`` and silence the single arg-type check.
    options: dict[str, object] = {"require": list(_REQUIRED_CLAIMS)}
    if audience is None:
        options["verify_aud"] = False
    if issuer is None:
        options["verify_iss"] = False

    try:
        claims = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            leeway=_CLOCK_SKEW_SECONDS,
            options=options,  # type: ignore[arg-type]
        )
    except jwt.ExpiredSignatureError as exc:
        raise SharpUnauthorized("token expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise SharpUnauthorized("audience mismatch") from exc
    except jwt.InvalidIssuerError as exc:
        raise SharpUnauthorized("issuer mismatch") from exc
    except jwt.InvalidSignatureError as exc:
        raise SharpUnauthorized("signature invalid") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise SharpUnauthorized(f"missing required claim: {exc.claim}") from exc
    except jwt.InvalidTokenError as exc:
        raise SharpUnauthorized(f"invalid token: {exc}") from exc

    return SharpContext(
        patient_id=str(claims["patient_id"]),
        encounter_id=str(claims["encounter_id"]),
        fhir_token=(str(claims["fhir_token"]) if "fhir_token" in claims else None),
        user_role=claims.get("user_role", "patient"),
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=_UTC),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=_UTC),
        issuer=str(claims["iss"]),
        audience=str(claims["aud"]),
    )


__all__ = [
    "SharpContext",
    "SharpError",
    "SharpForbidden",
    "SharpUnauthorized",
    "UserRole",
    "validate_sharp",
]
