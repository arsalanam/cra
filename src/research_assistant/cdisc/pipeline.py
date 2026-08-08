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
    AdamAdtte,
    AdverseEvent,
    Allocation,
    CdiscDerivation,
    DeployedForm,
    DrugDispensation,
    DrugReturn,
    FormInstance,
    InvestigationalProduct,
    ItemData,
    SdtmAe,
    SdtmCm,
    SdtmDa,
    SdtmDm,
    SdtmEx,
    SdtmLb,
    SdtmMh,
    SdtmVs,
    StudyDeployment,
    Subject,
    TlfArtefact,
)
from .adam_deriver import derive_adsl
from .adtte_deriver import derive_adtte
from .sdtm_mapper import (
    BuiltinPythonMapper,
    FormInstanceWithItems,
    ItemMappingConfig,
)
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
                select(FormInstance).where(FormInstance.subject_id.in_([s.id for s in subjects]))
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
        (await session.execute(select(ItemData).where(ItemData.form_instance_id.in_(fi_ids))))
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


async def _gather_form_instances_by_domain(
    session: AsyncSession,
    deployment_id: str,
    form_to_domain_map: dict[str, str],
) -> dict[str, list[FormInstanceWithItems]]:
    """For each (form_name → SDTM domain) mapping, return the list of
    form-instances of that form within the deployment, paired with
    their item data. Used by the repeating-form derivers (LB / EX /
    CM / MH) — each form-instance becomes one SDTM row."""
    by_domain: dict[str, list[FormInstanceWithItems]] = {
        domain: [] for domain in set(form_to_domain_map.values())
    }
    if not form_to_domain_map:
        return by_domain

    # Match by case-insensitive form_name so the customer's casing
    # doesn't trip the lookup.
    name_to_domain = {k.lower(): v for k, v in form_to_domain_map.items()}

    deployed_forms = list(
        (
            await session.execute(
                select(DeployedForm).where(
                    DeployedForm.deployment_id == deployment_id,
                )
            )
        )
        .scalars()
        .all()
    )
    df_id_to_domain: dict[str, str] = {}
    for df in deployed_forms:
        domain = name_to_domain.get(df.form_name.lower())
        if domain:
            df_id_to_domain[df.id] = domain
    if not df_id_to_domain:
        return by_domain

    # Pull form_instances whose deployed_form is in the matched set.
    # Sort by created_at so per-subject sequencing is stable.
    instances = list(
        (
            await session.execute(
                select(FormInstance)
                .join(Subject, FormInstance.subject_id == Subject.id)
                .where(Subject.deployment_id == deployment_id)
                .where(FormInstance.deployed_form_id.in_(list(df_id_to_domain.keys())))
                .order_by(FormInstance.subject_id, FormInstance.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not instances:
        return by_domain
    fi_ids = [fi.id for fi in instances]
    items = list(
        (await session.execute(select(ItemData).where(ItemData.form_instance_id.in_(fi_ids))))
        .scalars()
        .all()
    )
    items_by_fi: dict[str, list[ItemData]] = {}
    for it in items:
        items_by_fi.setdefault(it.form_instance_id, []).append(it)
    for fi in instances:
        domain = df_id_to_domain.get(fi.deployed_form_id)
        if domain:
            by_domain[domain].append((fi, items_by_fi.get(fi.id, [])))
    return by_domain


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
        (await session.execute(select(Subject).where(Subject.deployment_id == deployment_id)))
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

    # Drug accountability (SDTM DA) source rows: dispensations + returns +
    # the IP catalogue for units.
    dispensations: list[DrugDispensation] = list(
        (
            await session.scalars(
                select(DrugDispensation).where(DrugDispensation.deployment_id == deployment_id)
            )
        ).all()
    )
    drug_returns: list[DrugReturn] = list(
        (
            await session.scalars(
                select(DrugReturn).where(DrugReturn.deployment_id == deployment_id)
            )
        ).all()
    )
    units_by_ip_id: dict[str, str] = {
        ip.id: ip.units
        for ip in (
            await session.scalars(
                select(InvestigationalProduct).where(
                    InvestigationalProduct.deployment_id == deployment_id
                )
            )
        ).all()
    }

    # IRT (E8): pull Allocation rows so ADSL/ADTTE can populate
    # TRT01P / TRT01A from the audited randomisation assignment rather
    # than the "TBD" placeholder. Map subject_id → Allocation.
    allocation_rows: list[Allocation] = list(
        (await session.execute(select(Allocation).where(Allocation.deployment_id == deployment_id)))
        .scalars()
        .all()
    )
    allocations_by_subject_id: dict[str, Allocation] = {a.subject_id: a for a in allocation_rows}
    subjects_by_id_full = {s.id: s for s in subjects}
    allocations_by_usubjid: dict[str, Allocation] = {}
    for subj_id, allocation in allocations_by_subject_id.items():
        subj = subjects_by_id_full.get(subj_id)
        if subj is None:
            continue
        usubjid = f"{deployment.research_study_id}-{subj.subject_code}"
        allocations_by_usubjid[usubjid] = allocation

    mapper = BuiltinPythonMapper()
    cfg = config or ItemMappingConfig()

    # Repeating-form domains (LB / EX / CM / MH) are gathered per the
    # customer's form_to_domain_map and emit one SDTM row per
    # form-instance.
    form_instances_by_domain = await _gather_form_instances_by_domain(
        session, deployment_id, cfg.form_to_domain_map
    )

    # Wipe prior outputs for this deployment so the new run is the
    # current truth. Audit history of WHO ran derivations is preserved
    # in `cdisc_derivations`.
    for model in (
        SdtmDm,
        SdtmAe,
        SdtmVs,
        SdtmLb,
        SdtmEx,
        SdtmCm,
        SdtmMh,
        SdtmDa,
        AdamAdsl,
        AdamAdtte,
        TlfArtefact,
    ):
        await session.execute(delete(model).where(model.deployment_id == deployment_id))

    subjects_by_id = {s.id: s for s in subjects}

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
        subjects_by_id=subjects_by_id,
    )
    vs = mapper.derive_vs(
        deployment_id=deployment_id,
        study_id=study_id,
        subjects=subjects,
        item_data_by_subject=item_data_by_subject,
        config=cfg,
    )
    lb = mapper.derive_lb(
        deployment_id=deployment_id,
        study_id=study_id,
        subjects_by_id=subjects_by_id,
        form_instances=form_instances_by_domain.get("LB", []),
        config=cfg,
    )
    # P2 #6 lab-data feeds: extend the LB domain with any parsed lab
    # results that have a linked subject. The form-based path remains
    # the source of truth for studies that don't have central lab
    # feeds; parsed labs cascade additively without disturbing it.
    from ..persistence.clinical.models import LabResult

    lab_results_rows = list(
        (
            await session.scalars(
                select(LabResult).where(
                    LabResult.deployment_id == deployment_id,
                    LabResult.subject_id.is_not(None),
                )
            )
        ).all()
    )
    if lab_results_rows:
        starting_seq: dict[str, int] = {}
        for row in lb:
            # Find the Subject.id for the existing LB row's USUBJID.
            # Cheaper than another DB hit: walk subjects_by_id once.
            for sid, subj in subjects_by_id.items():
                if f"{study_id}-{subj.subject_code}" == row.USUBJID:
                    starting_seq[sid] = max(starting_seq.get(sid, 0), row.LBSEQ)
                    break
        lb_from_labs = mapper.derive_lb_from_lab_results(
            deployment_id=deployment_id,
            study_id=study_id,
            subjects_by_id=subjects_by_id,
            lab_results=lab_results_rows,
            starting_seq_by_subject=starting_seq,
        )
        lb = list(lb) + lb_from_labs
    ex = mapper.derive_ex(
        deployment_id=deployment_id,
        study_id=study_id,
        subjects_by_id=subjects_by_id,
        form_instances=form_instances_by_domain.get("EX", []),
        config=cfg,
    )
    cm = mapper.derive_cm(
        deployment_id=deployment_id,
        study_id=study_id,
        subjects_by_id=subjects_by_id,
        form_instances=form_instances_by_domain.get("CM", []),
        config=cfg,
    )
    mh = mapper.derive_mh(
        deployment_id=deployment_id,
        study_id=study_id,
        subjects_by_id=subjects_by_id,
        form_instances=form_instances_by_domain.get("MH", []),
        config=cfg,
    )
    da = mapper.derive_da(
        deployment_id=deployment_id,
        study_id=study_id,
        dispensations=dispensations,
        returns=drug_returns,
        subjects_by_id=subjects_by_id,
        units_by_ip_id=units_by_ip_id,
    )
    adsl = derive_adsl(
        deployment_id=deployment_id,
        study_id=study_id,
        dm_records=dm,
        ae_records=ae,
        subject_item_data={s.subject_code: item_data_by_subject.get(s.id, []) for s in subjects},
        allocations_by_usubjid=allocations_by_usubjid,
    )
    adtte = derive_adtte(
        deployment_id=deployment_id,
        study_id=study_id,
        adsl=adsl,
        ae_records=ae,
    )
    tlfs = generate_tlfs(
        deployment_id=deployment_id,
        adsl=adsl,
        ae_records=ae,
        adtte_records=adtte,
    )

    session.add_all(dm)
    session.add_all(ae)
    session.add_all(vs)
    session.add_all(lb)
    session.add_all(ex)
    session.add_all(cm)
    session.add_all(mh)
    session.add_all(da)
    session.add_all(adsl)
    session.add_all(adtte)
    session.add_all(tlfs)

    counts = {
        "dm": len(dm),
        "ae": len(ae),
        "vs": len(vs),
        "lb": len(lb),
        "ex": len(ex),
        "cm": len(cm),
        "mh": len(mh),
        "da": len(da),
        "adsl": len(adsl),
        "adtte": len(adtte),
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


async def list_datasets(session: AsyncSession, deployment_id: str) -> dict[str, Any]:
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


async def fetch_dm(session: AsyncSession, deployment_id: str) -> list[SdtmDm]:
    return list(
        (
            await session.execute(
                select(SdtmDm).where(SdtmDm.deployment_id == deployment_id).order_by(SdtmDm.USUBJID)
            )
        )
        .scalars()
        .all()
    )


async def fetch_ae(session: AsyncSession, deployment_id: str) -> list[SdtmAe]:
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


async def fetch_vs(session: AsyncSession, deployment_id: str) -> list[SdtmVs]:
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


async def fetch_adsl(session: AsyncSession, deployment_id: str) -> list[AdamAdsl]:
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


async def fetch_adtte(session: AsyncSession, deployment_id: str) -> list[AdamAdtte]:
    return list(
        (
            await session.execute(
                select(AdamAdtte)
                .where(AdamAdtte.deployment_id == deployment_id)
                .order_by(AdamAdtte.USUBJID, AdamAdtte.PARAMCD)
            )
        )
        .scalars()
        .all()
    )


async def fetch_tlfs(session: AsyncSession, deployment_id: str) -> list[TlfArtefact]:
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


async def fetch_lb(session: AsyncSession, deployment_id: str) -> list[SdtmLb]:
    return list(
        (
            await session.execute(
                select(SdtmLb)
                .where(SdtmLb.deployment_id == deployment_id)
                .order_by(SdtmLb.USUBJID, SdtmLb.LBSEQ)
            )
        )
        .scalars()
        .all()
    )


async def fetch_ex(session: AsyncSession, deployment_id: str) -> list[SdtmEx]:
    return list(
        (
            await session.execute(
                select(SdtmEx)
                .where(SdtmEx.deployment_id == deployment_id)
                .order_by(SdtmEx.USUBJID, SdtmEx.EXSEQ)
            )
        )
        .scalars()
        .all()
    )


async def fetch_cm(session: AsyncSession, deployment_id: str) -> list[SdtmCm]:
    return list(
        (
            await session.execute(
                select(SdtmCm)
                .where(SdtmCm.deployment_id == deployment_id)
                .order_by(SdtmCm.USUBJID, SdtmCm.CMSEQ)
            )
        )
        .scalars()
        .all()
    )


async def fetch_mh(session: AsyncSession, deployment_id: str) -> list[SdtmMh]:
    return list(
        (
            await session.execute(
                select(SdtmMh)
                .where(SdtmMh.deployment_id == deployment_id)
                .order_by(SdtmMh.USUBJID, SdtmMh.MHSEQ)
            )
        )
        .scalars()
        .all()
    )


async def fetch_da(session: AsyncSession, deployment_id: str) -> list[SdtmDa]:
    return list(
        (
            await session.execute(
                select(SdtmDa)
                .where(SdtmDa.deployment_id == deployment_id)
                .order_by(SdtmDa.USUBJID, SdtmDa.DASEQ)
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "DerivationResult",
    "fetch_adsl",
    "fetch_adtte",
    "fetch_ae",
    "fetch_cm",
    "fetch_da",
    "fetch_dm",
    "fetch_ex",
    "fetch_lb",
    "fetch_mh",
    "fetch_tlfs",
    "fetch_vs",
    "list_datasets",
    "run_derivation",
]
