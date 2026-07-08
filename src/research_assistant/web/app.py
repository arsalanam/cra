"""
app.py
────────────────────────────────────────────────────────────────────────────
FastAPI app factory:
  1. Serves the React frontend (web/static/index.html)
  2. Provides thread-based conversation endpoints under /api/threads
  3. Routes user turns through the dispatcher under /api/turn
     (alias /api/clinical/turn kept for backward compat)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from ..auth import clear_session
from ..config import get_settings
from ..data.sample_questions import SAMPLE_QUESTIONS
from ..persistence.clinical.database import init_clinical_db
from ..persistence.database import init_db
from ..rag import register_embedding_drain
from ..services.scheduler import start_scheduler, stop_scheduler
from .accounts import create_accounts_router
from .admin import create_admin_router
from .auth import create_auth_router, current_user
from .citations import create_citations_router
from .dispatch import create_dispatch_router
from .ecrf import create_ecrf_router
from .edc import create_edc_router
from .epro import create_epro_router
from .library import create_library_router
from .onboarding import create_onboarding_router
from .portfolio import create_portfolio_router
from .sr import create_sr_router
from .threads import create_thread_router
from .user_admin import create_user_admin_router
from .watch_subscriptions import create_subscriptions_router
from .watches import create_notifications_router, create_watches_router

logger = logging.getLogger(__name__)


class _RevalidatingStatics(StaticFiles):
    """StaticFiles that makes browsers revalidate HTML on every load.

    The whole SPA is a single index.html (React via CDN + inline JSX), so a
    cached copy means users keep running the old UI after a deploy. Serving
    HTML with `Cache-Control: no-cache` forces a revalidation on each load — a
    cheap 304 when unchanged, a fresh 200 after a new build — so a normal
    reload always picks up the latest UI and no hard-refresh (Ctrl+F5) is
    needed. Non-HTML assets keep their default caching.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        logger.info(
            "Starting Research Assistant — model=%s, region=%s",
            settings.bedrock_model_id,
            settings.aws_region,
        )
        await init_db()
        await init_clinical_db()
        await start_scheduler()
        register_embedding_drain()
        yield
        await stop_scheduler()
        logger.info("Shutting down Research Assistant")

    app = FastAPI(
        title="Research Assistant",
        description=("AI Research Assistant powered by Pydantic AI + AWS Bedrock"),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─── Auth routes (top-level, not under /api) ─────────────────────────
    # /auth/login, /auth/callback, /auth/logout, /auth/me. Admin endpoints
    # require the 'admin' role (Phase D); broader /api/* gating + frontend
    # 401 handling land in Phase E.
    app.include_router(create_auth_router())

    # ─── Thread-based conversation routes ─────────────────────────────────
    # User-data routers require an authenticated session (Phase E). When auth
    # is disabled (tests / early dev) `current_user` short-circuits to the
    # default-user placeholder, so these stay reachable. Admin endpoints carry
    # their own stricter `AdminUser` (role) check.
    auth_dep = [Depends(current_user)]
    app.include_router(create_thread_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_dispatch_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_admin_router(), prefix="/api")
    app.include_router(create_user_admin_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_onboarding_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_watches_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_subscriptions_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_notifications_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_accounts_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_library_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_ecrf_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_edc_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_sr_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_citations_router(), prefix="/api", dependencies=auth_dep)
    app.include_router(create_portfolio_router(), prefix="/api", dependencies=auth_dep)
    # ePRO is token-authenticated (participants are not Cognito users) — no
    # session dependency; the magic-link token is the credential.
    app.include_router(create_epro_router(), prefix="/api")

    @app.get("/api/questions")
    async def get_questions() -> list[dict[str, object]]:
        """Return the sample GAIA-inspired questions for the sidebar."""
        return SAMPLE_QUESTIONS

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        """Health check — also shows which model is configured.

        `alb_auth` tells the frontend whether the load balancer runs the
        Cognito login (Option A), so the sidebar can show a Log-out link
        even though the app's own auth is disabled.
        """
        return {
            "status": "ok",
            "model": settings.bedrock_model_id,
            "region": settings.aws_region,
            "alb_auth": settings.alb_auth_enabled,
        }

    @app.get("/logout")
    async def logout() -> RedirectResponse:
        """Log out of an ALB edge-auth (Cognito) deployment.

        The load balancer runs the login, so a genuine logout must BOTH
        expire the ALB session cookies AND end the Cognito hosted-UI session
        — otherwise the ALB silently re-authenticates on the next request.
        Falls back to a plain redirect home when ALB auth isn't configured.
        """
        if settings.alb_cognito_domain and settings.alb_cognito_client_id:
            target = f"{settings.alb_cognito_domain}/logout?" + urlencode(
                {
                    "client_id": settings.alb_cognito_client_id,
                    "logout_uri": settings.alb_post_logout_url or "/",
                }
            )
        else:
            target = "/"
        response = RedirectResponse(url=target, status_code=302)
        # Expire the ALB session cookies (chunked as -0, -1, ...).
        for i in range(4):
            response.delete_cookie(key=f"AWSELBAuthSessionCookie-{i}", path="/")
        clear_session(response)  # also drop the app's own session cookie, if any
        return response

    # ─── Generated image artefacts (forest plots, etc.) ────────────────────
    # Written by sandbox_exec into settings.images_dir; served read-only.
    # Mounted before the catch-all `/` so this prefix wins. In k8s,
    # settings.images_dir points at the shared-FS RWX mount.
    images_dir = Path(settings.images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=str(images_dir)), name="images")

    # ─── Static Files (React Frontend) ─────────────────────────────────────
    # Must be mounted AFTER the API routes so /api/* routes take precedence.
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/", _RevalidatingStatics(directory=str(static_dir), html=True), name="static")

    return app


# Module-level app instance for `uvicorn research_assistant.web.app:app`.
app = create_app()
