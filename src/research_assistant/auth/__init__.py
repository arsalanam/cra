"""AWS Cognito OIDC integration.

Three pieces, all small enough to live in their own modules:

  • `jwks` — fetches Cognito's public keys and caches them, with a
    refresh-on-kid-miss hook so key rotation doesn't break logins.
  • `tokens` — validates Cognito ID tokens (signature + iss + aud + exp)
    and returns a typed `IdentityClaims`.
  • `session` — signs short-lived session cookies that round-trip the
    validated claims to the browser without re-validating the JWT on
    every request.

The FastAPI surface in `web/auth.py` glues these together into
`/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/me`, and a
`Depends(current_user)` dependency.
"""

from .rbac import (
    ROLE_PERMISSIONS,
    SKILL_PERMISSION,
    Permission,
    Role,
    ScopeType,
    assignment_applies,
    effective_permissions,
    normalize_legacy_role,
    permissions_for_role,
)
from .session import SessionPayload, clear_session, read_session, write_session
from .tokens import IdentityClaims, TokenValidationError, validate_id_token

__all__ = [
    "ROLE_PERMISSIONS",
    "SKILL_PERMISSION",
    "IdentityClaims",
    "Permission",
    "Role",
    "ScopeType",
    "SessionPayload",
    "TokenValidationError",
    "assignment_applies",
    "clear_session",
    "effective_permissions",
    "normalize_legacy_role",
    "permissions_for_role",
    "read_session",
    "validate_id_token",
    "write_session",
]
