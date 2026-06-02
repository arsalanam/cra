"""Sprint U3 — onboarding flow + enforcement gate tests.

Coverage:
  - compute_missing_by_role (pure-fn) dedup + role aggregation
  - is_onboarded (pure-fn) with None / unset / set profile
  - CLINICAL_WRITE_PERMS shape — covers safety, capture, signing, lock,
    lab, drug, source-doc, randomisation perm sets
  - UserAdminRepository.onboarding_snapshot_for_user + mark_onboarded
  - /api/onboarding/* router smoke + DTO round-trip
  - Static-file pins for /onboarding.html (stepper / panel / submit) +
    collector + ecrf gates
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.auth.rbac import Permission, Role
from research_assistant.persistence.models import User, UserProfile
from research_assistant.persistence.user_admin_repository import UserAdminRepository
from research_assistant.services.user_admin import (
    CLINICAL_WRITE_PERMS,
    compute_missing_by_role,
    is_onboarded,
)

# ── compute_missing_by_role ──────────────────────────────────────────


def test_missing_by_role_dedupes_repeated_roles() -> None:
    """A user with PI on two studies still sees one entry for PI."""
    missing = compute_missing_by_role(
        assignment_roles=["principal_investigator"] * 3,
        profile=None,
    )
    assert "principal_investigator" in missing
    assert len(missing) == 1


def test_missing_by_role_skips_complete_role() -> None:
    profile = UserProfile(
        user_id="u1",
        first_name="A",
        last_name="B",
    )
    missing = compute_missing_by_role(
        assignment_roles=["student"],
        profile=profile,
    )
    # Student only requires first/last + both are filled.
    assert missing == {}


def test_missing_by_role_with_no_profile() -> None:
    """No profile → every role's required fields surface."""
    missing = compute_missing_by_role(
        assignment_roles=["principal_investigator", "coordinator"],
        profile=None,
    )
    assert "principal_investigator" in missing
    assert "coordinator" in missing
    assert "medical_license_number" in missing["principal_investigator"]


# ── is_onboarded ─────────────────────────────────────────────────────


def test_is_onboarded_false_when_profile_none() -> None:
    assert is_onboarded(None) is False


def test_is_onboarded_false_when_completed_at_unset() -> None:
    profile = UserProfile(user_id="u1")
    assert is_onboarded(profile) is False


def test_is_onboarded_true_when_completed_at_set() -> None:
    profile = UserProfile(user_id="u1", onboarding_completed_at=datetime.now(UTC))
    assert is_onboarded(profile) is True


# ── CLINICAL_WRITE_PERMS ────────────────────────────────────────────


def test_clinical_write_perms_covers_data_entry_and_signing() -> None:
    assert Permission.DATA_ENTER in CLINICAL_WRITE_PERMS
    assert Permission.FORM_SIGN in CLINICAL_WRITE_PERMS
    assert Permission.CASEBOOK_SIGNOFF in CLINICAL_WRITE_PERMS
    assert Permission.STUDY_LOCK in CLINICAL_WRITE_PERMS


def test_clinical_write_perms_covers_safety() -> None:
    assert Permission.AE_RECORD in CLINICAL_WRITE_PERMS
    assert Permission.AE_CLASSIFY in CLINICAL_WRITE_PERMS
    assert Permission.SAE_REPORT in CLINICAL_WRITE_PERMS
    assert Permission.DEVIATION_RECORD in CLINICAL_WRITE_PERMS
    assert Permission.CAPA_CLOSE in CLINICAL_WRITE_PERMS


def test_clinical_write_perms_covers_lab_drug_source() -> None:
    assert Permission.LAB_UPLOAD in CLINICAL_WRITE_PERMS
    assert Permission.IP_DISPENSE in CLINICAL_WRITE_PERMS
    assert Permission.SOURCE_DOCUMENT_UPLOAD in CLINICAL_WRITE_PERMS


def test_clinical_write_perms_excludes_read_only() -> None:
    """Read-only perms are deliberately NOT gated — they don't change
    the audit story."""
    for perm in (
        Permission.LIBRARY_READ,
        Permission.AUDIT_READ,
        Permission.WATCH_READ,
        Permission.STUDY_READ,
        Permission.DATA_READ,
        Permission.SCREENING_READ,
        Permission.LAB_READ,
        Permission.PORTFOLIO_READ_ORG,
        Permission.ACCOUNT_READ,
        Permission.TRIAL_READ,
    ):
        assert perm not in CLINICAL_WRITE_PERMS, f"{perm} should be read-only, not gated"


def test_clinical_write_perms_excludes_research_skills() -> None:
    """Research-tier skill perms aren't clinical writes — meta-analysis
    drafting doesn't need an onboarded profile."""
    for perm in (
        Permission.SKILL_META_ANALYSIS,
        Permission.SKILL_GENERAL_QA,
        Permission.SKILL_NMA,
        Permission.SKILL_MANUSCRIPT_DRAFTER,
    ):
        assert perm not in CLINICAL_WRITE_PERMS


# ── UserAdminRepository onboarding helpers ──────────────────────────


async def _seed_user(db: AsyncSession, *, email: str) -> User:
    user = User(email=email)
    db.add(user)
    await db.flush()
    return user


