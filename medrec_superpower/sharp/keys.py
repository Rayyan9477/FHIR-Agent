"""SHARP signing-key resolvers.

Two production-grade resolvers:

* :class:`StaticKeyResolver` — wraps a single PEM-encoded public key.
  Used in tests and during local development before a real JWKS endpoint
  is published. Production code paths refuse this resolver unless the
  ``MEDREC_ENV`` environment variable is anything other than ``"production"``.
* :class:`JWKSResolver` — fetches JSON Web Key Sets from a URL, caches them
  with a TTL, and resolves keys by ``kid``.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Protocol, cast

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey


class KeyResolver(Protocol):
    """Structural type any key resolver must satisfy."""

    async def resolve(self, kid: str | None) -> bytes:  # pragma: no cover - protocol
        """Return the PEM-encoded public key for a given ``kid``.

        :raises KeyError: when no matching key is available.
        """
        ...


class StaticKeyResolver:
    """A fixed PEM-encoded public key. Test & dev only."""

    def __init__(self, public_pem: bytes) -> None:
        if not public_pem or not public_pem.lstrip().startswith(b"-----BEGIN"):
            raise ValueError("public_pem must be PEM-encoded bytes")
        self._public_pem = public_pem
        # Refuse the static resolver in production unless explicitly overridden.
        if os.environ.get("MEDREC_ENV") == "production" and not os.environ.get(
            "MEDREC_ALLOW_STATIC_KEY"
        ):
            raise RuntimeError(
                "StaticKeyResolver is not allowed in production; "
                "use JWKSResolver or set MEDREC_ALLOW_STATIC_KEY=1 explicitly"
            )

    async def resolve(self, kid: str | None) -> bytes:
        # `kid` is intentionally ignored — a single key serves all requests.
        del kid
        return self._public_pem


class JWKSResolver:
    """Fetches JWKS from ``jwks_url`` with a TTL cache.

    Uses :class:`jwt.PyJWKClient` under the hood, wrapped in
    :func:`asyncio.to_thread` to remain non-blocking. Refreshes when the
    cache is older than ``ttl_seconds``.
    """

    def __init__(self, jwks_url: str, *, ttl_seconds: int = 3600) -> None:
        if not jwks_url.startswith("https://"):
            raise ValueError("jwks_url must be HTTPS")
        self._jwks_url = jwks_url
        self._ttl = ttl_seconds
        self._client: jwt.PyJWKClient | None = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def resolve(self, kid: str | None) -> bytes:
        if kid is None:
            raise KeyError("JWKS resolver requires a `kid` header on the token")
        async with self._lock:
            now = time.monotonic()
            if self._client is None or (now - self._fetched_at) > self._ttl:
                self._client = await asyncio.to_thread(jwt.PyJWKClient, self._jwks_url)
                self._fetched_at = now
        signing_key = await asyncio.to_thread(self._client.get_signing_key, kid)
        # We only support RS256 — cast is safe for our verify-only path.
        rsa_key = cast("RSAPublicKey", signing_key.key)
        return rsa_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )


__all__ = ["JWKSResolver", "KeyResolver", "StaticKeyResolver"]
