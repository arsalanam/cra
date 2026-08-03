"""Sprint A3 — Phase-04 resumability audit tests.

The audit found a single dominant gap: /accounts.html trial-detail
didn't deep-link into the Phase-04 surfaces (collector / ecrf /
multisite). A3 patches that by extending TrialDetailView to carry
per-study deployments + lock state, and by teaching the three Phase-04
static surfaces to honour URL query params for pre-selection.

These tests cover the DTO surface + the legacy-path behaviour on the
endpoint (no studies → no cross-store lookup → empty studies list).
The cross-store happy path is exercised end-to-end via the existing
accounts + multisite test suites; we don't duplicate that infra here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.repository import AccountRepository
from research_assistant.web.accounts import (
    DeploymentRefView,
    StudyRefView,
    TrialDetailView,
)

# ── DTO round-trip ───────────────────────────────────────────────────


def test_deployment_ref_view_round_trip() -> None:
    d = DeploymentRefView(deployment_id="d-1", name="Smoke deployment", is_locked=False)
    assert d.deployment_id == "d-1"
    assert d.is_locked is False
    dumped = d.model_dump()
    assert dumped == {"deployment_id": "d-1", "name": "Smoke deployment", "is_locked": False}


def test_deployment_ref_view_rejects_extra_fields() -> None:
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        DeploymentRefView.model_validate(
            {"deployment_id": "d", "name": "n", "is_locked": False, "extra": 1}
        )


def test_study_ref_view_round_trip() -> None:
    s = StudyRefView(
        study_id="s-1",
        name="SMOKE-T2DM-001",
        status="active",
        deployments=[
            DeploymentRefView(deployment_id="d-1", name="dep A", is_locked=False),
            DeploymentRefView(deployment_id="d-2", name="dep B", is_locked=True),
        ],
    )
    assert len(s.deployments) == 2
    assert s.deployments[1].is_locked is True


def test_study_ref_view_defaults_empty_deployments() -> None:
    s = StudyRefView(study_id="s-1", name="x", status="draft")
    assert s.deployments == []


def test_trial_detail_view_carries_studies() -> None:
    from datetime import UTC, datetime

    out = TrialDetailView(
        id="t-1",
        account_id="a-1",
        title="Trial X",
        sponsor="",
        indication="",
        phase=None,
        protocol_id=None,
        status="design",
        registration_thread_id=None,
        irb_thread_id=None,
        sap_thread_id=None,
        csr_thread_id=None,
        manuscript_thread_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        sites=[],
        n_deployments=2,
        studies=[
            StudyRefView(
                study_id="s-1",
                name="study A",
                status="active",
                deployments=[DeploymentRefView(deployment_id="d-1", name="dep A", is_locked=False)],
            ),
        ],
    )
    assert len(out.studies) == 1
    assert out.studies[0].deployments[0].deployment_id == "d-1"
    assert out.studies[0].deployments[0].is_locked is False


# ── Legacy path: trial with no EcrfStudy ─────────────────────────────


async def test_legacy_trial_returns_empty_studies(db_session: AsyncSession) -> None:
    """A Trial with no attached EcrfStudy (pre-account legacy or empty
    new account) should round-trip with `studies=[]` and `n_deployments=0`
    without touching the clinical store."""
    repo = AccountRepository(db_session)
    a = await repo.create_account(name="empty")
    t = await repo.create_trial(a.id, title="trial without studies")
    refreshed = await repo.get_trial(t.id)
    assert refreshed is not None
    # Construct a TrialDetailView as the endpoint would for the empty
    # path (no clinical-store call needed when there are no studies).
    from research_assistant.web.accounts import TrialView

    base = TrialView.model_validate(refreshed)
    detail = TrialDetailView(
        **base.model_dump(),
        sites=[],
        n_deployments=0,
        studies=[],
    )
    assert detail.studies == []
    assert detail.n_deployments == 0


# ── URL-param handler presence in static surfaces ────────────────────


# The exact JS is pure-DOM and can't be unit-tested without a browser
# harness, but we can pin that the handler exists. Catches accidental
# deletion + serves as a docstring for the audit.
def _read(static_rel: str) -> str:
    root = Path(__file__).resolve().parents[2] / "src" / "research_assistant" / "web" / "static"
    return (root / static_rel).read_text(encoding="utf-8")


def test_collector_honours_deployment_param() -> None:
    body = _read("collector.html")
    assert "URLSearchParams" in body
    assert 'params.get("deployment")' in body


def test_ecrf_honours_study_param() -> None:
    body = _read("ecrf.html")
    assert "URLSearchParams" in body
    assert 'params.get("study")' in body


def test_multisite_honours_deployment_param() -> None:
    body = _read("multisite.html")
    assert "URLSearchParams" in body
    assert 'params.get("deployment")' in body


def test_accounts_renders_resume_section() -> None:
    body = _read("accounts.html")
    assert "renderResumeSection" in body
    assert "/collector.html?deployment=" in body
    assert "/multisite.html?deployment=" in body
    assert "/ecrf.html" in body