async def test_onboarding_snapshot_returns_profile_and_assignments(
    db_session: AsyncSession,
) -> None:
    user = await _seed_user(db_session, email="x@example.com")
    repo = UserAdminRepository(db_session)
    await repo.upsert_profile(user.id, first_name="Ada", last_name="L")
    from research_assistant.persistence.models import RoleAssignment

    db_session.add(RoleAssignment(user_id=user.id, role="coordinator", scope_type="global"))
    await db_session.flush()
    profile, assignments = await repo.onboarding_snapshot_for_user(user.id)
    assert profile is not None
    assert profile.first_name == "Ada"
    assert len(assignments) == 1
    assert assignments[0].role == "coordinator"


async def test_mark_onboarded_sets_completed_timestamp(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, email="x@example.com")
    repo = UserAdminRepository(db_session)
    before = await repo.upsert_profile(user.id)
    assert before.onboarding_completed_at is None
    after = await repo.mark_onboarded(user.id)
    assert after.onboarding_completed_at is not None


# ── Router smoke ────────────────────────────────────────────────────


def test_onboarding_router_pins_5_routes() -> None:
    from research_assistant.web.onboarding import create_onboarding_router

    router = create_onboarding_router()
    paths = {r.path for r in router.routes}
    assert "/onboarding/me" in paths
    assert "/onboarding/me/profile" in paths
    assert "/onboarding/me/training-records" in paths
    assert "/onboarding/me/delegation-entries" in paths
    assert "/onboarding/me/complete" in paths


def test_onboarding_dtos_round_trip() -> None:
    from research_assistant.web.onboarding import (
        MeOut,
        ProfileIn,
        ProfileOut,
    )

    p = ProfileIn(first_name="Ada", last_name="L")
    assert p.first_name == "Ada"
    out = ProfileOut(
        user_id="u1",
        title=None,
        first_name="Ada",
        last_name="L",
        credentials=None,
        medical_license_number=None,
        medical_license_country=None,
        gcp_training_completed_date=None,
        gcp_training_provider=None,
        gcp_certificate_url=None,
        cv_url=None,
        financial_disclosure_signed_date=None,
        onboarding_completed_at=None,
    )
    assert out.first_name == "Ada"
    me_out = MeOut(
        user_id="u1",
        email="x@example.com",
        profile=out,
        assignments=[],
        missing_fields_by_role={"principal_investigator": ["medical_license_number"]},
        onboarding_required=True,
    )
    assert me_out.onboarding_required is True
    assert "principal_investigator" in me_out.missing_fields_by_role


# ── Static-file pins ─────────────────────────────────────────────────


def _read_static(name: str) -> str:
    root = Path(__file__).resolve().parents[2] / "src" / "research_assistant" / "web" / "static"
    return (root / name).read_text(encoding="utf-8")


def test_onboarding_html_exists_with_stepper() -> None:
    body = _read_static("onboarding.html")
    assert 'id="stepper"' in body
    assert "STEP_DEFS" in body
    assert "/api/onboarding/me" in body
    assert "submitComplete" in body


def test_onboarding_html_calls_complete_endpoint() -> None:
    body = _read_static("onboarding.html")
    assert "/api/onboarding/me/complete" in body
    assert "/api/onboarding/me/training-records" in body
    assert "/api/onboarding/me/delegation-entries" in body
    assert "/api/onboarding/me/profile" in body


def test_collector_has_onboarding_redirect() -> None:
    body = _read_static("collector.html")
    assert "onboarding_required" in body
    assert "/onboarding.html" in body


def test_ecrf_has_onboarding_redirect() -> None:
    body = _read_static("ecrf.html")
    assert "onboarding_required" in body
    assert "/onboarding.html" in body


def test_index_has_onboarding_banner() -> None:
    body = _read_static("index.html")
    assert "onboarding_required" in body
    assert "Onboarding incomplete" in body


# ── REQUIRED_FIELDS continues to match — sanity check ───────────────


def test_required_fields_pi_includes_financial_disclosure() -> None:
    """Onboarding wizard's Disclosure step depends on this being a
    required field for PI."""
    from research_assistant.services.user_admin import REQUIRED_FIELDS, ProfileField

    assert (
        ProfileField.FINANCIAL_DISCLOSURE_SIGNED_DATE
        in REQUIRED_FIELDS[Role.PRINCIPAL_INVESTIGATOR]
    )
    assert ProfileField.CV_URL in REQUIRED_FIELDS[Role.PRINCIPAL_INVESTIGATOR]


# ── /auth/me extension shape (via direct response dict) ─────────────


def test_auth_me_returns_onboarding_keys_shape() -> None:
    """The /auth/me endpoint returns a plain dict; verify the new keys
    are in the response shape by reading the source. Catches accidental
    removal of the U3 extension."""
    auth_py = Path(__file__).resolve().parents[2] / "src" / "research_assistant" / "web" / "auth.py"
    body = auth_py.read_text(encoding="utf-8")
    assert '"onboarding_required"' in body
    assert '"onboarding_completed_at"' in body
    assert '"missing_fields_by_role"' in body
    assert "compute_missing_by_role" in body


# ── authz.py require_permission_scoped enforces onboarding ──────────


def test_require_permission_scoped_calls_onboarding_gate() -> None:
    authz_py = (
        Path(__file__).resolve().parents[2] / "src" / "research_assistant" / "web" / "authz.py"
    )
    body = authz_py.read_text(encoding="utf-8")
    assert "CLINICAL_WRITE_PERMS" in body
    assert "is_onboarded" in body
    assert "Onboarding required" in body
