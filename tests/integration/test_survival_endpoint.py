"""POST /api/edc/deployments/{id}/cdisc/survival/render — sandbox mocked.

Sandbox `_impl` is monkeypatched so the test runs without Docker; the
test verifies the endpoint correctly:
  • refuses 409 before any derivation
  • refuses 503 when sandbox is disabled
  • persists TLF rows (K-M figure + Cox table) with the expected
    content
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_SECRET = "test-session-secret-must-be-long-enough-for-itsdangerous"
_COGNITO_ENV = {
    "COGNITO_REGION": "us-east-1",
    "COGNITO_USER_POOL_ID": "us-east-1_TESTPOOL",
    "COGNITO_CLIENT_ID": "test-client-id",
    "COGNITO_CLIENT_SECRET": "test-client-secret",
    "COGNITO_DOMAIN": "https://cra-test.auth.us-east-1.amazoncognito.com",
    "COGNITO_REDIRECT_URI": "http://localhost:8000/auth/callback",
    "SESSION_COOKIE_SECRET": _SECRET,
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from research_assistant.persistence.clinical.database import reset_clinical_engine
    from research_assistant.persistence.database import reset_engine

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("CLINICAL_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("IMAGES_DIR", str(tmp_path / "images"))
    for k, v in _COGNITO_ENV.items():
        monkeypatch.setenv(k, v)
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


async def _seed_user(sub: str, email: str, *, role: str) -> None:
    from sqlalchemy import select

    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import User
    from research_assistant.persistence.user_repository import UserRepository

    async with get_db_session() as session:
        existing = (await session.scalars(select(User).where(User.cognito_sub == sub))).first()
        if existing is not None:
            return
        user = User(cognito_sub=sub, email=email)
        session.add(user)
        await session.flush()
        await UserRepository(session).grant_role(user.id, role, scope_type="global", scope_id=None)


def _session_cookie(sub: str, email: str) -> str:
    import time as _time

    from research_assistant.auth.session import _serializer

    return _serializer(_SECRET).dumps(
        {"sub": sub, "email": email, "expires_at": int(_time.time()) + 3600}
    )


def _login(c: AsyncClient, sub: str, email: str) -> None:
    c.cookies.set("cra_session", _session_cookie(sub, email))


async def _setup_deployment_with_data(client: AsyncClient) -> str:
    """Create a deployment with one subject + one form-instance + one
    AE so derivation produces a non-empty ADTTE."""
    await _seed_user("sub-admin", "admin@example.com", role="admin")
    _login(client, "sub-admin", "admin@example.com")

    study = await client.post("/api/ecrf/studies", json={"name": "TTE Trial"})
    assert study.status_code == 201, study.text
    study_id = study.json()["id"]

    form = await client.post(
        f"/api/ecrf/studies/{study_id}/forms",
        json={
            "name": "demographics",
            "title": "Demographics",
            "sections": [
                {
                    "id": "main",
                    "title": "Main",
                    "items": [
                        {"id": "age", "label": "Age", "data_type": "integer", "required": True},
                    ],
                }
            ],
        },
    )
    assert form.status_code == 201, form.text
    pub = await client.post(f"/api/ecrf/forms/{form.json()['id']}/publish")
    assert pub.status_code == 200, pub.text

    dep = await client.post(
        "/api/edc/deployments",
        json={"research_study_id": study_id, "name": "TTE Dep"},
    )
    dep_id = dep.json()["id"]
    site = await client.post(f"/api/edc/deployments/{dep_id}/sites", json={"name": "S"})
    subj = await client.post(
        f"/api/edc/deployments/{dep_id}/subjects",
        json={"site_id": site.json()["id"], "subject_code": "S-001"},
    )
    subj_id = subj.json()["id"]
    forms = await client.get(f"/api/edc/deployments/{dep_id}/forms")
    deployed_id = forms.json()[0]["id"]
    fi = await client.post(
        f"/api/edc/subjects/{subj_id}/forms",
        json={"deployed_form_id": deployed_id},
    )
    await client.put(
        f"/api/edc/form-instances/{fi.json()['id']}/data",
        json={"values": {"age": "40"}, "mark_complete": True},
    )

    # Seed an AE so derive_adtte produces TTAE event row.
    await client.post(
        f"/api/edc/subjects/{subj_id}/adverse-events",
        json={
            "term_text": "headache",
            "severity_grade": 2,
            "outcome": "recovering",
            "relationship_to_intervention": "possible",
            "start_date": "2026-03-15T00:00:00Z",
        },
    )
    return dep_id


async def test_render_survival_refuses_409_before_derivation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "research_assistant.tools.data_science.sandbox_exec._impl",
        _fake_sandbox_factory(),
    )
    dep_id = await _setup_deployment_with_data(client)
    resp = await client.post(f"/api/edc/deployments/{dep_id}/cdisc/survival/render")
    assert resp.status_code == 409
    assert "ADTTE" in resp.text


async def test_render_survival_refuses_503_when_sandbox_disabled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SANDBOX_ENABLED", "false")
    dep_id = await _setup_deployment_with_data(client)
    await client.post(f"/api/edc/deployments/{dep_id}/cdisc/derive")
    resp = await client.post(f"/api/edc/deployments/{dep_id}/cdisc/survival/render")
    assert resp.status_code == 503


def _fake_sandbox_factory(
    *,
    png_present: bool = True,
    cox_summary: dict[str, Any] | None = None,
):
    """Build a fake `_impl` that mimics the sandbox return shape — a
    K-M PNG written to images_dir + a cox-summary.json inlined in the
    files dict."""

    async def _fake_impl(
        code: str,
        input_data: str | None = None,
        input_format: str = "csv",
    ) -> Any:
        import json
        from pathlib import Path

        from research_assistant.config import get_settings
        from research_assistant.tools.data_science.sandbox_exec import SandboxResult

        settings = get_settings()
        images_dir = Path(settings.images_dir)
        images_dir.mkdir(parents=True, exist_ok=True)
        files: dict[str, str] = {}
        if png_present:
            stored = "abc123def456_km-ttae.png"
            (images_dir / stored).write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes")
            files["km-ttae.png"] = f"/static/sandbox-images/{stored}"
        summary = (
            cox_summary
            if cox_summary is not None
            else {
                "params": [
                    {
                        "paramcd": "TTAE",
                        "param": "Time to First AE",
                        "fitted": True,
                        "reference": "Drug A",
                        "n_events": 10,
                        "n_subjects": 20,
                        "rows": [
                            {
                                "comparison": "Drug B vs Drug A",
                                "hr": 1.42,
                                "hr_95ci_lo": 0.85,
                                "hr_95ci_hi": 2.39,
                                "p_value": 0.18,
                                "log_hr": 0.35,
                                "se_log_hr": 0.27,
                            }
                        ],
                    }
                ]
            }
        )
        files["cox-summary.json"] = json.dumps(summary)
        return SandboxResult(stdout="ok", files=files)

    return _fake_impl


async def test_render_survival_persists_km_and_cox_tlfs(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "research_assistant.tools.data_science.sandbox_exec._impl",
        _fake_sandbox_factory(),
    )
    dep_id = await _setup_deployment_with_data(client)
    derive_resp = await client.post(f"/api/edc/deployments/{dep_id}/cdisc/derive")
    assert derive_resp.status_code == 200, derive_resp.text
    assert derive_resp.json()["counts"]["adtte"] >= 1

    resp = await client.post(f"/api/edc/deployments/{dep_id}/cdisc/survival/render")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tlfs_added"] == 2

    # Verify TLFs persisted with the expected ids.
    tlfs = (await client.get(f"/api/edc/deployments/{dep_id}/cdisc/tlf")).json()
    ids = {t["tlf_id"] for t in tlfs}
    assert "f-km-ttae" in ids
    assert "t-cox-ph-summary" in ids
    km = next(t for t in tlfs if t["tlf_id"] == "f-km-ttae")
    assert km["kind"] == "figure"
    assert km["svg"].startswith("data:image/png;base64,")
    cox = next(t for t in tlfs if t["tlf_id"] == "t-cox-ph-summary")
    assert cox["kind"] == "table"
    assert "Drug B vs Drug A" in str(cox["content"]["rows"])


async def test_render_survival_idempotent(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two renders should leave the TLF set the same shape — old K-M /
    Cox rows are wiped before the new ones get written."""
    monkeypatch.setattr(
        "research_assistant.tools.data_science.sandbox_exec._impl",
        _fake_sandbox_factory(),
    )
    dep_id = await _setup_deployment_with_data(client)
    await client.post(f"/api/edc/deployments/{dep_id}/cdisc/derive")
    await client.post(f"/api/edc/deployments/{dep_id}/cdisc/survival/render")
    await client.post(f"/api/edc/deployments/{dep_id}/cdisc/survival/render")
    tlfs = (await client.get(f"/api/edc/deployments/{dep_id}/cdisc/tlf")).json()
    km = [t for t in tlfs if t["tlf_id"] == "f-km-ttae"]
    cox = [t for t in tlfs if t["tlf_id"] == "t-cox-ph-summary"]
    assert len(km) == 1
    assert len(cox) == 1


async def test_render_survival_handles_cox_skip_reason(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cox PH that didn't fit (single-arm trial) should still persist
    a Cox table TLF — with the skip_reason as the row body."""
    monkeypatch.setattr(
        "research_assistant.tools.data_science.sandbox_exec._impl",
        _fake_sandbox_factory(
            cox_summary={
                "params": [
                    {
                        "paramcd": "TTAE",
                        "param": "Time to First AE",
                        "fitted": False,
                        "skip_reason": "Single treatment arm — Cox PH requires ≥2 arms.",
                    }
                ]
            }
        ),
    )
    dep_id = await _setup_deployment_with_data(client)
    await client.post(f"/api/edc/deployments/{dep_id}/cdisc/derive")
    await client.post(f"/api/edc/deployments/{dep_id}/cdisc/survival/render")
    tlfs = (await client.get(f"/api/edc/deployments/{dep_id}/cdisc/tlf")).json()
    cox = next(t for t in tlfs if t["tlf_id"] == "t-cox-ph-summary")
    assert "Single treatment arm" in str(cox["content"]["rows"])
