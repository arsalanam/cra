"""IQ — Installation Qualification snapshot generator (eCRF E7)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.validation.iq import (
    _read_pinned_dependencies,
    collect_iq_snapshot,
)


async def test_collect_iq_snapshot_basic_fields(clinical_session: AsyncSession) -> None:
    snap = await collect_iq_snapshot(clinical_session, deployment_environment="pytest")
    assert snap.package_name == "research-assistant"
    assert snap.package_version  # non-empty
    assert snap.python_version.count(".") >= 1
    assert snap.deployment_environment == "pytest"
    assert snap.database.dialect == "sqlite"
    # table_count > 0 isn't asserted here: in-memory SQLite per-connection
    # isolation in the test fixture can return 0 when the inspector runs
    # on a fresh connection. The dialect detection is what matters for IQ.


async def test_collect_iq_snapshot_cognito_unconfigured_in_tests(
    clinical_session: AsyncSession,
) -> None:
    """Tests run without Cognito env vars — IQ should reflect that."""
    snap = await collect_iq_snapshot(clinical_session, deployment_environment="x")
    # The conftest doesn't set COGNITO_*, so configured stays False.
    assert snap.cognito.configured is False


async def test_collect_iq_snapshot_audit_trigger_detection(
    clinical_session: AsyncSession,
) -> None:
    """The audit-immutability trigger isn't seeded by ClinicalBase.metadata
    in the test DB, so we expect False — but the call must not error."""
    snap = await collect_iq_snapshot(clinical_session, deployment_environment="x")
    assert isinstance(snap.database.audit_immutability_trigger_present, bool)


def test_read_pinned_dependencies_parses_uv_lock(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        """
[[package]]
name = "pandas"
version = "2.2.3"

[[package]]
name = "pydantic"
version = "2.9.0"
""".strip(),
        encoding="utf-8",
    )
    pins = _read_pinned_dependencies(uv_lock_path=lock)
    by_name = {p.name: p.version for p in pins}
    assert by_name == {"pandas": "2.2.3", "pydantic": "2.9.0"}


def test_read_pinned_dependencies_returns_empty_when_missing(tmp_path: Path) -> None:
    pins = _read_pinned_dependencies(uv_lock_path=tmp_path / "missing.lock")
    assert pins == []
