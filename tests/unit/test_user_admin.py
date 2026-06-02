"""Sprint U1 — user-administration module tests.

Coverage:
  - Matrices (required-fields per role + SoD pair invariants)
  - detect_grant_conflicts: scoped match, study-id mismatch, non-pair
  - missing_required_fields: None profile, partially-filled, fully-filled
  - UserAdminRepository: profile upsert, suspend/reactivate, scoped invitation
    round-trip with assignments_json + roles_json fallback, training records,
    delegation entries
  - resolve_login consumes assignments_json (new path) + falls back to
    roles_json (legacy path)
  - Router-factory smoke pin on the 16 routes
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.auth.rbac import Role, ScopeType
from research_assistant.persistence.models import (
    DelegationLogEntry,
    PendingInvitation,
    TrainingRecord,
    User,
    UserProfile,
)
from research_assistant.persistence.user_admin_repository import (
    UserAdminError,
    UserAdminRepository,
)
from research_assistant.persistence.user_repository import (
    UserRepository,
    _parse_scoped_assignments,
)
from research_assistant.services.user_admin import (
    REQUIRED_FIELDS,
    ROLE_CATALOGUE,
    SAME_STUDY_CONFLICTS,
    ProfileField,
    detect_grant_conflicts,
    missing_required_fields,
)

# ── Matrices ─────────────────────────────────────────────────────────


def test_pi_requires_license_and_gcp_and_financial_disclosure() -> None:
    fields = REQUIRED_FIELDS[Role.PRINCIPAL_INVESTIGATOR]
    assert ProfileField.MEDICAL_LICENSE_NUMBER in fields
    assert ProfileField.MEDICAL_LICENSE_COUNTRY in fields
    assert ProfileField.GCP_TRAINING_COMPLETED_DATE in fields
    assert ProfileField.FINANCIAL_DISCLOSURE_SIGNED_DATE in fields
    assert ProfileField.CV_URL in fields


def test_coordinator_requires_gcp_not_license() -> None:
    fields = REQUIRED_FIELDS[Role.COORDINATOR]
    assert ProfileField.GCP_TRAINING_COMPLETED_DATE in fields
    assert ProfileField.MEDICAL_LICENSE_NUMBER not in fields


def test_student_minimal_required() -> None:
    fields = REQUIRED_FIELDS[Role.STUDENT]
    assert fields == frozenset({ProfileField.FIRST_NAME, ProfileField.LAST_NAME})


def test_sod_includes_dm_pi() -> None:
    assert frozenset({Role.DATA_MANAGER, Role.PRINCIPAL_INVESTIGATOR}) in SAME_STUDY_CONFLICTS


def test_sod_includes_monitor_coordinator() -> None:
    assert frozenset({Role.MONITOR, Role.COORDINATOR}) in SAME_STUDY_CONFLICTS


def test_sod_includes_auditor_against_each_clinical_role() -> None:
    for partner in (
        Role.PRINCIPAL_INVESTIGATOR,
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.COORDINATOR,
    ):
        assert frozenset({Role.AUDITOR, partner}) in SAME_STUDY_CONFLICTS


def test_sod_rationale_strings_cite_regulation() -> None:
    """Every conflict has a non-empty rationale that mentions a known
    regulatory anchor (Part 11, ICH E6, or 21 CFR)."""
    for pair, rationale in SAME_STUDY_CONFLICTS.items():
        assert rationale.strip(), f"empty rationale for {pair}"
        assert any(anchor in rationale for anchor in ("Part 11", "ICH E6", "21 CFR")), (
            f"rationale for {pair} doesn't cite a regulation: {rationale}"
        )


def test_role_catalogue_covers_every_required_field_role() -> None:
    """Every role in REQUIRED_FIELDS should appear in ROLE_CATALOGUE,
    except SR-screening roles which are catalogued separately."""
    catalogued = {r.role for r in ROLE_CATALOGUE}
    sr_roles = {Role.REVIEWER_1, Role.REVIEWER_2, Role.ADJUDICATOR}
    for role in REQUIRED_FIELDS:
        if role in sr_roles:
            continue
        assert role in catalogued, f"{role} missing from ROLE_CATALOGUE"


# ── detect_grant_conflicts ───────────────────────────────────────────


def test_no_conflict_when_no_existing_assignments() -> None:
    conflicts = detect_grant_conflicts(
        existing_assignments=[],
        proposed_role="principal_investigator",
        proposed_scope_type="study",
        proposed_scope_id="study-1",
    )
    assert conflicts == []


def test_conflict_pi_then_dm_same_study() -> None:
    conflicts = detect_grant_conflicts(
        existing_assignments=[("principal_investigator", "study", "study-1")],
        proposed_role="data_manager",
        proposed_scope_type="study",
        proposed_scope_id="study-1",
    )
    assert len(conflicts) == 1
    assert conflicts[0].existing_role == Role.PRINCIPAL_INVESTIGATOR
    assert conflicts[0].proposed_role == Role.DATA_MANAGER
    assert "Part 11" in conflicts[0].rationale or "ICH E6" in conflicts[0].rationale


def test_no_conflict_when_different_studies() -> None:
    conflicts = detect_grant_conflicts(
        existing_assignments=[("principal_investigator", "study", "study-A")],
        proposed_role="data_manager",
        proposed_scope_type="study",
        proposed_scope_id="study-B",
    )
    assert conflicts == []


def test_no_conflict_global_scope() -> None:
    """Global PI + global DM doesn't conflict (no specific study);
    real conflicts are study-bound per ICH E6."""
    conflicts = detect_grant_conflicts(
        existing_assignments=[("principal_investigator", "global", None)],
        proposed_role="data_manager",
        proposed_scope_type="global",
        proposed_scope_id=None,
    )
    assert conflicts == []


def test_no_conflict_non_paired_roles() -> None:
    """Coordinator + study_designer aren't in SoD matrix."""
    conflicts = detect_grant_conflicts(
        existing_assignments=[("coordinator", "study", "study-1")],
        proposed_role="study_designer",
        proposed_scope_type="study",
        proposed_scope_id="study-1",
    )
    assert conflicts == []


def test_auditor_conflicts_with_existing_pi() -> None:
    conflicts = detect_grant_conflicts(
        existing_assignments=[("principal_investigator", "study", "s1")],
        proposed_role="auditor",
        proposed_scope_type="study",
        proposed_scope_id="s1",
    )
    assert len(conflicts) == 1


def test_idempotent_self_grant_no_conflict() -> None:
    """Granting PI again on the same scope (same role) shouldn't surface
    as a conflict — the SoD matrix only catches mixed-role pairs."""
    conflicts = detect_grant_conflicts(
        existing_assignments=[("principal_investigator", "study", "s1")],
        proposed_role="principal_investigator",
        proposed_scope_type="study",
        proposed_scope_id="s1",
    )
    assert conflicts == []


# ── missing_required_fields ──────────────────────────────────────────


def test_missing_when_profile_is_none() -> None:
    missing = missing_required_fields(role="principal_investigator", profile=None)
    assert ProfileField.MEDICAL_LICENSE_NUMBER in missing
    assert ProfileField.GCP_TRAINING_COMPLETED_DATE in missing


def test_no_missing_when_profile_complete() -> None:
    profile = UserProfile(
        user_id="u1",
        first_name="Ada",
        last_name="Lovelace",
        credentials="MD",
        medical_license_number="LIC-1",
        medical_license_country="UK",
        gcp_training_completed_date=datetime.now(UTC),
        cv_url="https://cv",
        financial_disclosure_signed_date=datetime.now(UTC),
    )
    missing = missing_required_fields(role="principal_investigator", profile=profile)
    assert missing == []


def test_missing_partial_profile() -> None:
    profile = UserProfile(user_id="u1", first_name="Ada", last_name="Lovelace")
    missing = missing_required_fields(role="coordinator", profile=profile)
    assert missing == [ProfileField.GCP_TRAINING_COMPLETED_DATE]


def test_missing_unknown_role_returns_empty() -> None:
    assert missing_required_fields(role="not_a_real_role", profile=None) == []


# ── _parse_scoped_assignments ────────────────────────────────────────


def test_parse_scoped_assignments_returns_empty_on_null() -> None:
    assert _parse_scoped_assignments(None) == []
    assert _parse_scoped_assignments("") == []


def test_parse_scoped_assignments_returns_dicts() -> None:
    raw = '[{"role":"coordinator","scope_type":"site","scope_id":"s-1"}]'
    parsed = _parse_scoped_assignments(raw)
    assert parsed == [{"role": "coordinator", "scope_type": "site", "scope_id": "s-1"}]


def test_parse_scoped_assignments_drops_non_dicts() -> None:
    raw = '[{"role":"coordinator"}, "garbage", 42]'
    parsed = _parse_scoped_assignments(raw)
    assert parsed == [{"role": "coordinator"}]


def test_parse_scoped_assignments_handles_malformed_json() -> None:
    assert _parse_scoped_assignments("not json") == []


# ── UserAdminRepository ──────────────────────────────────────────────


async def _seed_user(db: AsyncSession, *, email: str) -> User:
    user = User(email=email)
    db.add(user)
    await db.flush()
    return user


async def test_upsert_profile_creates_then_updates(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, email="ada@example.com")
    repo = UserAdminRepository(db_session)
    p = await repo.upsert_profile(user.id, first_name="Ada", last_name="Lovelace")
    assert p.first_name == "Ada"
    p2 = await repo.upsert_profile(user.id, credentials="MD")
    assert p2.first_name == "Ada"  # unchanged
    assert p2.credentials == "MD"


async def test_upsert_profile_unknown_user_raises(db_session: AsyncSession) -> None:
    repo = UserAdminRepository(db_session)
    with pytest.raises(UserAdminError, match="not found"):
        await repo.upsert_profile("ghost", first_name="x")


async def test_suspend_then_reactivate(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, email="x@example.com")
    repo = UserAdminRepository(db_session)
    profile = await repo.suspend(user.id, reason="security incident", suspended_by_user_id=None)
    assert profile.suspended_at is not None
    assert profile.suspended_reason == "security incident"
    reactivated = await repo.reactivate(user.id)
    assert reactivated.suspended_at is None
    assert reactivated.suspended_reason is None


async def test_suspend_requires_reason(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, email="x@example.com")
    repo = UserAdminRepository(db_session)
    with pytest.raises(UserAdminError, match="reason"):
        await repo.suspend(user.id, reason="   ", suspended_by_user_id=None)


async def test_reactivate_not_suspended_raises(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, email="x@example.com")
    repo = UserAdminRepository(db_session)
    with pytest.raises(UserAdminError, match="not suspended"):
        await repo.reactivate(user.id)


async def test_create_scoped_invitation_populates_both_columns(
    db_session: AsyncSession,
) -> None:
    repo = UserAdminRepository(db_session)
    inv = await repo.create_scoped_invitation(
        email="newcoord@example.com",
        assignments=[
            {"role": "coordinator", "scope_type": "site", "scope_id": "site-1"},
            {"role": "researcher", "scope_type": "global", "scope_id": None},
        ],
        invited_by=None,
    )
    assert inv.assignments_json is not None
    import json

    parsed = json.loads(inv.assignments_json)
    assert len(parsed) == 2
    # roles_json is the flat projection — kept for legacy matcher fallback.
    flat = json.loads(inv.roles_json)
    assert flat == ["coordinator", "researcher"]


async def test_create_scoped_invitation_reinvites_clears_consumed(
    db_session: AsyncSession,
) -> None:
    repo = UserAdminRepository(db_session)
    inv = await repo.create_scoped_invitation(
        email="x@example.com",
        assignments=[{"role": "researcher", "scope_type": "global", "scope_id": None}],
        invited_by=None,
    )
    inv.consumed_at = datetime.now(UTC)
    await db_session.flush()
    inv2 = await repo.create_scoped_invitation(
        email="x@example.com",
        assignments=[{"role": "coordinator", "scope_type": "site", "scope_id": "site-1"}],
        invited_by=None,
    )
    assert inv2.id == inv.id
    assert inv2.consumed_at is None


async def test_revoke_invitation_idempotent(db_session: AsyncSession) -> None:
    repo = UserAdminRepository(db_session)
    inv = await repo.create_scoped_invitation(
        email="x@example.com",
        assignments=[{"role": "researcher", "scope_type": "global", "scope_id": None}],
        invited_by=None,
    )
    assert await repo.revoke_invitation(inv.id) is True
    # Re-revoke is a no-op.
    assert await repo.revoke_invitation(inv.id) is False


async def test_create_training_record(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, email="x@example.com")
    repo = UserAdminRepository(db_session)
    rec = await repo.create_training_record(
        user_id=user.id,
        training_type="ich_gcp",
        topic="ICH E6(R2) GCP",
        provider="CITI",
        completed_date=datetime(2026, 1, 1, tzinfo=UTC),
        expires_date=datetime(2028, 1, 1, tzinfo=UTC),
    )
    assert isinstance(rec, TrainingRecord)
    assert rec.training_type == "ich_gcp"


async def test_training_record_rejects_unknown_type(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, email="x@example.com")
    repo = UserAdminRepository(db_session)
    with pytest.raises(UserAdminError, match="Unknown training_type"):
        await repo.create_training_record(
            user_id=user.id,
            training_type="bogus",
            topic="x",
            provider=None,
            completed_date=datetime.now(UTC),
        )


async def test_create_delegation_entry_with_signature(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, email="coord@example.com")
    pi = await _seed_user(db_session, email="pi@example.com")
    repo = UserAdminRepository(db_session)
    entry = await repo.create_delegation_entry(
        trial_id="trial-1",
        user_id=user.id,
        study_role="Study Coordinator",
        delegated_tasks=["informed consent", "data entry"],
        start_date=datetime(2026, 6, 1, tzinfo=UTC),
        signed_by_pi_user_id=pi.id,
    )
    assert isinstance(entry, DelegationLogEntry)
    assert entry.signed_at is not None
    assert "informed consent" in entry.delegated_tasks_json


async def test_delegation_entry_requires_role_and_tasks(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session, email="x@example.com")
    repo = UserAdminRepository(db_session)
    with pytest.raises(UserAdminError, match="study_role"):
        await repo.create_delegation_entry(
            trial_id="t1",
            user_id=user.id,
            study_role="   ",
            delegated_tasks=["x"],
            start_date=datetime.now(UTC),
        )
    with pytest.raises(UserAdminError, match="task"):
        await repo.create_delegation_entry(
            trial_id="t1",
            user_id=user.id,
            study_role="Coord",
            delegated_tasks=[],
            start_date=datetime.now(UTC),
        )


# ── resolve_login consumes assignments_json ──────────────────────────


async def test_resolve_login_uses_assignments_json_when_present(
    db_session: AsyncSession,
) -> None:
    """When a PendingInvitation has assignments_json populated, the
    matcher should grant scoped roles rather than the flat roles_json
    projection."""
    admin_repo = UserAdminRepository(db_session)
    await admin_repo.create_scoped_invitation(
        email="newcoord@example.com",
        assignments=[
            {"role": "coordinator", "scope_type": "site", "scope_id": "site-1"},
        ],
        invited_by=None,
    )
    user_repo = UserRepository(db_session)
    user = await user_repo.resolve_login(cognito_sub="sub-coord", email="newcoord@example.com")
    assignments = await user_repo.assignments_for_user(user.id)
    assert len(assignments) == 1
    assert assignments[0].role == "coordinator"
    assert assignments[0].scope_type == "site"
    assert assignments[0].scope_id == "site-1"


async def test_resolve_login_falls_back_to_roles_json_for_legacy(
    db_session: AsyncSession,
) -> None:
    """Pre-U1 PendingInvitation rows have only roles_json. Matcher should
    still grant the listed roles at global scope."""
    inv = PendingInvitation(
        email="legacy@example.com",
        roles_json='["researcher"]',
        # assignments_json deliberately NULL — legacy shape.
    )
    db_session.add(inv)
    await db_session.flush()
    user_repo = UserRepository(db_session)
    user = await user_repo.resolve_login(cognito_sub="sub-leg", email="legacy@example.com")
    assignments = await user_repo.assignments_for_user(user.id)
    assert len(assignments) == 1
    assert assignments[0].role == "researcher"
    assert assignments[0].scope_type == ScopeType.GLOBAL.value


# ── Router smoke ─────────────────────────────────────────────────────


def test_router_pins_expected_paths() -> None:
    from research_assistant.web.user_admin import create_user_admin_router

    router = create_user_admin_router()
    paths = {r.path for r in router.routes}
    expected = {
        "/user-admin/roles-catalogue",
        "/user-admin/separation-of-duties",
        "/user-admin/check-grant",
        "/user-admin/invitations",
        "/user-admin/invitations/{invitation_id}/resend",
        "/user-admin/invitations/{invitation_id}",
        "/user-admin/users",
        "/user-admin/users/{user_id}",
        "/user-admin/users/{user_id}/profile",
        "/user-admin/users/{user_id}/suspend",
        "/user-admin/users/{user_id}/reactivate",
        "/user-admin/users/{user_id}/role-assignments",
        "/user-admin/users/{user_id}/role-assignments/{assignment_id}",
        "/user-admin/users/{user_id}/training-records",
        "/user-admin/users/{user_id}/delegation-entries",
        "/user-admin/trials/{trial_id}/delegation-entries",
    }
    missing = expected - paths
    assert not missing, f"router missing routes: {missing}"
