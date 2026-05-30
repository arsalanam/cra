"""Orchestrator — runs SDTM → ADaM → TLF in one transaction.

Wires the mapper + ADSL deriver + TLF generator. Upserts every output
row in place: re-running for a deployment refreshes all derived rows
without leaving stale history. The `CdiscDerivation` row captures who
triggered the run + when, providing the audit trail.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..persistence.clinical.models import (
    AdamAdsl,
    AdverseEvent,
    CdiscDerivation,
    FormInstance,
    ItemData,
    SdtmAe,
    SdtmDm,
    SdtmVs,
    StudyDeployment,
    Subject,
    TlfArtefact,
)
from .adam_deriver import derive_adsl
from .sdtm_mapper import BuiltinPythonMapper, ItemMappingConfig
from .tlf_generator import generate_tlfs

logger = logging.getLogger(__name__)


@dataclass
class DerivationResult:
    deployment_id: str
    triggered_at: datetime
    counts: dict[str, int]


async def _gather_item_data(
    session: AsyncSession, subjects: list[Subject]
) -> dict[str, list[ItemData]]:
    """For each subject, collect every ItemData row across all their
    FormInstances. Heavy on small studies; for big studies the API
    endpoint can chunk it — out of scope for the MVP."""
    out: dict[str, list[ItemData]] = {s.id: [] for s in subjects}
    if not subjects:
        return out
    instances = (
        (
            await session.execute(
                select(FormInstance).where(
                    FormInstance.subject_id.in_([s.id for s in subjects])
                )
            )
        )
        .scalars()
        .all()
    )
    by_subj: dict[str, list[str]] = {}
    for fi in instances:
        by_subj.setdefault(fi.subject_id, []).append(fi.id)
    fi_ids: list[str] = [fi.id for fi in instances]
    if not fi_ids:
        return out
    items = (
        (
            await session.execute(
                select(ItemData).where(ItemData.form_instance_id.in_(fi_ids))
            )
        )
        .scalars()
        .all()
    )
    by_fi: dict[str, list[ItemData]] = {}
    for it in items:
        by_fi.setdefault(it.form_instance_id, []).append(it)
    for sid, fids in by_subj.items():
        for fid in fids:
            out[sid].extend(by_fi.get(fid, []))
    return out


async def run_derivation(
    session: AsyncSession,
    *,
    deployment_id: str,
    triggered_by: str | None = None,
    config: ItemMappingConfig | None = None,
) -> DerivationResult:
    """End-to-end derivation: clears prior outputs + materialises fresh.

    Caller is expected to wrap this in a transaction (the existing
    `get_clinical_session()` does). On exception the partial writes
    roll back together with the CdiscDerivation row, leaving the prior
    derivation intact.
    """
    deployment = await session.get(StudyDeployment, deployment_id)
    if deployment is None:
        raise ValueError(f"Deployment {deployment_id!r} not found.")
    study_id = deployment.research_study_id

    subjects: list[Subject] = list(
        (
            await session.execute(
                select(Subject).where(Subject.deployment_id == deployment_id)
            )
        )
        .scalars()
        .all()
    )
    aes: list[AdverseEvent] = list(
        (
            await session.execute(
                select(AdverseEvent).where(AdverseEvent.deployment_id == deployment_id)
            )
        )
        .scalars()
        .all()
    )
    item_data_by_subject = await _gather_item_data(session, subjects)

    # Wipe prior outputs for this deployment so the new run is the
    # current truth. Audit history of WHO ran derivations is preserved
    # in `cdisc_derivations`.
    for model in (SdtmDm, SdtmAe, SdtmVs, AdamAdsl, TlfArtefact):
        await session.execute(
            delete(model).where(model.deployment_id == deployment_id)
        )

    mapper = BuiltinPythonMapper()
    cfg = config or ItemMappingConfig()

    dm = mapper.derive_dm(
        deployment_id=deployment_id,
        study_id=study_id,
        subjects=subjects,
        item_data_by_subject=item_data_by_subject,
        config=cfg,
    )
    ae = mapper.derive_ae(
        deployment_id=deployment_id,
        study_id=study_id,
        adverse_events=aes,
        subjects_by_id={s.id: s for s in subjects},
    )
    vs = mapper.derive_vs(
        deployment_id=deployment_id,
        study_id=study_id,
        subjects=subjects,
        item_data_by_subject=item_data_by_subject,
        config=cfg,
    )
    adsl = derive_adsl(
        deployment_id=deployment_id,
        study_id=study_id,
        dm_records=dm,
        ae_records=ae,
        subject_item_data={s.subject_code: item_data_by_subject.get(s.id, []) for s in subjects},
    )
    tlfs = generate_tlfs(deployment_id=deployment_id, adsl=adsl, ae_records=ae)

    session.add_all(dm)
    session.add_all(ae)
    session.add_all(vs)
    session.add_all(adsl)
    session.add_all(tlfs)

    counts = {
        "dm": len(dm),
        "ae": len(ae),
        "vs": len(vs),
        "adsl": len(adsl),
        "tlf": len(tlfs),
    }
    triggered_at = datetime.now(UTC)
    derivation = CdiscDerivation(
        deployment_id=deployment_id,
        triggered_by=triggered_by,
        triggered_at=triggered_at,
        counts_json=json.dumps(counts),
    )
    session.add(derivation)
    await session.flush()
    logger.info(
        "CDISC derivation done: deployment=%s counts=%s by=%s",
        deployment_id,
        counts,
        triggered_by,
    )
    return DerivationResult(
        deployment_id=deployment_id,
        triggered_at=triggered_at,
        counts=counts,
    )


async def list_datasets(
    session: AsyncSession, deployment_id: str
) -> dict[str, Any]:
    """Summary view: last-derived timestamp + counts per dataset.

    Returns None values when nothing has been derived yet.
    """
    last_run = (
        (
            await session.execute(
                select(CdiscDerivation)
                .where(CdiscDerivation.deployment_id == deployment_id)
                .order_by(CdiscDerivation.triggered_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    payload: dict[str, Any] = {
        "deployment_id": deployment_id,
        "last_derived_at": last_run.triggered_at if last_run else None,
        "last_triggered_by": last_run.triggered_by if last_run else None,
        "counts": json.loads(last_run.counts_json) if last_run else {},
    }
    return payload


async def fetch_dm(
    session: AsyncSession, deployment_id: str
) -> list[SdtmDm]:
    return list(
        (
            await session.execute(
                select(SdtmDm)
                .where(SdtmDm.deployment_id == deployment_id)
                .order_by(SdtmDm.USUBJID)
            )
        )
        .scalars()
        .all()
    )


async def fetch_ae(
    session: AsyncSession, deployment_id: str
) -> list[SdtmAe]:
    return list(
        (
            await session.execute(
                select(SdtmAe)
                .where(SdtmAe.deployment_id == deployment_id)
                .order_by(SdtmAe.USUBJID, SdtmAe.AESEQ)
            )
        )
        .scalars()
        .all()
    )


async def fetch_vs(
    session: AsyncSession, deployment_id: str
) -> list[SdtmVs]:
    return list(
        (
            await session.execute(
                select(SdtmVs)
                .where(SdtmVs.deployment_id == deployment_id)
                .order_by(SdtmVs.USUBJID, SdtmVs.VSSEQ)
            )
        )
        .scalars()
        .all()
    )


async def fetch_adsl(
    session: AsyncSession, deployment_id: str
) -> list[AdamAdsl]:
    return list(
        (
            await session.execute(
                select(AdamAdsl)
                .where(AdamAdsl.deployment_id == deployment_id)
                .order_by(AdamAdsl.USUBJID)
            )
        )
        .scalars()
        .all()
    )


async def fetch_tlfs(
    session: AsyncSession, deployment_id: str
) -> list[TlfArtefact]:
    return list(
        (
            await session.execute(
                select(TlfArtefact)
                .where(TlfArtefact.deployment_id == deployment_id)
                .order_by(TlfArtefact.kind, TlfArtefact.tlf_id)
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "DerivationResult",
    "fetch_adsl",
    "fetch_ae",
    "fetch_dm",
    "fetch_tlfs",
    "fetch_vs",
    "list_datasets",
    "run_derivation",
]
