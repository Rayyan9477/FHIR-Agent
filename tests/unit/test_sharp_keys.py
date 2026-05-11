"""Tests for SHARP key resolvers (input validation + safe production posture)."""

from __future__ import annotations

import pytest

from medrec_superpower.sharp.keys import JWKSResolver, StaticKeyResolver


class TestStaticKeyResolver:
    def test_pem_bytes_required(self) -> None:
        with pytest.raises(ValueError, match="PEM-encoded"):
            StaticKeyResolver(b"not pem")

    def test_empty_bytes_rejected(self) -> None:
        with pytest.raises(ValueError):
            StaticKeyResolver(b"")

    def test_production_refuses_static_resolver(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sharp_public_pem: bytes,
    ) -> None:
        monkeypatch.setenv("MEDREC_ENV", "production")
        monkeypatch.delenv("MEDREC_ALLOW_STATIC_KEY", raising=False)
        with pytest.raises(RuntimeError, match="production"):
            StaticKeyResolver(sharp_public_pem)

    def test_production_with_override_allowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sharp_public_pem: bytes,
    ) -> None:
        monkeypatch.setenv("MEDREC_ENV", "production")
        monkeypatch.setenv("MEDREC_ALLOW_STATIC_KEY", "1")
        # Should not raise.
        resolver = StaticKeyResolver(sharp_public_pem)
        assert resolver is not None

    async def test_resolve_returns_pem_regardless_of_kid(
        self, sharp_resolver: StaticKeyResolver, sharp_public_pem: bytes
    ) -> None:
        assert await sharp_resolver.resolve(None) == sharp_public_pem
        assert await sharp_resolver.resolve("any-kid") == sharp_public_pem


class TestJWKSResolver:
    def test_https_required(self) -> None:
        with pytest.raises(ValueError, match="HTTPS"):
            JWKSResolver("http://insecure.example.com/.well-known/jwks.json")

    def test_https_url_accepted(self) -> None:
        # construction only — no network call until `resolve` is awaited.
        resolver = JWKSResolver("https://app.promptopinion.ai/.well-known/jwks.json")
        assert resolver is not None

    async def test_resolve_requires_kid(self) -> None:
        resolver = JWKSResolver("https://example.com/jwks.json")
        with pytest.raises(KeyError, match="kid"):
            await resolver.resolve(None)
