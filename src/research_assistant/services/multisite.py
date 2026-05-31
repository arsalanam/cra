"""Central-coordinator multi-site rollup (P2 #5).

Pure read-side aggregations over the existing ClinicalBase tables —
no new persistence, no new columns. Composes existing repository
methods + targeted SELECTs so a central coordinator / monitor / DM
can see every site's status on one page.

Two tiers:

  • `site_rollup(deployment_id)` — every site within one deployment.
    Used by coordinators / DM / PI / monitor / auditor on the
    per-deployment view.
  • `org_rollup()` — every deployment (with embedded site cards) plus
    org-wide totals. Admin-only at the endpoint layer (gated by the
    existing `portfolio.read_org` permission).

The four KPI groups per site card:

  • Enrolment funnel — counts from ScreeningLog (screened / eligible /
    consented / enrolled).
  • Query backlog — counts from Query by status (open / answered /
    closed), filtered to forms whose subject is on this site.
  • Safety — counts of open AEs, open serious AEs, open deviations,
    open CAPAs.
  • Operational — overdue planned visits (window_end < now,
    status=pending), low-IP lots (current inventory < threshold),
    last-SDV timestamp.

`overdue_visit` definition: status='pending' AND window_end < now.
`low_ip_threshold` is a runtime knob (default 10 units) so the
rollup adapts to studies that ship in larger or smaller lots.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..persistence.clinical.models import (
    AdverseEvent,
    CapaAction,
    FormInstance,
    PlannedVisit,
    ProtocolDeviation,
    Query,
    Site,
    StudyDeployment,
    Subject,
    Verification,
)
from ..persistence.clinical.repository import ClinicalRepository

logger = logging.getLogger(__name__)


# Default low-IP threshold — surfaces lots with current inventory below
# this so a monitor can flag re-supply. Operators can tune by passing
# `low_ip_threshold` to the endpoint when ordering bigger / smaller
# kits.
DEFAULT_LOW_IP_THRESHOLD = 10


# ── DTOs (plain dataclasses, serialised by the endpoint DTO layer) ──────


@dataclass
class EnrolmentBlock:
    screened: int = 0
    eligible: int = 0
    consented: int = 0
    enrolled: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "screened": self.screened,
            "eligible": self.eligible,
            "consented": self.consented,
            "enrolled": self.enrolled,
        }


@dataclass
class QueryBlock:
    open: int = 0
    answered: int = 0
    closed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"open": self.open, "answered": self.answered, "closed": self.closed}


@dataclass
class SafetyBlock:
    open_aes: int = 0
    open_serious_aes: int = 0
    open_deviations: int = 0
    open_capas: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "open_aes": self.open_aes,
            "open_serious_aes": self.open_serious_aes,
            "open_deviations": self.open_deviations,
            "open_capas": self.open_capas,
        }


@dataclass
class OperationalBlock:
    overdue_visits: int = 0
    low_ip_lots: list[str] = field(default_factory=list)
    last_sdv_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "overdue_visits": self.overdue_visits,
            "low_ip_lots": list(self.low_ip_lots),
            "last_sdv_at": self.last_sdv_at,
        }


@dataclass
class SiteCard:
    site_id: str | None  # None = "(unassigned)" bucket
    site_name: str
    site_code: str | None
    n_subjects: int = 0
    enrolment: EnrolmentBlock = field(default_factory=EnrolmentBlock)
    queries: QueryBlock = field(default_factory=QueryBlock)
    safety: SafetyBlock = field(default_factory=SafetyBlock)
    operational: OperationalBlock = field(default_factory=OperationalBlock)

    def as_dict(self) -> dict[str, object]:
        return {
            "site_id": self.site_id,
            "site_name": self.site_name,
            "site_code": self.site_code,
            "n_subjects": self.n_subjects,
            "enrolment": self.enrolment.as_dict(),
            "queries": self.queries.as_dict(),
            "safety": self.safety.as_dict(),
            "operational": self.operational.as_dict(),
        }


@dataclass
class DeploymentCard:
    deployment_id: str
    deployment_name: str
    status: str
    n_sites: int
    n_subjects: int
    site_totals: SiteCard  # Aggregate row across the deployment.
    sites: list[SiteCard] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "deployment_name": self.deployment_name,
            "status": self.status,
            "n_sites": self.n_sites,
            "n_subjects": self.n_subjects,
            "site_totals": self.site_totals.as_dict(),
            "sites": [s.as_dict() for s in self.sites],
        }


# ── Per-deployment rollup ────────────────────────────────────────────────


async def site_rollup(
    session: AsyncSession,
    *,
    deployment_id: str,
    low_ip_threshold: int = DEFAULT_LOW_IP_THRESHOLD,
    now: datetime | None = None,
) -> DeploymentCard:
    """Aggregate every site within a single deployment into a
    DeploymentCard.

    `now` is injectable so tests can pin "overdue" cleanly. Production
    callers leave it as `datetime.now(UTC)`.
    """
    now = now or datetime.now(UTC)
    deployment = await session.get(StudyDeployment, deployment_id)
    if deployment is None:
        raise ValueError(f"Deployment {deployment_id!r} not found.")

    sites = list(
        (
            await session.scalars(
                select(Site).where(Site.deployment_id == deployment_id).order_by(Site.created_at)
            )
        ).all()
    )

    # Subjects per site.
    subjects = list(
        (await session.scalars(select(Subject).where(Subject.deployment_id == deployment_id))).all()
    )
    subjects_by_site: dict[str | None, list[Subject]] = {}
    for s in subjects:
        subjects_by_site.setdefault(s.site_id, []).append(s)

    # Enrolment funnel per site — reuses the existing recruitment_funnel
    # method by calling it once per site. Cheap; ScreeningLog volume is
    # bounded by per-deployment lifetime screening (low thousands).
    repo = ClinicalRepository(session)
    funnel_by_site: dict[str, dict[str, int]] = {}
    for site in sites:
        funnel = await repo.recruitment_funnel(deployment_id, site_id=site.id)
        funnel_by_site[site.id] = funnel["totals"]  # type: ignore[assignment]

    # Queries per site — JOIN through form_instance → subject → site.
    # status counts are computed in-Python from the row list so the
    # same query stream feeds open / answered / closed without three
    # separate round-trips.
    query_rows = list(
        (
            await session.execute(
                select(Query.status, Subject.site_id)
                .join(FormInstance, Query.form_instance_id == FormInstance.id)
                .join(Subject, FormInstance.subject_id == Subject.id)
                .where(Subject.deployment_id == deployment_id)
            )
        ).all()
    )
    queries_by_site: dict[str | None, QueryBlock] = {}
    for status, site_id in query_rows:
        qb = queries_by_site.setdefault(site_id, QueryBlock())
        if status == "open":
            qb.open += 1
        elif status == "answered":
            qb.answered += 1
        elif status == "closed":
            qb.closed += 1

    # Safety: open AEs (resolved/recovered/recovering/death/unknown =
    # "outcome" field; "open" means outcome NOT one of the closed set).
    # MVP: count AEs whose outcome is NULL or 'ongoing' as open.
    ae_rows = list(
        (
            await session.execute(
                select(AdverseEvent.outcome, AdverseEvent.severity_grade, Subject.site_id)
                .join(Subject, AdverseEvent.subject_id == Subject.id)
                .where(AdverseEvent.deployment_id == deployment_id)
            )
        ).all()
    )
    # An AE is "open" until the subject explicitly recovers or the AE
    # ends in death (which has its own follow-up but is not a triage
    # item for the monitor). `recovering` is mid-resolution and stays
    # on the open list. `unknown` is treated as open because the
    # monitor still needs to chase down a clinical update.
    closed_ae_outcomes = {"recovered", "death"}
    safety_by_site: dict[str | None, SafetyBlock] = {}
    for outcome, severity, site_id in ae_rows:
        sb = safety_by_site.setdefault(site_id, SafetyBlock())
        is_open = outcome not in closed_ae_outcomes
        if is_open:
            sb.open_aes += 1
            if severity is not None and severity >= 3:
                sb.open_serious_aes += 1

    # Open deviations + open CAPAs per site (deviations are deployment-
    # scoped; subject_id is nullable). Subject-less deviations show up
    # on the "(unassigned)" bucket.
    deviation_rows = list(
        (
            await session.execute(
                select(
                    ProtocolDeviation.status,
                    ProtocolDeviation.subject_id,
                ).where(ProtocolDeviation.deployment_id == deployment_id)
            )
        ).all()
    )
    subject_site_lookup: dict[str, str | None] = {subj.id: subj.site_id for subj in subjects}
    for dev_status, dev_subject_id in deviation_rows:
        dev_site_id = subject_site_lookup.get(dev_subject_id) if dev_subject_id else None
        dev_bucket = safety_by_site.setdefault(dev_site_id, SafetyBlock())
        if dev_status in ("open", "under_capa"):
            dev_bucket.open_deviations += 1
    deviation_id_query = list(
        (
            await session.scalars(
                select(ProtocolDeviation.id).where(ProtocolDeviation.deployment_id == deployment_id)
            )
        ).all()
    )
    deviation_ids: list[str] = list(deviation_id_query)
    if deviation_ids:
        capa_rows = list(
            (
                await session.execute(
                    select(CapaAction.status, ProtocolDeviation.subject_id)
                    .join(
                        ProtocolDeviation,
                        CapaAction.deviation_id == ProtocolDeviation.id,
                    )
                    .where(CapaAction.deviation_id.in_(deviation_ids))
                )
            ).all()
        )
        for capa_status, capa_subject_id in capa_rows:
            capa_site_id = subject_site_lookup.get(capa_subject_id) if capa_subject_id else None
            capa_bucket = safety_by_site.setdefault(capa_site_id, SafetyBlock())
            if capa_status == "open":
                capa_bucket.open_capas += 1

    # Operational — overdue planned visits per site.
    visit_rows = list(
        (
            await session.execute(
                select(
                    PlannedVisit.status,
                    PlannedVisit.window_end,
                    Subject.site_id,
                )
                .join(Subject, PlannedVisit.subject_id == Subject.id)
                .where(Subject.deployment_id == deployment_id)
            )
        ).all()
    )
    # SQLite test fixtures hand back naive datetimes; Postgres hands back
    # aware. Normalise both sides of the comparison to a naive UTC
    # offset so the test path and the runtime path use the same code.
    now_naive = now.replace(tzinfo=None) if now.tzinfo is not None else now
    overdue_by_site: dict[str | None, int] = {}
    for status, window_end, site_id in visit_rows:
        if status != "pending" or window_end is None:
            continue
        we = window_end.replace(tzinfo=None) if window_end.tzinfo is not None else window_end
        if we < now_naive:
            overdue_by_site[site_id] = overdue_by_site.get(site_id, 0) + 1

    # IP reconciliation feeds the low-stock list. Per-lot rollup is
    # deployment-scoped (not site-scoped) — surfaced under "(unassigned)"
    # in the site cards but rolled into the deployment-totals block.
    deployment_low_lots: list[str] = []
    try:
        recon = await repo.drug_reconciliation(deployment_id)
        by_lot: dict[str, dict[str, int]] = recon["by_lot"]  # type: ignore[assignment]
        for lot_key, b in by_lot.items():
            inv = int(b.get("current_inventory", 0))
            if inv < low_ip_threshold:
                deployment_low_lots.append(lot_key)
    except Exception:
        logger.exception("multisite: drug_reconciliation failed for %s", deployment_id)

    # Last SDV: max(Verification.verified_at) across all form instances
    # whose subject is on the site.
    sdv_rows = list(
        (
            await session.execute(
                select(
                    Subject.site_id,
                    func.max(Verification.verified_at),
                )
                .join(FormInstance, Verification.form_instance_id == FormInstance.id)
                .join(Subject, FormInstance.subject_id == Subject.id)
                .where(Subject.deployment_id == deployment_id)
                .group_by(Subject.site_id)
            )
        ).all()
    )
    last_sdv_by_site: dict[str | None, datetime] = {
        site_id: verified_at for site_id, verified_at in sdv_rows
    }

    # Compose site cards.
    site_cards: list[SiteCard] = []
    totals = SiteCard(
        site_id=None,
        site_name="(total)",
        site_code=None,
    )
    for site in sites:
        funnel_totals = funnel_by_site.get(
            site.id, {"screened": 0, "eligible": 0, "consented": 0, "enrolled": 0}
        )
        enrol = EnrolmentBlock(
            screened=funnel_totals["screened"],
            eligible=funnel_totals["eligible"],
            consented=funnel_totals["consented"],
            enrolled=funnel_totals["enrolled"],
        )
        qblock = queries_by_site.get(site.id, QueryBlock())
        sblock = safety_by_site.get(site.id, SafetyBlock())
        op = OperationalBlock(
            overdue_visits=overdue_by_site.get(site.id, 0),
            low_ip_lots=[],  # IP is deployment-scoped; rolled into deployment_low_lots
            last_sdv_at=last_sdv_by_site.get(site.id),
        )
        site_card = SiteCard(
            site_id=site.id,
            site_name=site.name,
            site_code=site.code,
            n_subjects=len(subjects_by_site.get(site.id, [])),
            enrolment=enrol,
            queries=qblock,
            safety=sblock,
            operational=op,
        )
        site_cards.append(site_card)
        # Accumulate into totals.
        totals.n_subjects += site_card.n_subjects
        totals.enrolment.screened += enrol.screened
        totals.enrolment.eligible += enrol.eligible
        totals.enrolment.consented += enrol.consented
        totals.enrolment.enrolled += enrol.enrolled
        totals.queries.open += qblock.open
        totals.queries.answered += qblock.answered
        totals.queries.closed += qblock.closed
        totals.safety.open_aes += sblock.open_aes
        totals.safety.open_serious_aes += sblock.open_serious_aes
        totals.safety.open_deviations += sblock.open_deviations
        totals.safety.open_capas += sblock.open_capas
        totals.operational.overdue_visits += op.overdue_visits

    # Subjects unassigned to any site (shouldn't happen in production —
    # subject.site_id is NOT NULL — but covers data drift).
    unassigned_subjects = subjects_by_site.get(None, [])
    if unassigned_subjects or (None in queries_by_site or None in safety_by_site):
        unassigned_card = SiteCard(
            site_id=None,
            site_name="(unassigned)",
            site_code=None,
            n_subjects=len(unassigned_subjects),
            queries=queries_by_site.get(None, QueryBlock()),
            safety=safety_by_site.get(None, SafetyBlock()),
        )
        site_cards.append(unassigned_card)

    # Roll the deployment-scoped IP low-lots into the totals card.
    totals.operational.low_ip_lots = deployment_low_lots

    return DeploymentCard(
        deployment_id=deployment_id,
        deployment_name=deployment.name,
        status=deployment.status,
        n_sites=len(sites),
        n_subjects=len(subjects),
        site_totals=totals,
        sites=site_cards,
    )


# ── Cross-deployment org rollup ──────────────────────────────────────────


async def org_rollup(
    session: AsyncSession,
    *,
    low_ip_threshold: int = DEFAULT_LOW_IP_THRESHOLD,
    now: datetime | None = None,
) -> dict[str, object]:
    """Compute the org-wide rollup: one DeploymentCard per deployment +
    aggregate totals.

    Caller is responsible for gating on portfolio.read_org. The
    function itself does not enforce permission — pure aggregation.
    """
    deployments = list(
        (
            await session.scalars(
                select(StudyDeployment).order_by(StudyDeployment.created_at.desc())
            )
        ).all()
    )
    cards: list[DeploymentCard] = []
    for dep in deployments:
        try:
            card = await site_rollup(
                session,
                deployment_id=dep.id,
                low_ip_threshold=low_ip_threshold,
                now=now,
            )
            cards.append(card)
        except Exception:
            logger.exception("multisite: site_rollup failed for %s", dep.id)

    # Compose org totals from the deployment cards' site_totals rows.
    org_totals = SiteCard(site_id=None, site_name="(org total)", site_code=None)
    org_low_lots: list[str] = []
    for card in cards:
        t = card.site_totals
        org_totals.n_subjects += t.n_subjects
        org_totals.enrolment.screened += t.enrolment.screened
        org_totals.enrolment.eligible += t.enrolment.eligible
        org_totals.enrolment.consented += t.enrolment.consented
        org_totals.enrolment.enrolled += t.enrolment.enrolled
        org_totals.queries.open += t.queries.open
        org_totals.queries.answered += t.queries.answered
        org_totals.queries.closed += t.queries.closed
        org_totals.safety.open_aes += t.safety.open_aes
        org_totals.safety.open_serious_aes += t.safety.open_serious_aes
        org_totals.safety.open_deviations += t.safety.open_deviations
        org_totals.safety.open_capas += t.safety.open_capas
        org_totals.operational.overdue_visits += t.operational.overdue_visits
        org_low_lots.extend(t.operational.low_ip_lots)
    org_totals.operational.low_ip_lots = org_low_lots

    return {
        "n_deployments": len(cards),
        "org_totals": org_totals.as_dict(),
        "deployments": [c.as_dict() for c in cards],
    }


__all__ = [
    "DEFAULT_LOW_IP_THRESHOLD",
    "DeploymentCard",
    "EnrolmentBlock",
    "OperationalBlock",
    "QueryBlock",
    "SafetyBlock",
    "SiteCard",
    "org_rollup",
    "site_rollup",
]
