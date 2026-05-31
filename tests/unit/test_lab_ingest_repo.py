"""Lab-data feeds repository (P2 #6) — ingest + dedupe + subject-code
linkage + SDTM LB cascade + RBAC matrix.
"""

from __future__ import annotations

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

_HL7V2 = (
    b"MSH|^~\\&|LAB|HOSP|EDC|TRIAL|20260531120000||ORU^R01|MSG1|P|2.5\r"
    b"PID|||S-001\r"
    b"OBR|1||ACC123|CBC^Complete Blood Count^L|||20260531110000\r"
    b"OBX|1|NM|HGB^Hemoglobin^L||14.2|g/dL|13.0-17.0|N|||F\r"
    b"OBX|2|NM|GLUC^Glucose^L||102|mg/dL|70-100|H|||F\r"
)


async def _seed_deployment(
    session: AsyncSession,
) -> tuple[StudyDeployment, Site, Subject]:
    dep = StudyDeployment(research_study_id="rs", name="Trial")
    session.add(dep)
    await session.flush()
    site = Site(deployment_id=dep.id, name="Site A")
    session.add(site)
    await session.flush()
    subj = Subject(
        deployment_id=dep.id,
        site_id=site.id,
        subject_code="S-001",
    )
    session.add(subj)
    await session.flush()
    return dep, site, subj


# ── Ingest happy path ──────────────────────────────────────────────────


async def test_ingest_creates_batch_and_results(
    clinical_session: AsyncSession,
) -> None:
    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    batch, was_new, warnings = await repo.ingest_lab_batch(
        dep.id,
        source_format="hl7v2",
        raw_bytes=_HL7V2,
        filename="cbc.hl7",
    )
    assert was_new is True
    assert batch.row_count == 2
    assert warnings == []
    rows = await repo.list_lab_results(batch_id=batch.id)
    assert len(rows) == 2
    # subject_id resolved from `PID|||S-001`.
    assert all(r.subject_id == subj.id for r in rows)
    assert all(r.subject_code_hint == "S-001" for r in rows)


