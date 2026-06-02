"""Cross-store helpers for resolving Trial / Account context (Sprint A2).

The account layer lives in the research DB (Account / ClinicalTrial /
EcrfStudy) while subject data lives in the clinical DB (StudyDeployment /
Subject / FormInstance / …). When a clinical-side surface (collector.html,
multi-site rollup, SDTM bundle download) needs to render the current
Trial — or auto-promote its status when the deployment is created /
locked / unlocked — the resolver walks:

    StudyDeployment.research_study_id   (by-value cross-store FK)
        → EcrfStudy.id
        → EcrfStudy.trial_id            (FK, may be NULL for legacy)
        → ClinicalTrial

Returns None for legacy deployments that pre-date the account layer
(EcrfStudy.trial_id IS NULL). Caller decides whether to fall back to
the Default Account or surface the missing-context gracefully.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..persistence.clinical.database import get_clinical_session
from ..persistence.clinical.models import StudyDeployment
from ..persistence.database import get_db_session
from ..persistence.models import (
    Account,
    ClinicalTrial,
    EcrfStudy,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def resolve_research_study_for_deployment(
    deployment_id: str,
    *,
    clinical_session: AsyncSession | None = None,
) -> str | None:
    """Pull the research_study_id off a StudyDeployment.

    Lets callers chain into resolve_trial_for_research_study without
    opening two sessions of their own. Returns None if the deployment
    doesn't exist.
    """
    if clinical_session is not None:
        deployment = await clinical_session.get(StudyDeployment, deployment_id)
        return deployment.research_study_id if deployment else None
    async with get_clinical_session() as cs:
        deployment = await cs.get(StudyDeployment, deployment_id)
        return deployment.research_study_id if deployment else None


async def resolve_trial_for_research_study(
    research_study_id: str,
    *,
    research_session: AsyncSession | None = None,
) -> ClinicalTrial | None:
    """Walk EcrfStudy → ClinicalTrial.

    Returns None if the EcrfStudy doesn't exist OR has no trial_id
    (legacy studies pre-account layer).
    """
    if research_session is not None:
        study = await research_session.get(EcrfStudy, research_study_id)
        if study is None or study.trial_id is None:
            return None
        return await research_session.get(ClinicalTrial, study.trial_id)
    async with get_db_session() as session:
        study = await session.get(EcrfStudy, research_study_id)
        if study is None or study.trial_id is None:
            return None
        return await session.get(ClinicalTrial, study.trial_id)


async def resolve_trial_for_deployment(
    deployment_id: str,
) -> ClinicalTrial | None:
    """End-to-end resolver: deployment → trial.

    Opens both DB sessions internally. Caller gets a detached
    ClinicalTrial instance (the session has closed) — safe to read
    .id / .title / .status / artefact-thread links, but lazy
    relationships will not load. Use the repository directly for
    relationship-bearing reads.
    """
    research_study_id = await resolve_research_study_for_deployment(deployment_id)
    if research_study_id is None:
        return None
    return await resolve_trial_for_research_study(research_study_id)


async def resolve_account_for_deployment(
    deployment_id: str,
) -> Account | None:
    """Walk all the way to the owning Account."""
    trial = await resolve_trial_for_deployment(deployment_id)
    if trial is None:
        return None
    async with get_db_session() as session:
        return await session.get(Account, trial.account_id)


async def list_deployments_for_trial(trial_id: str) -> list[str]:
    """Return every clinical-DB StudyDeployment.id whose research_study
    points at one of the trial's EcrfStudies.

    Used by the trial-detail endpoint to count + list deployments, and
    by the auto-status hook to know when 'design' should flip to
    'deployed'.
    """
    async with get_db_session() as session:
        study_ids = list(
            (
                await session.scalars(select(EcrfStudy.id).where(EcrfStudy.trial_id == trial_id))
            ).all()
        )
    if not study_ids:
        return []
    async with get_clinical_session() as cs:
        rows = await cs.scalars(
            select(StudyDeployment.id).where(StudyDeployment.research_study_id.in_(study_ids))
        )
        return list(rows.all())


__all__ = [
    "list_deployments_for_trial",
    "resolve_account_for_deployment",
    "resolve_research_study_for_deployment",
    "resolve_trial_for_deployment",
    "resolve_trial_for_research_study",
]
