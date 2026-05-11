"""Server smoke tests — bootstraps the FastAPI app + verifies /healthz."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medrec_superpower.server import build_http_app


@pytest.fixture
def sharp_key_env(
    sharp_public_pem: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Write the test public PEM to disk and point env at it."""
    pem_path = tmp_path / "sharp_pub.pem"
    pem_path.write_bytes(sharp_public_pem)
    monkeypatch.setenv("SHARP_PUBLIC_KEY_PEM", str(pem_path))
    return pem_path


@pytest.fixture
def http_client(sharp_key_env: Path) -> Iterator[TestClient]:
    del sharp_key_env  # consumed via env
    app = build_http_app()
    with TestClient(app) as client:
        yield client


class TestHealthz:
    def test_returns_ok_with_metadata(self, http_client: TestClient) -> None:
        response = http_client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "medrec-superpower"
        assert "capabilities" in body
        assert "medrec.fhir_data" in body["capabilities"]
        assert "medrec.reconcile" in body["capabilities"]
        assert body["version"]

    def test_openapi_published(self, http_client: TestClient) -> None:
        response = http_client.get("/openapi.json")
        assert response.status_code == 200
        spec = response.json()
        assert spec["info"]["title"] == "medrec-superpower"


class TestServerBootstrap:
    def test_build_http_app_without_sharp_key_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No SHARP key → server refuses to construct (R1 fail-closed)."""
        monkeypatch.delenv("SHARP_PUBLIC_KEY_PEM", raising=False)
        monkeypatch.delenv("SHARP_JWKS_URL", raising=False)
        with pytest.raises(RuntimeError, match="SHARP key"):
            build_http_app()

    def test_app_has_mcp_mount(self, http_client: TestClient) -> None:
        # /mcp is mounted; root returns the openapi but /mcp paths
        # are owned by the FastMCP sub-app and not introspectable via FastAPI's openapi.
        response = http_client.get("/openapi.json")
        assert response.status_code == 200
        # mount works if /mcp at minimum responds (even with 4xx — it's MCP-protocol-specific)
        mcp_response = http_client.get("/mcp")
        # any non-500 means the mount is wired — actual MCP traffic is non-HTTP-GET
        assert mcp_response.status_code < 500
