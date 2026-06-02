"""Cognito OIDC routes + the FastAPI `current_user` dependency.

Endpoints:
  • GET /auth/login     — kicks the user to Cognito's hosted UI.
  • GET /auth/callback  — receives `?code=...`, exchanges it for tokens,
                          validates the ID token, sets the session cookie,
                          redirects to /.
  • POST /auth/logout   — clears the session cookie and bounces to
                          Cognito's logout endpoint.
  • GET  /auth/me       — returns the current session's identity claims.

The `current_user` Depends is the integration point for the rest of the
app. When `settings.auth_enabled` is False (no Cognito configured, e.g.
in pytest), it short-circuits and returns a placeholder so existing
non-auth code paths keep working. Phase D will replace `_require_admin`
in admin endpoints with `Depends(current_user)` + role checks.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from ..auth import (
    IdentityClaims,
    Permission,
    SessionPayload,
    TokenValidationError,
    clear_session,
    read_session,
    validate_id_token,
    write_session,
)
from ..config import Settings, get_settings
from ..persistence.database import get_db_session
from ..persistence.user_repository import NoInvitationError, UserRepository

logger = logging.getLogger(__name__)


# Cookie used to round-trip the OIDC `state` parameter between /auth/login
# and /auth/callback. Short-lived; cleared on callback.
_STATE_COOKIE = "cra_oauth_state"


def _require_auth_configured(settings: Settings) -> None:
    """Raise 503 if Cognito settings are missing.

    Endpoints under /auth/* call this — operating the app in auth-disabled
    mode is fine for tests, but actually trying to log in requires real
    Cognito config.
    """
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Authentication is not configured. "
                "Run scripts/cognito_setup.py and populate the COGNITO_* "
                "env vars before using /auth/* endpoints."
            ),
        )


async def current_user(request: Request) -> SessionPayload:
    """FastAPI dependency. Returns the session payload or raises 401.

    When auth is disabled (`auth_enabled == False`), returns a stable
    placeholder so existing endpoints stay reachable in tests / early
    dev. Phase D removes the placeholder once auth is the default.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return SessionPayload(
            sub="default-user",
            email="dev@local",
            expires_at=2**31 - 1,
        )
    session = read_session(request, secret=settings.session_cookie_secret)
    if session is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": 'Session realm="cra", login="/auth/login"'},
        )
    return session


CurrentUser = Annotated[SessionPayload, Depends(current_user)]


async def _effective_perms(user: SessionPayload) -> frozenset[Permission]:
    """Resolve the caller's global-scope effective permissions, fresh from DB.

    Caching is intentionally absent — the RBAC contract is that a grant or
    revoke takes effect on the next request, not next login. A future
    per-request cache is fine; a process-wide cache is not.
    """
    async with get_db_session() as db:
        return await UserRepository(db).effective_permissions_for_sub(user.sub)


def require_permission(perm: Permission) -> Any:
    """Build a FastAPI dependency that allows the request only if the caller
    holds `perm` at global scope (per `rbac-design.md` §5).

    Returns `Depends(...)` so it composes the same way as the legacy
    `AdminUser`/`DataEntryUser` annotations. When auth is disabled
    (tests / early dev) the dependency short-circuits open, mirroring
    `current_user`.

    Per-study / per-site scope resolution lands in RBAC-2 alongside the
    eCRF resource→scope resolvers. This RBAC-1 surface covers the
    everything-global cases (skill gating, admin, data_entry).
    """

    async def _dep(user: CurrentUser) -> SessionPayload:
        settings = get_settings()
        if not settings.auth_enabled:
            return user
        perms = await _effective_perms(user)
        if perm not in perms:
            raise HTTPException(
                status_code=403,
                detail=f"Permission required: {perm.value}.",
            )
        return user

    return Depends(_dep)


async def require_admin(user: CurrentUser) -> SessionPayload:
    """FastAPI dependency: require platform-admin authority.

    Back-compat shim — re-expresses the legacy `admin` role check as the
    `user.manage` permission, which the matrix in `auth.rbac` grants only
    to the `admin` role. Anything calling this keeps working; new code
    should depend on `require_permission(Permission.USER_MANAGE)` (or the
    narrower perm it actually needs) directly.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return user
    perms = await _effective_perms(user)
    if Permission.USER_MANAGE not in perms:
        raise HTTPException(status_code=403, detail="Admin role required.")
    return user


AdminUser = Annotated[SessionPayload, Depends(require_admin)]


async def require_data_entry(user: CurrentUser) -> SessionPayload:
    """FastAPI dependency: require clinical-data write authority (eCRF E1).

    Back-compat shim over the new `data.enter` permission, which the matrix
    grants to `coordinator` (and `admin`). The legacy `data_entry` role
    string is mapped to `coordinator` by `auth.rbac.normalize_legacy_role`
    so existing assignments keep working without operator action. Per-site
    scoping (a coordinator only seeing their own site's subjects) lands in
    RBAC-2 — for now the check is global, exactly as before.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return user
    perms = await _effective_perms(user)
    if Permission.DATA_ENTER not in perms:
        raise HTTPException(
            status_code=403,
            detail="data_entry or admin role required.",
        )
    return user


DataEntryUser = Annotated[SessionPayload, Depends(require_data_entry)]


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.get("/login")
    async def login(
        response: Response,
        next_url: str = Query("/", alias="next"),
    ) -> RedirectResponse:
        """Bounce to Cognito's hosted UI; sets a state cookie for CSRF."""
        settings = get_settings()
        _require_auth_configured(settings)

        state = secrets.token_urlsafe(32)
        params = {
            "client_id": settings.cognito_client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": settings.cognito_redirect_uri,
            "state": state,
        }
        target = f"{settings.cognito_domain}/oauth2/authorize?" + urlencode(params)

        redirect = RedirectResponse(url=target, status_code=302)
        # State + post-login destination round-tripped via short-lived cookie
        # so /auth/callback can verify state and forward the user back.
        redirect.set_cookie(
            key=_STATE_COOKIE,
            value=f"{state}|{next_url}",
            max_age=600,  # 10 min for the round trip
            httponly=True,
            samesite="lax",
            path="/auth",
        )
        return redirect

    @router.get("/callback")
    async def callback(
        request: Request,
        code: str = Query(...),
        state: str = Query(...),
    ) -> RedirectResponse:
        """Exchange the auth code for tokens, set session, redirect home."""
        settings = get_settings()
        _require_auth_configured(settings)

        raw_state_cookie = request.cookies.get(_STATE_COOKIE) or ""
        expected_state, _, next_url = raw_state_cookie.partition("|")
        if not expected_state or expected_state != state:
            raise HTTPException(status_code=400, detail="state mismatch — possible CSRF")
        if not next_url:
            next_url = "/"

        # Exchange the code for tokens at Cognito's /oauth2/token endpoint.
        # The endpoint requires Basic auth using client_id:client_secret
        # (we're a confidential client).
        token_url = f"{settings.cognito_domain}/oauth2/token"
        body = {
            "grant_type": "authorization_code",
            "client_id": settings.cognito_client_id,
            "code": code,
            "redirect_uri": settings.cognito_redirect_uri,
        }
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                token_url,
                data=body,
                auth=(settings.cognito_client_id, settings.cognito_client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            logger.warning("Cognito token exchange failed: %d %s", resp.status_code, resp.text)
            raise HTTPException(
                status_code=502,
                detail=f"Token exchange with Cognito failed (status {resp.status_code}).",
            )
        token_payload = resp.json()
        id_token = token_payload.get("id_token")
        if not id_token:
            raise HTTPException(status_code=502, detail="Cognito response missing id_token.")
        # Needed to verify the ID token's at_hash claim (see validate_id_token).
        access_token = token_payload.get("access_token")

        # Verify the ID token — signature, audience, issuer, expiry, at_hash.
        try:
            claims = await validate_id_token(
                id_token,
                region=settings.cognito_region,
                user_pool_id=settings.cognito_user_pool_id,
                client_id=settings.cognito_client_id,
                access_token=access_token,
            )
        except TokenValidationError as e:
            logger.warning("ID token validation failed: %s", e)
            raise HTTPException(status_code=401, detail=f"ID token rejected: {e}") from e

        # Bind the validated identity to a local user + roles. Admin-invitation
        # only: a first login with no pending invitation is refused.
        async with get_db_session() as db:
            try:
                await UserRepository(db).resolve_login(cognito_sub=claims.sub, email=claims.email)
            except NoInvitationError as e:
                logger.warning("Login rejected — no invitation for %s", e.email)
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"No invitation found for {e.email}. "
                        "Ask an administrator to invite you first."
                    ),
                ) from e

        redirect = RedirectResponse(url=next_url, status_code=302)
        # Drop the state cookie now that the round-trip is complete.
        redirect.delete_cookie(key=_STATE_COOKIE, path="/auth")
        write_session(
            redirect,
            payload=SessionPayload(
                sub=claims.sub,
                email=claims.email,
                expires_at=claims.expires_at,
            ),
            secret=settings.session_cookie_secret,
            # Browser will only resend over HTTPS in production. Compose dev
            # is http://localhost so we relax in dev — operator should put
            # the app behind TLS for any real deployment.
            secure=settings.cognito_redirect_uri.startswith("https://"),
        )
        return redirect

    @router.post("/logout")
    async def logout() -> RedirectResponse:
        """Clear session, then bounce to Cognito's logout endpoint."""
        settings = get_settings()
        _require_auth_configured(settings)

        logout_redirect = f"{settings.cognito_domain}/logout?" + urlencode(
            {
                "client_id": settings.cognito_client_id,
                "logout_uri": settings.cognito_redirect_uri.rsplit("/auth/", 1)[0] + "/",
            }
        )
        response = RedirectResponse(url=logout_redirect, status_code=302)
        clear_session(response)
        return response

    @router.get("/me")
    async def me(user: CurrentUser) -> dict[str, object]:
        """Return identity + roles + effective permissions for the current session.

        Roles are loaded from the DB so the frontend can show/hide admin
        affordances without baking (staleable) roles into the cookie.

        `permissions` is the global-scope effective permission set — the
        frontend uses it to hide workflow entry points the caller can't
        run (e.g. hide RoB / SR-protocol cards for a Student account that
        only has `skill.meta_analysis` + `skill.general_qa`). Server-side
        gating in `web/dispatch.py` is authoritative; this is UX polish.

        Sprint U3: also returns the onboarding gate signal so the frontend
        can redirect to /onboarding.html when required.
        """
        from ..persistence.user_admin_repository import UserAdminRepository
        from ..services.user_admin import compute_missing_by_role, is_onboarded

        settings = get_settings()
        roles: list[str] = []
        permissions: list[str] = []
        onboarding_required = False
        onboarding_completed_at = None
        missing_fields_by_role: dict[str, list[str]] = {}
        if settings.auth_enabled:
            async with get_db_session() as db:
                repo = UserRepository(db)
                roles = await repo.roles_for_sub(user.sub)
                perms = await repo.effective_permissions_for_sub(user.sub)
                permissions = sorted(p.value for p in perms)

                local = await repo.get_by_sub(user.sub)
                if local is not None:
                    admin_repo = UserAdminRepository(db)
                    profile, assignments = await admin_repo.onboarding_snapshot_for_user(local.id)
                    missing_fields_by_role = compute_missing_by_role(
                        assignment_roles=[a.role for a in assignments],
                        profile=profile,
                    )
                    if profile is not None and profile.onboarding_completed_at is not None:
                        onboarding_completed_at = profile.onboarding_completed_at.isoformat()
                    # Gate fires when: not onboarded yet AND user has at least
                    # one role (any missing fields). A user with zero
                    # assignments isn't gated — they can't reach anything.
                    onboarding_required = not is_onboarded(profile) and len(assignments) > 0
        return {
            "sub": user.sub,
            "email": user.email,
            "expires_at": user.expires_at,
            "roles": roles,
            "permissions": permissions,
            "onboarding_required": onboarding_required,
            "onboarding_completed_at": onboarding_completed_at,
            "missing_fields_by_role": missing_fields_by_role,
        }

    return router


__all__ = [
    "AdminUser",
    "CurrentUser",
    "DataEntryUser",
    "create_auth_router",
    "current_user",
    "require_admin",
    "require_data_entry",
    "require_permission",
]


def _identity_from_claims(claims: IdentityClaims) -> SessionPayload:
    """Test hook — convert validated claims to a SessionPayload."""
    return SessionPayload(sub=claims.sub, email=claims.email, expires_at=claims.expires_at)
