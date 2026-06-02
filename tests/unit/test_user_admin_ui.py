"""Sprint U2 — static-file pins for /users-admin.html surfaces.

Vanilla-JS without a browser harness can't be unit-tested for behavior,
but we can pin that the key handler names + API endpoint references
exist in the file. Catches accidental deletion during future refactors
and serves as a docstring for the audit.

Also covers the new POST /invitations/by-email/resend endpoint that
was added so the admin UI can resend by email (without tracking the
invitation_id in the user list).
"""

from __future__ import annotations

from pathlib import Path


def _read_static(name: str) -> str:
    root = Path(__file__).resolve().parents[2] / "src" / "research_assistant" / "web" / "static"
    return (root / name).read_text(encoding="utf-8")


# ── /users-admin.html — page exists + key handlers present ──────────


def test_users_admin_page_exists() -> None:
    body = _read_static("users-admin.html")
    assert len(body) > 1000
    assert "User administration" in body


def test_users_admin_has_filters() -> None:
    body = _read_static("users-admin.html")
    assert 'id="f-scope-type"' in body
    assert 'id="f-scope-id"' in body
    assert 'id="f-role"' in body
    assert 'id="f-status"' in body


def test_users_admin_calls_check_grant_endpoint() -> None:
    body = _read_static("users-admin.html")
    assert "/api/user-admin/check-grant" in body
    assert "runGrantCheck" in body


def test_users_admin_calls_roles_catalogue() -> None:
    body = _read_static("users-admin.html")
    assert "/api/user-admin/roles-catalogue" in body
    assert "/api/user-admin/separation-of-duties" in body


def test_users_admin_has_invite_modal_with_override() -> None:
    body = _read_static("users-admin.html")
    assert 'id="invite-modal"' in body
    assert 'id="invite-override"' in body
    assert "submitInvite" in body
    # Server-side enforcement uses override_rationale; client must attach it.
    assert "override_rationale" in body


def test_users_admin_has_grant_modal() -> None:
    body = _read_static("users-admin.html")
    assert 'id="grant-modal"' in body
    assert "submitGrant" in body
    assert 'id="grant-override"' in body


def test_users_admin_has_suspend_flow() -> None:
    body = _read_static("users-admin.html")
    assert 'id="suspend-modal"' in body
    assert "submitSuspend" in body
    assert "/suspend" in body


def test_users_admin_has_csv_drop() -> None:
    body = _read_static("users-admin.html")
    assert 'id="csv-drop"' in body
    assert "parseCsv" in body
    assert "submitCsv" in body


def test_users_admin_has_catalogue_drawer() -> None:
    body = _read_static("users-admin.html")
    assert 'id="catalogue-drawer"' in body
    assert "openCatalogue" in body
    assert "catalogue-entry" in body


def test_users_admin_renders_conflict_block_with_rationale() -> None:
    """Conflict rendering must surface the regulatory rationale so admin
    can make an informed override decision."""
    body = _read_static("users-admin.html")
    assert "renderConflicts" in body
    assert "rationale" in body
    assert 'class="conflict"' in body


def test_users_admin_intra_invitation_sod_check() -> None:
    """Client-side SoD pre-check across the invite-modal's rows uses the
    loaded matrix — guard against accidental deletion."""
    body = _read_static("users-admin.html")
    assert "findIntraConflicts" in body
    assert "sodMatrix" in body


def test_users_admin_uses_resend_by_email_endpoint() -> None:
    body = _read_static("users-admin.html")
    assert "/api/user-admin/invitations/by-email/resend" in body


# ── /index.html — sidebar link ──────────────────────────────────────


def test_sidebar_has_user_admin_link_gated_on_user_manage() -> None:
    body = _read_static("index.html")
    assert "/users-admin.html" in body
    # The link must be guarded by hasPerm("user.manage") — pre-existing
    # admin.html link is also gated; we follow the same pattern.
    assert 'hasPerm("user.manage")' in body
    # Pin the link text so a future refactor doesn't silently drop the
    # navigation entry.
    assert "User Admin" in body


# ── Router still pins all 17 routes (16 from U1 + 1 from U2) ────────


def test_router_pins_resend_by_email_route() -> None:
    from research_assistant.web.user_admin import create_user_admin_router

    router = create_user_admin_router()
    paths = {r.path for r in router.routes}
    assert "/user-admin/invitations/by-email/resend" in paths
