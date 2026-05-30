"""End-to-end gating + format of the /api/admin/validation-pack/* surface."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

_SECRET = "test-session-secret-must-be-long-enough-for-itsdangerous"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_assistant.persistence.clinical.database import reset_clinical_engine
    from research_assistant.persistence.database import reset_engine

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("CLINICAL_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("SESSION_COOKIE_SECRET", _SECRET)
    reset_engine()
    reset_clinical_engine()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from research_assistant.persistence.clinical.database import (
        init_clinical_db,
        reset_clinical_engine,
    )
    from research_assistant.persistence.database import init_db, reset_engine
    from research_assistant.web.app import create_app

    app = create_app()
    await init_db()
    await init_clinical_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    reset_engine()
    reset_clinical_engine()


# Note: when COGNITO_USER_POOL_ID is absent (this test deliberately does
# not set it), auth_enabled is False and AdminUser short-circuits open —
# so no login is required. That mirrors the dev/test deployment posture.


async def test_iq_pdf_returns_pdf_bytes(client: AsyncClient) -> None:
    resp = await client.get("/api/admin/validation-pack/iq.pdf?environment=pytest")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")


async def test_iq_json_carries_runtime_state(client: AsyncClient) -> None:
    resp = await client.get("/api/admin/validation-pack/iq.json?environment=pytest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["package_name"] == "research-assistant"
    assert body["deployment_environment"] == "pytest"
    assert body["database"]["dialect"] == "sqlite"


async def test_oq_pdf_refuses_409_before_first_run(client: AsyncClient) -> None:
    resp = await client.get("/api/admin/validation-pack/oq.pdf")
    assert resp.status_code == 409
    assert "no oq run" in resp.text.lower()


async def test_pq_pdf_returns_pdf_bytes(client: AsyncClient) -> None:
    resp = await client.get("/api/admin/validation-pack/pq.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")


async def test_bundle_zip_signature_and_contents(client: AsyncClient) -> None:
    resp = await client.get("/api/admin/validation-pack/bundle.zip?environment=pytest")
    assert resp.status_code == 200
    payload = resp.content
    assert payload[:4] == b"PK\x03\x04"  # ZIP signature
    z = zipfile.ZipFile(io.BytesIO(payload))
    names = set(z.namelist())
    assert "iq-installation-qualification.pdf" in names
    assert "pq-performance-qualification-runbook.pdf" in names
    # OQ skipped because no run has been recorded yet — bundle still valid.
    assert "MANIFEST.txt" in names
    manifest = z.read("MANIFEST.txt").decode("utf-8")
    assert "research-assistant" in manifest
    assert "Environment: pytest" in manifest


async def test_rtm_endpoint_returns_matrix_json(client: AsyncClient) -> None:
    resp = await client.get("/api/admin/validation-pack/rtm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"]
    codes = {r["code"] for r in body["requirements"]}
    assert "AUDIT-001" in codes
    assert "SIGN-001" in codes
