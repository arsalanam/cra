"""IQ — Installation Qualification snapshot generator (eCRF E7).

A request-time snapshot of the running system, persisted as a Pydantic
model so the PDF renderer (`reports/validation_pack.py`) can convert
it into the human-facing IQ artefact. The fields here match a real
IQ checklist: what software is installed, at what versions, against
which configured backends, with which security controls active.

Secrets (AWS keys, Cognito client_secret) are NEVER captured — only
identifiers + non-secret config.
"""

from __future__ import annotations

import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__ as _package_version
from ..config import get_settings


class DependencyPin(BaseModel):
    name: str
    version: str


class DatabaseSnapshot(BaseModel):
    """Captured DB state relevant for IQ.

    `audit_immutability_trigger_present` reflects the E6 DB-level
    immutability trigger; when False, the installation is degraded
    (the application-level audit invariant still holds, but the
    second line of defence is missing).
    """

    dialect: str
    table_count: int
    audit_immutability_trigger_present: bool
    schema_version: str | None = None


class CognitoSnapshot(BaseModel):
    """Identity-provider configuration (non-secret fields only)."""

    configured: bool
    region: str | None = None
    user_pool_id: str | None = None
    client_id: str | None = None
    domain: str | None = None


class FeatureFlagsSnapshot(BaseModel):
    """Which compliance-relevant features are enabled at install time."""

    auth_enabled: bool
    sandbox_enabled: bool


class InstallationSnapshot(BaseModel):
    """The complete IQ artefact — rendered to PDF by `reports/validation_pack.py`."""

    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    package_name: str = "research-assistant"
    package_version: str
    python_version: str
    platform: str
    host_name: str
    deployment_environment: str
    cognito: CognitoSnapshot
    database: DatabaseSnapshot
    dependencies: list[DependencyPin]
    feature_flags: FeatureFlagsSnapshot


def _read_pinned_dependencies(uv_lock_path: Path | None = None) -> list[DependencyPin]:
    """Best-effort pin extraction from `uv.lock` — the regulator-evidence
    case is "the install matches the locked manifest", so this captures
    the *locked* versions rather than what's resolved at runtime.

    Returns an empty list if `uv.lock` cannot be read, rather than
    crashing — the PDF renderer notes the absence.
    """
    if uv_lock_path is None:
        uv_lock_path = Path(__file__).resolve().parents[3] / "uv.lock"
    if not uv_lock_path.exists():
        return []
    pins: list[DependencyPin] = []
    current_name: str | None = None
    for line in uv_lock_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("name = "):
            current_name = s.split("=", 1)[1].strip().strip('"')
        elif s.startswith("version = ") and current_name is not None:
            version = s.split("=", 1)[1].strip().strip('"')
            pins.append(DependencyPin(name=current_name, version=version))
            current_name = None
    return pins


async def _audit_trigger_present(s: AsyncSession) -> bool:
    """Detect the E6 audit-immutability trigger.

    Implementation differs per dialect: SQLite stores triggers in
    `sqlite_master`; PostgreSQL surfaces them via `pg_trigger`. The
    name we look for is `audit_entries_immutable` (the E6 build named
    the trigger that way for both dialects).
    """
    dialect = s.bind.dialect.name if s.bind is not None else ""
    try:
        if dialect == "sqlite":
            row = await s.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'audit_entries_%' LIMIT 1"
                )
            )
            return row.first() is not None
        if dialect == "postgresql":
            row = await s.execute(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgname LIKE 'audit_entries_%' AND NOT tgisinternal LIMIT 1"
                )
            )
            return row.first() is not None
    except Exception:
        return False
    return False


async def _database_snapshot(s: AsyncSession) -> DatabaseSnapshot:
    bind = s.bind
    dialect = bind.dialect.name if bind is not None else "unknown"

    def _tables(sync_conn: object) -> int:
        inspector = inspect(sync_conn)
        if inspector is None:  # defensive — sqlalchemy.inspect never returns None for a connection
            return 0
        return len(inspector.get_table_names())

    try:
        table_count = await s.run_sync(_tables)
    except Exception:
        table_count = 0
    trigger_ok = await _audit_trigger_present(s)
    return DatabaseSnapshot(
        dialect=dialect,
        table_count=table_count,
        audit_immutability_trigger_present=trigger_ok,
    )


def _cognito_snapshot() -> CognitoSnapshot:
    settings = get_settings()
    configured = bool(settings.cognito_user_pool_id and settings.cognito_client_id)
    if not configured:
        return CognitoSnapshot(configured=False)
    return CognitoSnapshot(
        configured=True,
        region=settings.cognito_region,
        user_pool_id=settings.cognito_user_pool_id,
        client_id=settings.cognito_client_id,
        domain=settings.cognito_domain or None,
    )


def _feature_flags_snapshot() -> FeatureFlagsSnapshot:
    settings = get_settings()
    return FeatureFlagsSnapshot(
        auth_enabled=settings.auth_enabled,
        sandbox_enabled=getattr(settings, "sandbox_enabled", False),
    )


async def collect_iq_snapshot(
    s: AsyncSession,
    *,
    deployment_environment: str = "unknown",
    uv_lock_path: Path | None = None,
) -> InstallationSnapshot:
    """Build the IQ artefact at request time.

    `deployment_environment` is a free-text label captured per install
    ("dev" / "staging" / "prod" / customer name). The endpoint passes
    it through from a request parameter so the IQ pack can be filed
    against the correct environment.
    """
    return InstallationSnapshot(
        package_version=_package_version,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        host_name=platform.node(),
        deployment_environment=deployment_environment,
        cognito=_cognito_snapshot(),
        database=await _database_snapshot(s),
        dependencies=_read_pinned_dependencies(uv_lock_path),
        feature_flags=_feature_flags_snapshot(),
    )


__all__ = [
    "CognitoSnapshot",
    "DatabaseSnapshot",
    "DependencyPin",
    "FeatureFlagsSnapshot",
    "InstallationSnapshot",
    "collect_iq_snapshot",
]
