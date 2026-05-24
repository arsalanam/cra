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
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from ..auth import (
    IdentityClaims,
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


async def require_admin(user: CurrentUser) -> SessionPayload:
    """FastAPI dependency: require the caller to hold the 'admin' role.

    Roles are read fresh from the DB (by Cognito sub) so a grant/revoke
    takes effect on the next request rather than at next login. When auth
    is disabled (tests / early dev) this short-circuits open, mirroring
    `current_user`. Raises 403 if the authenticated user lacks 'admin'.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return user
    async with get_db_session() as db:
        roles = await UserRepository(db).roles_for_sub(user.sub)
    if "admin" not in roles:
        raise HTTPException(status_code=403, detail="Admin role required.")
    return user


AdminUser = Annotated[SessionPayload, Depends(require_admin)]


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
                await UserRepository(db).resolve_login(
                    cognito_sub=claims.sub, email=claims.email
                )
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
        """Return identity + roles for the current session.

        Roles are loaded from the DB so the frontend can show/hide admin
        affordances without baking (staleable) roles into the cookie.
        """
        settings = get_settings()
        roles: list[str] = []
        if settings.auth_enabled:
            async with get_db_session() as db:
                roles = await UserRepository(db).roles_for_sub(user.sub)
        return {
            "sub": user.sub,
            "email": user.email,
            "expires_at": user.expires_at,
            "roles": roles,
        }

    return router


__all__ = [
    "AdminUser",
    "CurrentUser",
    "create_auth_router",
    "current_user",
    "require_admin",
]


def _identity_from_claims(claims: IdentityClaims) -> SessionPayload:
    """Test hook — convert validated claims to a SessionPayload."""
    return SessionPayload(sub=claims.sub, email=claims.email, expires_at=claims.expires_at)
