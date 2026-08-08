"""Drug accountability (P2 #3) — IP catalogue + receipts + dispensations +
returns + per-lot reconciliation rollup.

State invariants enforced at the repository layer:
  • dispense > current lot inventory → reject
  • return without prior dispensation → reject
  • quantity_used + quantity_lost > quantity_returned → reject
  • return quantity > original dispensed quantity → reject
  • cross-deployment subject vs IP → reject
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.auth.rbac import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
)
from research_assistant.persistence.clinical.models import (
    Site,
    StudyDeployment,
    Subject,
)
from research_assistant.persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
)


async def _seed_deployment(
    session: AsyncSession,
) -> tuple[StudyDeployment, Site, Subject]:
    dep = StudyDeployment(research_study_id="rs-1", name="Trial")
    session.add(dep)
    await session.flush()
    site = Site(deployment_id=dep.id, name="Site A")
    session.add(site)
    await session.flush()
    baseline = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    subj = Subject(
        deployment_id=dep.id,
        site_id=site.id,
        subject_code="S-001",
        baseline_date=baseline,
    )
    session.add(subj)
    await session.flush()
    return dep, site, subj


# ── Catalogue ─────────────────────────────────────────────────────────────


async def test_register_ip_succeeds(clinical_session: AsyncSession) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    assert ip.drug_name == "DrugX"
    assert ip.units == "tablet"
    assert ip.status == "active"


async def test_register_ip_rejects_duplicate_name_strength(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    with pytest.raises(ClinicalError, match="already registered"):
        await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")


async def test_register_ip_rejects_unknown_deployment(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="not found"):
        await repo.register_investigational_product(
            "does-not-exist", drug_name="DrugX", strength="10 mg"
        )


# ── Receipts ──────────────────────────────────────────────────────────────


async def test_record_receipt_adds_to_inventory(
    clinical_session: AsyncSession,
) -> None:
    dep, site, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    receipt = await repo.record_drug_receipt(
        dep.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        quantity_received=100,
        site_id=site.id,
    )
    assert receipt.quantity_received == 100
    inventory = await repo._lot_inventory(dep.id, ip.id, "LOT-A")
    assert inventory == 100


async def test_record_receipt_rejects_negative_quantity(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    with pytest.raises(ClinicalError, match="positive"):
        await repo.record_drug_receipt(
            dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=-1
        )


async def test_record_receipt_rejects_cross_deployment_ip(
    clinical_session: AsyncSession,
) -> None:
    dep_a, _, _ = await _seed_deployment(clinical_session)
    dep_b = StudyDeployment(research_study_id="rs-2", name="Trial B")
    clinical_session.add(dep_b)
    await clinical_session.flush()
    repo = ClinicalRepository(clinical_session)
    ip_a = await repo.register_investigational_product(
        dep_a.id, drug_name="DrugX", strength="10 mg"
    )
    with pytest.raises(ClinicalError, match="not registered in this deployment"):
        await repo.record_drug_receipt(
            dep_b.id, ip_id=ip_a.id, lot_number="LOT-A", quantity_received=50
        )


async def test_record_receipt_temp_excursion_flag_persists(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    receipt = await repo.record_drug_receipt(
        dep.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        quantity_received=50,
        temp_excursion_flag=True,
        notes="Cold-chain break at JFK 4 hr",
    )
    assert receipt.temp_excursion_flag is True
    assert "JFK" in receipt.notes


# ── Dispensations ─────────────────────────────────────────────────────────


async def test_record_dispensation_succeeds_within_inventory(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    disp = await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="KIT-001",
        quantity_dispensed=30,
    )
    assert disp.quantity_dispensed == 30
    inventory = await repo._lot_inventory(dep.id, ip.id, "LOT-A")
    assert inventory == 70


async def test_dispense_rejects_exceeding_inventory(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=10)
    with pytest.raises(ClinicalError, match="only 10"):
        await repo.record_drug_dispensation(
            dep.id,
            subject_id=subj.id,
            ip_id=ip.id,
            lot_number="LOT-A",
            kit_id="KIT-001",
            quantity_dispensed=50,
        )


async def test_dispense_rejects_cross_deployment_subject(
    clinical_session: AsyncSession,
) -> None:
    _dep_a, _, subj_a = await _seed_deployment(clinical_session)
    dep_b = StudyDeployment(research_study_id="rs-2", name="Trial B")
    clinical_session.add(dep_b)
    await clinical_session.flush()
    repo = ClinicalRepository(clinical_session)
    ip_b = await repo.register_investigational_product(
        dep_b.id, drug_name="DrugX", strength="10 mg"
    )
    await repo.record_drug_receipt(
        dep_b.id, ip_id=ip_b.id, lot_number="LOT-B", quantity_received=100
    )
    with pytest.raises(ClinicalError, match="not found in deployment"):
        await repo.record_drug_dispensation(
            dep_b.id,
            subject_id=subj_a.id,
            ip_id=ip_b.id,
            lot_number="LOT-B",
            kit_id="KIT-001",
            quantity_dispensed=10,
        )


async def test_dispense_rejects_negative_quantity(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    with pytest.raises(ClinicalError, match="positive"):
        await repo.record_drug_dispensation(
            dep.id,
            subject_id=subj.id,
            ip_id=ip.id,
            lot_number="LOT-A",
            kit_id="KIT-001",
            quantity_dispensed=-5,
        )


# ── kit_id pattern enforcement ────────────────────────────────────────────


async def test_register_ip_rejects_invalid_kit_id_pattern(
    clinical_session: AsyncSession,
) -> None:
    """A typo'd pattern must fail at registration, not silently reject every
    dispense later."""
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="not a valid regex"):
        await repo.register_investigational_product(
            dep.id, drug_name="DrugX", strength="10 mg", kit_id_pattern="KIT-[0-9"
        )


async def test_dispense_accepts_kit_id_matching_pattern(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(
        dep.id, drug_name="DrugX", strength="10 mg", kit_id_pattern="KIT-[0-9]{4}"
    )
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    disp = await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="KIT-0001",
        quantity_dispensed=30,
    )
    assert disp.kit_id == "KIT-0001"


async def test_dispense_rejects_kit_id_not_matching_pattern(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(
        dep.id, drug_name="DrugX", strength="10 mg", kit_id_pattern="KIT-[0-9]{4}"
    )
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    with pytest.raises(ClinicalError, match="does not match the required pattern"):
        await repo.record_drug_dispensation(
            dep.id,
            subject_id=subj.id,
            ip_id=ip.id,
            lot_number="LOT-A",
            kit_id="BOTTLE-1",
            quantity_dispensed=30,
        )


async def test_dispense_kit_id_pattern_is_fullmatch_not_prefix(
    clinical_session: AsyncSession,
) -> None:
    """A prefix that matches but carries extra trailing chars must be
    rejected — 'KIT-0001-X' is not a valid 'KIT-[0-9]{4}'."""
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(
        dep.id, drug_name="DrugX", strength="10 mg", kit_id_pattern="KIT-[0-9]{4}"
    )
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    with pytest.raises(ClinicalError, match="does not match the required pattern"):
        await repo.record_drug_dispensation(
            dep.id,
            subject_id=subj.id,
            ip_id=ip.id,
            lot_number="LOT-A",
            kit_id="KIT-0001-X",
            quantity_dispensed=30,
        )


async def test_dispense_without_pattern_accepts_any_kit_id(
    clinical_session: AsyncSession,
) -> None:
    """No declared pattern → the kit_id is free-text (back-compat)."""
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    disp = await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="anything-goes",
        quantity_dispensed=30,
    )
    assert disp.kit_id == "anything-goes"


# ── Returns ──────────────────────────────────────────────────────────────


async def test_record_return_succeeds_within_dispensed(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    disp = await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="KIT-001",
        quantity_dispensed=30,
    )
    ret = await repo.record_drug_return(
        disp.id,
        quantity_returned=10,
        quantity_used=8,
        quantity_lost=1,
    )
    assert ret.quantity_returned == 10
    # Inventory accounting: 100 received − 30 dispensed + (10 − 8 − 1) = 71.
    inventory = await repo._lot_inventory(dep.id, ip.id, "LOT-A")
    assert inventory == 71


async def test_return_rejects_used_plus_lost_exceeds_returned(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    disp = await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="KIT-001",
        quantity_dispensed=30,
    )
    with pytest.raises(ClinicalError, match="cannot exceed quantity_returned"):
        await repo.record_drug_return(
            disp.id, quantity_returned=10, quantity_used=8, quantity_lost=5
        )


async def test_return_rejects_returned_exceeds_dispensed(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    disp = await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="KIT-001",
        quantity_dispensed=10,
    )
    with pytest.raises(ClinicalError, match="exceeds dispensed"):
        await repo.record_drug_return(disp.id, quantity_returned=20)


async def test_return_rejects_invalid_reason(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    disp = await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="KIT-001",
        quantity_dispensed=30,
    )
    with pytest.raises(ClinicalError, match="Invalid return_reason"):
        await repo.record_drug_return(disp.id, quantity_returned=10, return_reason="cat_ate_it")


async def test_return_rejects_unknown_dispensation(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="not found"):
        await repo.record_drug_return("does-not-exist", quantity_returned=10)


# ── Reconciliation rollup ────────────────────────────────────────────────


async def test_reconciliation_aggregates_by_lot(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    # Two lots, one with returns + losses.
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-B", quantity_received=60)
    disp_a = await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="KIT-001",
        quantity_dispensed=30,
    )
    await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-B",
        kit_id="KIT-002",
        quantity_dispensed=20,
    )
    await repo.record_drug_return(disp_a.id, quantity_returned=10, quantity_used=8, quantity_lost=1)

    rollup = await repo.drug_reconciliation(dep.id)
    totals = rollup["totals"]
    assert totals["received"] == 160
    assert totals["dispensed"] == 50
    assert totals["returned"] == 10
    assert totals["used"] == 8
    assert totals["lost"] == 1
    # 160 − 50 + (10 − 8 − 1) = 111
    assert totals["current_inventory"] == 111
    by_lot = rollup["by_lot"]
    assert f"{ip.id}|LOT-A" in by_lot
    assert by_lot[f"{ip.id}|LOT-A"]["current_inventory"] == 71
    assert by_lot[f"{ip.id}|LOT-B"]["current_inventory"] == 40


async def test_reconciliation_empty_deployment_returns_zero_totals(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    rollup = await repo.drug_reconciliation(dep.id)
    assert rollup["deployment_id"] == dep.id
    assert rollup["totals"]["received"] == 0
    assert rollup["totals"]["current_inventory"] == 0
    assert rollup["by_lot"] == {}


# ── Per-subject compliance ────────────────────────────────────────────────


async def test_subject_compliance_ratio_used_over_dispensed(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    disp = await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="KIT-001",
        quantity_dispensed=40,
    )
    # Returned 10, of which 8 used → compliance = 8 / 40 = 0.2.
    await repo.record_drug_return(disp.id, quantity_returned=10, quantity_used=8, quantity_lost=1)

    rollup = await repo.subject_drug_compliance(dep.id)
    bucket = rollup["by_subject"][subj.id]
    assert bucket["subject_code"] == "S-001"
    assert bucket["dispensed"] == 40
    assert bucket["used"] == 8
    assert bucket["compliance"] == 0.2


async def test_subject_compliance_none_when_nothing_dispensed(
    clinical_session: AsyncSession,
) -> None:
    """A subject with no dispensations doesn't appear; the deployment
    rollup is simply empty."""
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    rollup = await repo.subject_drug_compliance(dep.id)
    assert rollup["deployment_id"] == dep.id
    assert rollup["by_subject"] == {}


async def test_subject_compliance_dispensed_not_yet_returned_is_zero(
    clinical_session: AsyncSession,
) -> None:
    """Drug is out but no return logged yet → used=0, compliance=0.0 (the
    documented lower-bound behaviour), NOT None."""
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    await repo.record_drug_dispensation(
        dep.id,
        subject_id=subj.id,
        ip_id=ip.id,
        lot_number="LOT-A",
        kit_id="KIT-001",
        quantity_dispensed=30,
    )
    rollup = await repo.subject_drug_compliance(dep.id)
    bucket = rollup["by_subject"][subj.id]
    assert bucket["dispensed"] == 30
    assert bucket["used"] == 0
    assert bucket["compliance"] == 0.0


async def test_subject_compliance_scoped_to_single_subject(
    clinical_session: AsyncSession,
) -> None:
    dep, site, subj_a = await _seed_deployment(clinical_session)
    # A second subject in the same deployment.
    subj_b = Subject(
        deployment_id=dep.id,
        site_id=site.id,
        subject_code="S-002",
        baseline_date=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
    )
    clinical_session.add(subj_b)
    await clinical_session.flush()
    repo = ClinicalRepository(clinical_session)
    ip = await repo.register_investigational_product(dep.id, drug_name="DrugX", strength="10 mg")
    await repo.record_drug_receipt(dep.id, ip_id=ip.id, lot_number="LOT-A", quantity_received=100)
    for s in (subj_a, subj_b):
        await repo.record_drug_dispensation(
            dep.id,
            subject_id=s.id,
            ip_id=ip.id,
            lot_number="LOT-A",
            kit_id="KIT-001",
            quantity_dispensed=10,
        )
    scoped = await repo.subject_drug_compliance(dep.id, subject_id=subj_a.id)
    assert set(scoped["by_subject"].keys()) == {subj_a.id}


# ── RBAC matrix ──────────────────────────────────────────────────────────


def test_ip_catalogue_designer_and_dm_only() -> None:
    """The IP catalogue is registered at design time; mid-study additions
    are a data-management activity. Other roles do NOT author."""
    assert Permission.IP_CATALOGUE in ROLE_PERMISSIONS[Role.STUDY_DESIGNER]
    assert Permission.IP_CATALOGUE in ROLE_PERMISSIONS[Role.DATA_MANAGER]
    for role in (
        Role.COORDINATOR,
        Role.PRINCIPAL_INVESTIGATOR,
        Role.MONITOR,
        Role.AUDITOR,
        Role.STUDENT,
        Role.RESEARCHER,
    ):
        assert Permission.IP_CATALOGUE not in ROLE_PERMISSIONS[role], (
            f"{role.value} should not author the IP catalogue"
        )


def test_ip_receive_coordinator_and_dm() -> None:
    """Point-of-care receipt is the coordinator at the site; central
    depots are the DM."""
    assert Permission.IP_RECEIVE in ROLE_PERMISSIONS[Role.COORDINATOR]
    assert Permission.IP_RECEIVE in ROLE_PERMISSIONS[Role.DATA_MANAGER]
    for role in (
        Role.PRINCIPAL_INVESTIGATOR,
        Role.MONITOR,
        Role.AUDITOR,
        Role.STUDY_DESIGNER,
        Role.STUDENT,
    ):
        assert Permission.IP_RECEIVE not in ROLE_PERMISSIONS[role]


def test_ip_dispense_coordinator_and_pi() -> None:
    """Dispense is point-of-care (coordinator) + PI co-signature."""
    assert Permission.IP_DISPENSE in ROLE_PERMISSIONS[Role.COORDINATOR]
    assert Permission.IP_DISPENSE in ROLE_PERMISSIONS[Role.PRINCIPAL_INVESTIGATOR]
    for role in (
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.AUDITOR,
        Role.STUDY_DESIGNER,
        Role.STUDENT,
    ):
        assert Permission.IP_DISPENSE not in ROLE_PERMISSIONS[role]


def test_ip_return_coordinator_only() -> None:
    """Return event happens at the site visit — coordinator only.
    The DM doesn't see the subject; the PI signs but doesn't log."""
    assert Permission.IP_RETURN in ROLE_PERMISSIONS[Role.COORDINATOR]
    for role in (
        Role.PRINCIPAL_INVESTIGATOR,
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.AUDITOR,
        Role.STUDY_DESIGNER,
        Role.STUDENT,
    ):
        assert Permission.IP_RETURN not in ROLE_PERMISSIONS[role]


def test_ip_reconcile_dm_monitor_auditor_pi() -> None:
    """Reconciliation rollup is the audit-side surface — DM, monitor,
    auditor, PI read; mutating roles (coordinator, designer) do not."""
    for role in (
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.AUDITOR,
        Role.PRINCIPAL_INVESTIGATOR,
    ):
        assert Permission.IP_RECONCILE in ROLE_PERMISSIONS[role], (
            f"{role.value} should read the IP reconciliation rollup"
        )
    for role in (Role.COORDINATOR, Role.STUDY_DESIGNER, Role.STUDENT):
        assert Permission.IP_RECONCILE not in ROLE_PERMISSIONS[role]
