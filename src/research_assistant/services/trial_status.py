"""Auto-status promotion for the account layer (Sprint A2).

Wires the three lifecycle events that should advance a ClinicalTrial's
status without operator effort:

  EcrfStudy first form publish    →  Trial.status='design'  ⇒  'draft'
  StudyDeployment create          →  Trial.status='draft'   ⇒  'deployed'
                                     (or 'design' ⇒ 'deployed' if study
                                      had no published form, e.g. fast-path)
  StudyLock lock                  →  Trial.status='deployed' ⇒ 'locked'
  StudyLock unlock                →  Trial.status='locked'   ⇒ 'deployed'

Each helper is called by the endpoint layer AFTER the relevant repo
method succeeds. They open their own research-DB session, never touch
the clinical DB, and are idempotent — a no-op when the trial is
already at or past the target state. Status never regresses (an
already-locked trial won't get bumped back to draft on a fresh form
publish).

Lifecycle order: design ⊂ draft ⊂ deployed ⊂ locked. Archived is the
terminal terminal — auto-status NEVER touches archived trials.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..persistence.database import get_db_session
from ..persistence.models import ClinicalTrial, EcrfStudy
from .trial_context import (
    resolve_trial_for_deployment,
    resolve_trial_for_research_study,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Status ordering. Higher index = more advanced. Used to decide whether
# a proposed status change is an advance (apply) or a regression (skip).
_STATUS_ORDER = ("design", "draft", "deployed", "locked")


def _can_advance_to(current: str, target: str) -> bool:
    """True when moving from current → target is a forward step.

    Archived trials are immutable here; the auto-status hooks must
    never touch them.
    """
    if current == "archived":
        return False
    if current not in _STATUS_ORDER or target not in _STATUS_ORDER:
        return False
    return _STATUS_ORDER.index(target) > _STATUS_ORDER.index(current)


async def _set_trial_status(
    trial_id: str,
    *,
    target: str,
    session: AsyncSession | None = None,
) -> bool:
    """Set the trial's status to `target` only if it would advance.

    Returns True if the status changed, False otherwise (no-op). When
    `session` is supplied (tests, batched callers) the helper reuses it
    and doesn't commit; otherwise it opens its own session + commits.
    """
    if session is not None:
        trial = await session.get(ClinicalTrial, trial_id)
        if trial is None:
            return False
        if not _can_advance_to(trial.status, target):
            return False
        trial.status = target
        await session.flush()
        logger.info("Auto-status: trial %s advanced to %s", trial_id, target)
        return True
    async with get_db_session() as own_session:
        trial = await own_session.get(ClinicalTrial, trial_id)
        if trial is None:
            return False
        if not _can_advance_to(trial.status, target):
            return False
        trial.status = target
        await own_session.commit()
        logger.info("Auto-status: trial %s advanced to %s", trial_id, target)
        return True


async def promote_to_draft_for_study(research_study_id: str) -> bool:
    """Called by web/ecrf.py after EcrfRepository.publish_form succeeds.

    Returns True when the trial actually advanced. Cheap no-op when the
    trial is missing, already deployed, or further along.
    """
    trial = await resolve_trial_for_research_study(research_study_id)
    if trial is None:
        return False
    return await _set_trial_status(trial.id, target="draft")


async def promote_to_deployed_for_deployment(deployment_id: str) -> bool:
    """Called by web/edc.py after ClinicalRepository.deploy_study succeeds.

    Skips work for legacy deployments whose EcrfStudy has no trial_id.
    """
    trial = await resolve_trial_for_deployment(deployment_id)
    if trial is None:
        return False
    return await _set_trial_status(trial.id, target="deployed")


async def promote_to_locked_for_deployment(deployment_id: str) -> bool:
    """Called by web/edc.py after ClinicalRepository.lock_study succeeds."""
    trial = await resolve_trial_for_deployment(deployment_id)
    if trial is None:
        return False
    return await _set_trial_status(trial.id, target="locked")


async def demote_to_deployed_for_deployment(deployment_id: str) -> bool:
    """Called by web/edc.py after ClinicalRepository.unlock_study succeeds.

    Allows a one-step regression (locked → deployed). Doesn't touch
    archived trials. Returns True only when the trial was previously
    locked and is now demoted.
    """
    trial = await resolve_trial_for_deployment(deployment_id)
    if trial is None:
        return False
    async with get_db_session() as session:
        t = await session.get(ClinicalTrial, trial.id)
        if t is None:
            return False
        if t.status != "locked":
            return False
        t.status = "deployed"
        await session.commit()
        logger.info(
            "Auto-status: trial %s demoted to deployed (unlock)",
            trial.id,
        )
        return True


async def find_trial_for_ecrf_form(form_id: str) -> ClinicalTrial | None:
    """Walk EcrfFormDefinition → EcrfStudy → ClinicalTrial. Used by the
    web/ecrf.py publish endpoint."""
    from ..persistence.models import EcrfFormDefinition

    async with get_db_session() as session:
        form = await session.get(EcrfFormDefinition, form_id)
        if form is None:
            return None
        study = await session.get(EcrfStudy, form.study_id)
        if study is None or study.trial_id is None:
            return None
        return await session.get(ClinicalTrial, study.trial_id)


__all__ = [
    "demote_to_deployed_for_deployment",
    "find_trial_for_ecrf_form",
    "promote_to_deployed_for_deployment",
    "promote_to_draft_for_study",
    "promote_to_locked_for_deployment",
]
