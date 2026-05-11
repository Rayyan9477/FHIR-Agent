"""Shared pytest fixtures.

The SHARP test keypair is generated once per session (RSA 2048) and shared
across all SHARP-related tests. Tests issue JWTs against this keypair via
the :func:`sharp_token_factory` fixture.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from medrec_superpower.sharp import SharpContext, StaticKeyResolver

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mark the process as a test environment.

    Production-only code paths (e.g. JWKS network fetch) check this and
    refuse to run when ``MEDREC_ENV != "production"``.
    """
    monkeypatch.setenv("MEDREC_ENV", "test")
    for var in ("ANTHROPIC_API_KEY", "NGROK_AUTHTOKEN"):
        if var in os.environ:
            monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="session")
def sharp_keypair() -> tuple[bytes, bytes]:
    """Session-scoped RSA 2048 keypair (private_pem, public_pem)."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@pytest.fixture(scope="session")
def sharp_public_pem(sharp_keypair: tuple[bytes, bytes]) -> bytes:
    return sharp_keypair[1]


@pytest.fixture(scope="session")
def sharp_private_pem(sharp_keypair: tuple[bytes, bytes]) -> bytes:
    return sharp_keypair[0]


@pytest.fixture
def sharp_resolver(sharp_public_pem: bytes) -> StaticKeyResolver:
    """Resolver pinned to the test keypair."""
    return StaticKeyResolver(sharp_public_pem)


SharpTokenFactory = Callable[..., str]


@pytest.fixture
def sharp_token_factory(sharp_private_pem: bytes) -> SharpTokenFactory:
    """Returns a callable that mints a SHARP JWT for a given claim set."""

    def _make(
        *,
        patient_id: str = "Patient/P123",
        encounter_id: str = "Encounter/E456",
        user_role: str = "patient",
        audience: str = "medrec-superpower",
        issuer: str = "promptopinion.ai",
        ttl_seconds: int = 3600,
        issued_at: datetime | None = None,
        fhir_token: str | None = "stub-fhir-token",
        override_claims: dict[str, Any] | None = None,
    ) -> str:
        now = issued_at or datetime.now(UTC)
        claims: dict[str, Any] = {
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "user_role": user_role,
            "iss": issuer,
            "aud": audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        }
        if fhir_token is not None:
            claims["fhir_token"] = fhir_token
        if override_claims:
            claims.update(override_claims)
        return jwt.encode(claims, sharp_private_pem, algorithm="RS256")

    return _make


@pytest.fixture
def sharp_context() -> SharpContext:
    """A valid SharpContext for unit tests of @requires_sharp."""
    now = datetime.now(UTC)
    return SharpContext(
        patient_id="Patient/P123",
        encounter_id="Encounter/E456",
        fhir_token="stub-fhir-token",
        user_role="patient",
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )
