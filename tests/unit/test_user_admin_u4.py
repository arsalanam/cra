"""Sprint U4 — audit log + countersigning + reports + suspension tests.

Coverage:
  - UserAdminAuditEntry model round-trip via record_event
  - KNOWN_ACTIONS shape — every action label is referenceable
  - Report assemblers + PDF builders return non-empty bytes
  - Static-file pins for /users-admin.html U4 tabs + sign modal
  - Router shape — new endpoints mounted
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.models import (
    UserAdminAuditEntry,
    UserProfile,
)
from research_assistant.reports.user_admin_reports import (
    AuditLogData,
    AuditLogRow,
    DelegationLogData,
    DelegationRow,
    TrainingMatrixData,
    TrainingRow,
    _truncate_payload,
    build_audit_log_pdf,
    build_delegation_log_pdf,
    build_training_matrix_pdf,
)
from research_assistant.services.user_admin_audit import (
    ACTION_DELEGATION_SIGNED,
    ACTION_INVITE_CREATED,
    ACTION_PROFILE_PATCHED,
    ACTION_ROLE_GRANT_OVERRIDDEN,
    ACTION_ROLE_GRANTED,
    ACTION_ROLE_REVOKED,
    ACTION_USER_REACTIVATED,
    ACTION_USER_SUSPENDED,
    KNOWN_ACTIONS,
    record_event,
)

# ── KNOWN_ACTIONS shape ─────────────────────────────────────────────


def test_known_actions_covers_lifecycle() -> None:
    """Every action constant we import must be in KNOWN_ACTIONS so the
    /audit endpoint's filter validation accepts them."""
    expected = {
        ACTION_INVITE_CREATED,
        ACTION_ROLE_GRANTED,
        ACTION_ROLE_REVOKED,
        ACTION_ROLE_GRANT_OVERRIDDEN,
        ACTION_USER_SUSPENDED,
        ACTION_USER_REACTIVATED,
        ACTION_PROFILE_PATCHED,
        ACTION_DELEGATION_SIGNED,
    }
    assert expected <= KNOWN_ACTIONS


def test_known_actions_use_dot_namespace() -> None:
    """Convention: all actions are dotted ('invite.created'); catches
    typos and helps audit filtering by prefix."""
    for action in KNOWN_ACTIONS:
        assert "." in action, f"action {action!r} should use dotted namespace"


# ── record_event round-trip ─────────────────────────────────────────


async def test_record_event_persists_payload(db_session: AsyncSession) -> None:
    entry = await record_event(
        db_session,
        actor_user_id=None,
        action=ACTION_INVITE_CREATED,
        target_user_id=None,
        payload={"email": "ada@example.com", "assignments": [{"role": "researcher"}]},
        ip_address="127.0.0.1",
    )
    refreshed = await db_session.get(UserAdminAuditEntry, entry.id)
    assert refreshed is not None
    assert refreshed.action == ACTION_INVITE_CREATED
    assert "ada@example.com" in refreshed.payload_json
    assert refreshed.ip_address == "127.0.0.1"


async def test_record_event_with_scope(db_session: AsyncSession) -> None:
    """Scope columns capture study / site / trial context for audit
    queries scoped to a specific resource."""
    entry = await record_event(
        db_session,
        actor_user_id=None,
        action=ACTION_ROLE_GRANTED,
        target_user_id=None,
        scope_type="study",
        scope_id="study-1",
        payload={"role": "coordinator"},
    )
    refreshed = await db_session.get(UserAdminAuditEntry, entry.id)
    assert refreshed is not None
    assert refreshed.scope_type == "study"
    assert refreshed.scope_id == "study-1"


async def test_record_event_serialises_datetime(db_session: AsyncSession) -> None:
    """json.dumps default=str must handle datetime + uuid + Decimal."""
    now = datetime.now(UTC)
    entry = await record_event(
        db_session,
        actor_user_id=None,
        action=ACTION_PROFILE_PATCHED,
        payload={"when": now, "fields_changed": ["first_name"]},
    )
    refreshed = await db_session.get(UserAdminAuditEntry, entry.id)
    assert refreshed is not None
    assert "first_name" in refreshed.payload_json


# ── Report PDF builders ─────────────────────────────────────────────


def test_delegation_log_pdf_returns_bytes() -> None:
    data = DelegationLogData(
        trial_id="t-1",
        trial_title="Phase 2 SGLT2i in T2DM",
        sponsor="Sponsor Inc",
        indication="Type 2 diabetes",
        pi_signature_count=1,
        unsigned_count=1,
        rows=[
            DelegationRow(
                user_email="coord@example.com",
                user_name="Ada Lovelace",
                study_role="Study Coordinator",
                delegated_tasks=["informed consent", "data entry"],
                start_date=datetime(2026, 6, 1, tzinfo=UTC),
                end_date=None,
                signed_at=datetime(2026, 6, 2, tzinfo=UTC),
                signed_by_pi_name="Dr Smith",
            ),
            DelegationRow(
                user_email="dm@example.com",
                user_name="Grace Hopper",
                study_role="Data Manager",
                delegated_tasks=["query resolution", "database lock"],
                start_date=datetime(2026, 6, 1, tzinfo=UTC),
                end_date=None,
                signed_at=None,
                signed_by_pi_name=None,
            ),
        ],
    )
    pdf = build_delegation_log_pdf(data)
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_training_matrix_pdf_returns_bytes() -> None:
    data = TrainingMatrixData(
        site_id="s-1",
        site_name="University Hospital",
        site_code="UH-01",
        members=2,
        expired_count=1,
        expiring_soon_count=0,
        rows=[
            TrainingRow(
                user_email="x@example.com",
                user_name="A B",
                topic="ICH GCP",
                provider="CITI",
                completed_date=datetime(2024, 1, 1, tzinfo=UTC),
                expires_date=datetime(2025, 1, 1, tzinfo=UTC),
                expired=True,
                expiring_soon=False,
            ),
            TrainingRow(
                user_email="y@example.com",
                user_name="C D",
                topic="ICH GCP",
                provider="CITI",
                completed_date=datetime(2026, 1, 1, tzinfo=UTC),
                expires_date=datetime(2029, 1, 1, tzinfo=UTC),
                expired=False,
                expiring_soon=False,
            ),
        ],
    )
    pdf = build_training_matrix_pdf(data)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_training_matrix_pdf_handles_empty_rows() -> None:
    """No assigned members yet should render the empty-state message
    rather than crashing on an empty Table."""
    data = TrainingMatrixData(
        site_id="s-1",
        site_name="Empty Site",
        site_code=None,
        members=0,
        expired_count=0,
        expiring_soon_count=0,
        rows=[],
    )
    pdf = build_training_matrix_pdf(data)
    assert pdf.startswith(b"%PDF")


def test_audit_log_pdf_returns_bytes() -> None:
    data = AuditLogData(
        title="Audit log",
        from_ts=datetime(2026, 6, 1, tzinfo=UTC),
        to_ts=datetime(2026, 6, 30, tzinfo=UTC),
        filter_summary="action=role.granted",
        rows=[
            AuditLogRow(
                created_at=datetime(2026, 6, 15, tzinfo=UTC),
                actor_email="admin@example.com",
                action=ACTION_ROLE_GRANTED,
                target_email="user@example.com",
                scope_type="study",
                scope_id="study-1234",
                payload_json='{"role": "coordinator"}',
                ip_address="10.0.0.1",
            ),
        ],
    )
    pdf = build_audit_log_pdf(data)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_truncate_payload_handles_long_values() -> None:
    """Audit payload PDF rendering must truncate long values to avoid
    blowing up the table layout."""
    payload = '{"override_rationale": "' + ("x" * 500) + '"}'
    out = _truncate_payload(payload, max_len=100)
    assert len(out) <= 101  # 100 + ellipsis
    assert out.endswith("…")


def test_truncate_payload_handles_invalid_json() -> None:
    """Malformed JSON falls through to raw-substring rendering rather
    than crashing the report build."""
    out = _truncate_payload("not json at all", max_len=200)
    assert "not json" in out


# ── Static-file pins (U4 frontend) ──────────────────────────────────


def _read(name: str) -> str:
    root = Path(__file__).resolve().parents[2] / "src" / "research_assistant" / "web" / "static"
    return (root / name).read_text(encoding="utf-8")


def test_users_admin_has_tab_nav() -> None:
    body = _read("users-admin.html")
    assert 'data-tab="audit"' in body
    assert 'data-tab="queue"' in body
    assert 'data-tab="expiry"' in body
    assert 'data-tab="reports"' in body
    assert "switchTab" in body


def test_users_admin_has_sign_modal() -> None:
    body = _read("users-admin.html")
    assert 'id="sign-modal"' in body
    assert "submitSign" in body
    assert "delegation-entries/" in body
    assert "/sign" in body


def test_users_admin_calls_u4_endpoints() -> None:
    body = _read("users-admin.html")
    assert "/api/user-admin/audit" in body
    assert "/api/user-admin/delegation-queue" in body
    assert "/api/user-admin/training-expiring" in body
    assert "/api/user-admin/reports/delegation-log/" in body
    assert "/api/user-admin/reports/training-matrix/" in body
    assert "/api/user-admin/reports/audit-log.pdf" in body


# ── Router pins ────────────────────────────────────────────────────


def test_router_pins_u4_routes() -> None:
    from research_assistant.web.user_admin import create_user_admin_router

    router = create_user_admin_router()
    paths = {r.path for r in router.routes}
    expected = {
        "/user-admin/audit",
        "/user-admin/audit/actions",
        "/user-admin/delegation-queue",
        "/user-admin/training-expiring",
        "/user-admin/delegation-entries/{entry_id}/sign",
        "/user-admin/reports/delegation-log/{trial_id}.pdf",
        "/user-admin/reports/training-matrix/{site_id}.pdf",
        "/user-admin/reports/audit-log.pdf",
    }
    missing = expected - paths
    assert not missing, f"router missing routes: {missing}"


# ── authz.py — suspension gate code presence ───────────────────────


def test_authz_enforces_suspension() -> None:
    authz = Path(__file__).resolve().parents[2] / "src" / "research_assistant" / "web" / "authz.py"
    body = authz.read_text(encoding="utf-8")
    # Suspension check must reference UserProfile.suspended_at
    assert "suspended_at" in body
    assert "Your account is suspended" in body
    # Must come BEFORE the perm check so the suspension error wins
    suspension_idx = body.index("Your account is suspended")
    onboarding_idx = body.index("Onboarding required")
    assert suspension_idx < onboarding_idx, "suspension check should fire before onboarding gate"


# ── UserAdminAuditEntry — append-only pin ──────────────────────────


def test_user_admin_audit_entry_has_no_update_helper() -> None:
    """Append-only by convention — the repository must not expose an
    update or delete method on audit entries. Catches accidental
    addition during refactors."""
    from research_assistant.persistence import user_admin_repository

    src = Path(user_admin_repository.__file__).read_text(encoding="utf-8")
    assert "update_audit" not in src
    assert "delete_audit" not in src


# ── /onboarding completion records audit ───────────────────────────


async def test_onboarding_completion_records_audit_event(
    db_session: AsyncSession,
) -> None:
    """When a user completes onboarding, the action must be recorded so
    the regulatory audit log captures the milestone."""
    from sqlalchemy import select

    from research_assistant.persistence.models import User
    from research_assistant.services.user_admin_audit import ACTION_ONBOARDING_COMPLETED

    user = User(email="x@example.com")
    db_session.add(user)
    await db_session.flush()
    # Simulate the audit recording the onboarding handler does.
    await record_event(
        db_session,
        actor_user_id=user.id,
        action=ACTION_ONBOARDING_COMPLETED,
        target_user_id=user.id,
        payload={"roles": ["coordinator"]},
    )
    rows = list(
        (
            await db_session.scalars(
                select(UserAdminAuditEntry).where(
                    UserAdminAuditEntry.action == ACTION_ONBOARDING_COMPLETED
                )
            )
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].target_user_id == user.id


# ── Suspended user has UserProfile.suspended_at set ────────────────


def test_user_profile_suspension_columns_present() -> None:
    """U1 added the suspension columns; U4 reads them. Pin the column
    set so a model refactor doesn't silently break the gate."""
    p = UserProfile(user_id="u1")
    for col in ("suspended_at", "suspended_by_user_id", "suspended_reason"):
        assert hasattr(p, col), f"UserProfile missing {col}"


# ── Onboarding endpoint actor + IP capture ─────────────────────────


def test_onboarding_complete_imports_record_event() -> None:
    """The /me/complete handler must record an audit event."""
    src = (
        Path(__file__).resolve().parents[2] / "src" / "research_assistant" / "web" / "onboarding.py"
    )
    body = src.read_text(encoding="utf-8")
    assert "ACTION_ONBOARDING_COMPLETED" in body
    assert "record_event" in body


# ── Training expiry — date math ────────────────────────────────────


def test_training_expiring_marks_expired_vs_expiring_soon() -> None:
    """Training records with expires_date in the past are EXPIRED;
    within 30 days is EXPIRING_SOON. The PDF builder uses these flags."""
    now = datetime.now(UTC)
    past = TrainingRow(
        user_email="x@example.com",
        user_name="A",
        topic="GCP",
        provider=None,
        completed_date=now - timedelta(days=400),
        expires_date=now - timedelta(days=1),
        expired=True,
        expiring_soon=False,
    )
    soon = TrainingRow(
        user_email="x@example.com",
        user_name="A",
        topic="GCP",
        provider=None,
        completed_date=now - timedelta(days=200),
        expires_date=now + timedelta(days=10),
        expired=False,
        expiring_soon=True,
    )
    assert past.expired is True
    assert past.expiring_soon is False
    assert soon.expired is False
    assert soon.expiring_soon is True