async def test_ingest_dedupes_identical_payload_by_sha256(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    first, was_new1, _ = await repo.ingest_lab_batch(
        dep.id, source_format="hl7v2", raw_bytes=_HL7V2
    )
    second, was_new2, _ = await repo.ingest_lab_batch(
        dep.id, source_format="hl7v2", raw_bytes=_HL7V2
    )
    assert was_new1 is True
    assert was_new2 is False
    assert first.id == second.id
    # Still only 2 result rows in total (not 4).
    rows = await repo.list_lab_results(deployment_id=dep.id)
    assert len(rows) == 2


async def test_ingest_rejects_unknown_format(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="source_format"):
        await repo.ingest_lab_batch(dep.id, source_format="x12", raw_bytes=_HL7V2)


async def test_ingest_rejects_empty_payload(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="Empty"):
        await repo.ingest_lab_batch(dep.id, source_format="hl7v2", raw_bytes=b"")


async def test_ingest_rejects_unknown_deployment(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="not found"):
        await repo.ingest_lab_batch("ghost", source_format="hl7v2", raw_bytes=_HL7V2)


# ── Subject-code linkage ───────────────────────────────────────────────


async def test_ingest_leaves_subject_id_null_when_no_matching_subject(
    clinical_session: AsyncSession,
) -> None:
    dep = StudyDeployment(research_study_id="rs", name="Trial")
    clinical_session.add(dep)
    await clinical_session.flush()
    repo = ClinicalRepository(clinical_session)
    batch, _, _ = await repo.ingest_lab_batch(dep.id, source_format="hl7v2", raw_bytes=_HL7V2)
    rows = await repo.list_lab_results(batch_id=batch.id)
    assert all(r.subject_id is None for r in rows)
    # Hint preserved so a later backfill can re-link.
    assert all(r.subject_code_hint == "S-001" for r in rows)


async def test_backfill_links_subjects_added_after_ingest(
    clinical_session: AsyncSession,
) -> None:
    dep = StudyDeployment(research_study_id="rs", name="Trial")
    clinical_session.add(dep)
    await clinical_session.flush()
    repo = ClinicalRepository(clinical_session)
    batch, _, _ = await repo.ingest_lab_batch(dep.id, source_format="hl7v2", raw_bytes=_HL7V2)
    # Now enroll the subject the labs were about.
    site = Site(deployment_id=dep.id, name="Site A")
    clinical_session.add(site)
    await clinical_session.flush()
    subj = Subject(deployment_id=dep.id, site_id=site.id, subject_code="S-001")
    clinical_session.add(subj)
    await clinical_session.flush()
    updated = await repo.backfill_lab_subject_link(dep.id)
    assert updated == 2
    rows = await repo.list_lab_results(batch_id=batch.id)
    assert all(r.subject_id == subj.id for r in rows)


# ── SDTM LB cascade ────────────────────────────────────────────────────


async def test_sdtm_lb_cascade_emits_one_row_per_parsed_lab(
    clinical_session: AsyncSession,
) -> None:
    """`derive_lb_from_lab_results` produces SDTM rows from parsed
    labs that link to a Subject; unlinked labs are skipped."""
    from research_assistant.cdisc.sdtm_mapper import BuiltinPythonMapper

    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    batch, _, _ = await repo.ingest_lab_batch(dep.id, source_format="hl7v2", raw_bytes=_HL7V2)
    rows = await repo.list_lab_results(batch_id=batch.id)
    mapper = BuiltinPythonMapper()
    lb_rows = mapper.derive_lb_from_lab_results(
        deployment_id=dep.id,
        study_id="rs",
        subjects_by_id={subj.id: subj},
        lab_results=rows,
    )
    assert len(lb_rows) == 2
    hgb = next(r for r in lb_rows if r.LBTESTCD in ("HGB", "Hemoglobin"))
    # USUBJID = study_id-subject_code.
    assert hgb.USUBJID == "rs-S-001"
    # LBSEQ increments per-subject from 1.
    seqs = sorted(r.LBSEQ for r in lb_rows)
    assert seqs == [1, 2]


async def test_sdtm_lb_cascade_skips_unlinked_rows(
    clinical_session: AsyncSession,
) -> None:
    """Lab results without a linked Subject are dropped from the
    cascade (a future backfill + re-derive picks them up)."""
    from research_assistant.cdisc.sdtm_mapper import BuiltinPythonMapper

    dep = StudyDeployment(research_study_id="rs", name="Trial")
    clinical_session.add(dep)
    await clinical_session.flush()
    repo = ClinicalRepository(clinical_session)
    _, _, _ = await repo.ingest_lab_batch(dep.id, source_format="hl7v2", raw_bytes=_HL7V2)
    rows = await repo.list_lab_results(deployment_id=dep.id)
    mapper = BuiltinPythonMapper()
    lb_rows = mapper.derive_lb_from_lab_results(
        deployment_id=dep.id,
        study_id="rs",
        subjects_by_id={},  # no subjects registered
        lab_results=rows,
    )
    assert lb_rows == []


async def test_sdtm_lb_cascade_honours_starting_seq(
    clinical_session: AsyncSession,
) -> None:
    """The pipeline runs derive_lb (form-based) first, then derive_lb_
    from_lab_results — the cascade must continue numbering from the
    last form-based row so LBSEQ stays unique per (deployment,
    USUBJID)."""
    from research_assistant.cdisc.sdtm_mapper import BuiltinPythonMapper

    dep, _, subj = await _seed_deployment(clinical_session)
    repo = ClinicalRepository(clinical_session)
    batch, _, _ = await repo.ingest_lab_batch(dep.id, source_format="hl7v2", raw_bytes=_HL7V2)
    rows = await repo.list_lab_results(batch_id=batch.id)
    mapper = BuiltinPythonMapper()
    lb_rows = mapper.derive_lb_from_lab_results(
        deployment_id=dep.id,
        study_id="rs",
        subjects_by_id={subj.id: subj},
        lab_results=rows,
        starting_seq_by_subject={subj.id: 5},
    )
    seqs = sorted(r.LBSEQ for r in lb_rows)
    assert seqs == [6, 7]


# ── RBAC matrix ────────────────────────────────────────────────────────


def test_lab_upload_coord_and_dm_only() -> None:
    """Upload is point-of-care (coord) + central depot (DM). Other
    roles do NOT carry it by default."""
    assert Permission.LAB_UPLOAD in ROLE_PERMISSIONS[Role.COORDINATOR]
    assert Permission.LAB_UPLOAD in ROLE_PERMISSIONS[Role.DATA_MANAGER]
    for role in (
        Role.PRINCIPAL_INVESTIGATOR,
        Role.MONITOR,
        Role.AUDITOR,
        Role.STUDY_DESIGNER,
        Role.STUDENT,
        Role.RESEARCHER,
    ):
        assert Permission.LAB_UPLOAD not in ROLE_PERMISSIONS[role], (
            f"{role.value} should NOT carry lab.upload by default"
        )


def test_lab_read_for_every_clinical_role() -> None:
    """Read access aligns with study.read: every clinical-tier role
    plus auditor. Researcher / student aren't trial-side and don't
    carry it."""
    for role in (
        Role.STUDY_DESIGNER,
        Role.PRINCIPAL_INVESTIGATOR,
        Role.COORDINATOR,
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.AUDITOR,
    ):
        assert Permission.LAB_READ in ROLE_PERMISSIONS[role], f"{role.value} should carry lab.read"
    for role in (Role.RESEARCHER, Role.STUDENT):
        assert Permission.LAB_READ not in ROLE_PERMISSIONS[role]
