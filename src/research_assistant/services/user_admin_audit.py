"""Sprint U4 — user-admin audit log helpers.

Canonical action names + a `record_event` helper that writes one
`UserAdminAuditEntry` row. Callers come from the endpoint layer (so we
can capture the actor sub + IP), NOT from the repository (which doesn't
know about HTTP context).

Append-only by convention. There are no `update_event` or `delete_event`
helpers; auditor downstream tooling treats the table as immutable.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..persistence.models import UserAdminAuditEntry

# ── Canonical action names ──────────────────────────────────────────────


# Invitation lifecycle
ACTION_INVITE_CREATED = "invite.created"
ACTION_INVITE_RESENT = "invite.resent"
ACTION_INVITE_REVOKED = "invite.revoked"

# Role-assignment lifecycle
ACTION_ROLE_GRANTED = "role.granted"
ACTION_ROLE_REVOKED = "role.revoked"
ACTION_ROLE_GRANT_OVERRIDDEN = "role.grant_overridden"

# Profile lifecycle
ACTION_PROFILE_PATCHED = "profile.patched"

# Suspension lifecycle
ACTION_USER_SUSPENDED = "user.suspended"
ACTION_USER_REACTIVATED = "user.reactivated"

# Onboarding lifecycle
ACTION_ONBOARDING_COMPLETED = "onboarding.completed"

# Training lifecycle
ACTION_TRAINING_RECORDED = "training.recorded"
ACTION_TRAINING_VERIFIED = "training.verified"

# Delegation lifecycle
ACTION_DELEGATION_CREATED = "delegation.created"
ACTION_DELEGATION_SIGNED = "delegation.signed"

# Account ownership lifecycle (already audited inline in web/accounts.py
# but mirrored here so the unified audit log catches it).
ACTION_ACCOUNT_OWNERSHIP_TRANSFERRED = "account.ownership_transferred"

# All known actions — drives the U4 filter dropdown + test assertions.
KNOWN_ACTIONS: frozenset[str] = frozenset(
    {
        ACTION_INVITE_CREATED,
        ACTION_INVITE_RESENT,
        ACTION_INVITE_REVOKED,
        ACTION_ROLE_GRANTED,
        ACTION_ROLE_REVOKED,
        ACTION_ROLE_GRANT_OVERRIDDEN,
        ACTION_PROFILE_PATCHED,
        ACTION_USER_SUSPENDED,
        ACTION_USER_REACTIVATED,
        ACTION_ONBOARDING_COMPLETED,
        ACTION_TRAINING_RECORDED,
        ACTION_TRAINING_VERIFIED,
        ACTION_DELEGATION_CREATED,
        ACTION_DELEGATION_SIGNED,
        ACTION_ACCOUNT_OWNERSHIP_TRANSFERRED,
    }
)


# ── Recording helper ───────────────────────────────────────────────────


async def record_event(
    session: AsyncSession,
    *,
    actor_user_id: str | None,
    action: str,
    target_user_id: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    payload: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> UserAdminAuditEntry:
    """Append one audit row. Returns the row so callers can chain
    further writes in the same session.

    `payload` is serialised with `json.dumps(default=str)` so datetime /
    UUID / Decimal values pass through. Sensitive fields (e.g. passwords)
    must be redacted BY THE CALLER before passing — the helper trusts
    the caller's filter.
    """
    entry = UserAdminAuditEntry(
        actor_user_id=actor_user_id,
        action=action,
        target_user_id=target_user_id,
        scope_type=scope_type,
        scope_id=scope_id,
        payload_json=json.dumps(payload or {}, default=str),
        ip_address=ip_address,
    )
    session.add(entry)
    await session.flush()
    return entry
