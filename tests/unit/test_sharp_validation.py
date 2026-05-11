"""SHARP JWT validation tests — V1, V2, V3, claim-presence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from medrec_superpower.sharp import (
    SharpContext,
    SharpUnauthorized,
    StaticKeyResolver,
    validate_sharp,
)

UTC = timezone.utc


class TestValidateSharp:
    async def test_valid_token_decodes(
        self,
        sharp_resolver: StaticKeyResolver,
        sharp_token_factory: object,
    ) -> None:
        token = sharp_token_factory()  # type: ignore[operator]
        ctx = await validate_sharp(token, key_resolver=sharp_resolver)
        assert isinstance(ctx, SharpContext)
        assert ctx.patient_id == "Patient/P123"
        assert ctx.encounter_id == "Encounter/E456"
        assert ctx.user_role == "patient"
        assert ctx.fhir_token == "stub-fhir-token"

    async def test_expired_token_rejected(
        self,
        sharp_resolver: StaticKeyResolver,
        sharp_token_factory: object,
    ) -> None:
        token = sharp_token_factory(  # type: ignore[operator]
            issued_at=datetime.now(UTC) - timedelta(hours=2), ttl_seconds=60
        )
        with pytest.raises(SharpUnauthorized, match="expired"):
            await validate_sharp(token, key_resolver=sharp_resolver)

    async def test_audience_mismatch_rejected(
        self,
        sharp_resolver: StaticKeyResolver,
        sharp_token_factory: object,
    ) -> None:
        token = sharp_token_factory(audience="some-other-mcp")  # type: ignore[operator]
        with pytest.raises(SharpUnauthorized, match="audience"):
            await validate_sharp(token, key_resolver=sharp_resolver)

    async def test_issuer_mismatch_rejected(
        self,
        sharp_resolver: StaticKeyResolver,
        sharp_token_factory: object,
    ) -> None:
        token = sharp_token_factory(issuer="evil.example.com")  # type: ignore[operator]
        with pytest.raises(SharpUnauthorized, match="issuer"):
            await validate_sharp(token, key_resolver=sharp_resolver)

    async def test_invalid_signature_rejected(
        self,
        sharp_resolver: StaticKeyResolver,
        sharp_token_factory: object,
    ) -> None:
        # Sign with a different key — validator's resolver returns the original public key.
        other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_pem = other_private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        bad_token = pyjwt.encode(
            {
                "patient_id": "Patient/P123",
                "encounter_id": "Encounter/E456",
                "iss": "promptopinion.ai",
                "aud": "medrec-superpower",
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            other_pem,
            algorithm="RS256",
        )
        with pytest.raises(SharpUnauthorized, match="signature"):
            await validate_sharp(bad_token, key_resolver=sharp_resolver)

    async def test_missing_patient_id_claim_rejected(
        self,
        sharp_resolver: StaticKeyResolver,
        sharp_private_pem: bytes,
    ) -> None:
        # Hand-craft a token missing patient_id
        now = datetime.now(UTC)
        token = pyjwt.encode(
            {
                "encounter_id": "Encounter/E456",
                "iss": "promptopinion.ai",
                "aud": "medrec-superpower",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(hours=1)).timestamp()),
            },
            sharp_private_pem,
            algorithm="RS256",
        )
        with pytest.raises(SharpUnauthorized, match="patient_id"):
            await validate_sharp(token, key_resolver=sharp_resolver)

    async def test_empty_token_rejected(
        self,
        sharp_resolver: StaticKeyResolver,
    ) -> None:
        with pytest.raises(SharpUnauthorized):
            await validate_sharp("", key_resolver=sharp_resolver)

    async def test_malformed_token_rejected(
        self,
        sharp_resolver: StaticKeyResolver,
    ) -> None:
        with pytest.raises(SharpUnauthorized, match=r"malformed|invalid"):
            await validate_sharp("not.a.jwt", key_resolver=sharp_resolver)

    async def test_clinician_role_decodes(
        self,
        sharp_resolver: StaticKeyResolver,
        sharp_token_factory: object,
    ) -> None:
        token = sharp_token_factory(user_role="clinician")  # type: ignore[operator]
        ctx = await validate_sharp(token, key_resolver=sharp_resolver)
        assert ctx.user_role == "clinician"
