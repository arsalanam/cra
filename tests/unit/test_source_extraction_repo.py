"""Source-document extraction repository (P1 #5).

CSV ingest + content-hash dedupe + per-form versioned mappings +
dry-run vs apply behaviour + per-cell ExtractionFill provenance.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.clinical.models import (
    DeployedForm,
    ExtractionFill,
    FormInstance,
    ItemData,
    Site,
    StudyDeployment,
    Subject,
)
from research_assistant.persistence.clinical.repository import (
    ClinicalError,
    ClinicalRepository,
)


async def _seed(
    session: AsyncSession,
    *,
    n_subjects: int = 2,
) -> tuple[StudyDeployment, list[Subject], DeployedForm]:
    dep = StudyDeployment(research_study_id="rs-1", name="Trial")
    session.add(dep)
    await session.flush()
    site = Site(deployment_id=dep.id, name="Site A")
    session.add(site)
    await session.flush()
    subjects = []
    for i in range(n_subjects):
        subj = Subject(
            deployment_id=dep.id,
            site_id=site.id,
            subject_code=f"S-{i + 1:03d}",
        )
        session.add(subj)
        subjects.append(subj)
    await session.flush()
    form = DeployedForm(
        deployment_id=dep.id,
        form_def_id="fd-1",
        form_name="vitals",
        version=1,
        title="Vitals",
        definition_json="{}",
    )
    session.add(form)
    await session.flush()
    return dep, subjects, form


# ── CSV ingest + dedupe ────────────────────────────────────────────────


async def test_ingest_parses_csv_into_source_rows(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    csv = (
        b"subject_id,sbp,dbp\n"
        b"S-001,118,76\n"
        b"S-002,122,80\n"
    )
    doc, added = await repo.ingest_source_document(
        dep.id,
        filename="labs.csv",
        raw_bytes=csv,
        subject_code_field="subject_id",
    )
    assert doc.row_count == 2
    assert added == 2
    headers = json.loads(doc.headers_json)
    assert headers == ["subject_id", "sbp", "dbp"]
    rows = await repo.list_source_rows(doc.id)
    assert rows[0].subject_code_hint == "S-001"
    assert json.loads(rows[0].payload_json)["sbp"] == "118"


async def test_ingest_rejects_csv_without_header(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="header row"):
        await repo.ingest_source_document(
            dep.id, filename="bad.csv", raw_bytes=b""
        )


async def test_ingest_rejects_unknown_subject_field(
    clinical_session: AsyncSession,
) -> None:
    dep, _, _ = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    csv = b"a,b\n1,2\n"
    with pytest.raises(ClinicalError, match="not in CSV headers"):
        await repo.ingest_source_document(
            dep.id, filename="x.csv", raw_bytes=csv, subject_code_field="z"
        )


async def test_ingest_content_hash_dedupe(
    clinical_session: AsyncSession,
) -> None:
    """Re-uploading the same bytes returns the existing row with
    rows_added=0 — no double-ingest."""
    dep, _, _ = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    csv = b"a,b\n1,2\n"
    first, added1 = await repo.ingest_source_document(
        dep.id, filename="a.csv", raw_bytes=csv
    )
    second, added2 = await repo.ingest_source_document(
        dep.id, filename="a.csv", raw_bytes=csv
    )
    assert first.id == second.id
    assert added1 == 1
    assert added2 == 0


# ── ExtractionMapping versioning ────────────────────────────────────────


async def test_create_mapping_bumps_version_and_deactivates_prior(
    clinical_session: AsyncSession,
) -> None:
    dep, _, form = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    m1 = await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={"sbp": "sbp_item"},
    )
    m2 = await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={"sbp": "sbp_item", "dbp": "dbp_item"},
    )
    assert m1.version == 1
    assert m2.version == 2
    refreshed_m1 = await clinical_session.get(type(m1), m1.id)
    assert refreshed_m1 is not None and refreshed_m1.is_active is False
    assert m2.is_active is True


async def test_create_mapping_rejects_cross_deployment_form(
    clinical_session: AsyncSession,
) -> None:
    dep1, _, _ = await _seed(clinical_session)
    dep2, _, form2 = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    with pytest.raises(ClinicalError, match="not found in this deployment"):
        await repo.create_extraction_mapping(
            dep1.id,
            deployed_form_id=form2.id,
            subject_code_field="subject_id",
            mapping={},
        )


async def test_get_active_returns_latest_active(
    clinical_session: AsyncSession,
) -> None:
    dep, _, form = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={},
    )
    m2 = await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={"a": "b"},
    )
    active = await repo.get_active_mapping(dep.id, form.id)
    assert active is not None and active.id == m2.id


# ── dry-run vs apply ───────────────────────────────────────────────────


async def test_dry_run_returns_preview_without_writes(
    clinical_session: AsyncSession,
) -> None:
    dep, _, form = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    csv = b"subject_id,sbp\nS-001,118\nS-002,122\n"
    doc, _ = await repo.ingest_source_document(
        dep.id,
        filename="x.csv",
        raw_bytes=csv,
        subject_code_field="subject_id",
    )
    m = await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={"sbp": "sbp_item"},
    )
    preview = await repo.dry_run_extraction(m.id, source_document_id=doc.id)
    assert len(preview) == 2
    assert preview[0]["subject_code"] == "S-001"
    assert preview[0]["proposed_items"][0]["item_id"] == "sbp_item"
    # No ItemData rows written.
    items_rs = await clinical_session.scalars(select(ItemData))
    assert items_rs.first() is None


async def test_apply_to_subjects_writes_item_data_and_fill(
    clinical_session: AsyncSession,
) -> None:
    dep, subjects, form = await _seed(clinical_session)
    repo = ClinicalRepository(clinical_session)
    csv = b"subject_id,sbp,dbp\nS-001,118,76\nS-002,122,80\n"
    doc, _ = await repo.ingest_source_document(
        dep.id,
        filename="x.csv",
        raw_bytes=csv,
        subject_code_field="subject_id",
    )
    m = await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={"sbp": "sbp_item", "dbp": "dbp_item"},
    )
    summary = await repo.apply_extraction_to_subjects(
        m.id, source_document_id=doc.id
    )
    assert summary["subjects_filled"] == 2
    assert summary["items_written"] == 4
    assert summary["source_rows_unmatched"] == 0
    # Verify FormInstance + ItemData exist.
    fis = await repo.list_form_instances(subject_id=subjects[0].id)
    assert len(fis) == 1
    items = list(
        (
            await clinical_session.scalars(
                select(ItemData).where(
                    ItemData.form_instance_id == fis[0].id
                )
            )
        ).all()
    )
    assert {i.item_id for i in items} == {"sbp_item", "dbp_item"}
    # Verify ExtractionFill provenance rows.
    fills = list(
        (
            await clinical_session.scalars(
                select(ExtractionFill).where(
                    ExtractionFill.mapping_id == m.id
                )
            )
        ).all()
    )
    assert len(fills) == 4
    assert all(f.mapping_version == m.version for f in fills)
    assert all(f.target_kind == "item_data" for f in fills)


async def test_apply_records_unmatched_subjects(
    clinical_session: AsyncSession,
) -> None:
    """Source rows pointing at subjects NOT in the deployment count
    as unmatched but don't crash the apply pass."""
    dep, _, form = await _seed(clinical_session, n_subjects=1)
    repo = ClinicalRepository(clinical_session)
    csv = b"subject_id,sbp\nS-001,118\nS-999,200\n"  # S-999 doesn't exist
    doc, _ = await repo.ingest_source_document(
        dep.id,
        filename="x.csv",
        raw_bytes=csv,
        subject_code_field="subject_id",
    )
    m = await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={"sbp": "sbp_item"},
    )
    summary = await repo.apply_extraction_to_subjects(
        m.id, source_document_id=doc.id
    )
    assert summary["subjects_filled"] == 1
    assert summary["items_written"] == 1
    assert summary["source_rows_unmatched"] == 1


async def test_apply_is_idempotent_overwrites_value(
    clinical_session: AsyncSession,
) -> None:
    """Re-applying the same source row to the same subject overwrites
    the ItemData value but DOES append another ExtractionFill row
    (the audit trail of every apply attempt is preserved)."""
    dep, _, form = await _seed(clinical_session, n_subjects=1)
    repo = ClinicalRepository(clinical_session)
    csv = b"subject_id,sbp\nS-001,118\n"
    doc, _ = await repo.ingest_source_document(
        dep.id,
        filename="x.csv",
        raw_bytes=csv,
        subject_code_field="subject_id",
    )
    m = await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={"sbp": "sbp_item"},
    )
    await repo.apply_extraction_to_subjects(m.id, source_document_id=doc.id)
    await repo.apply_extraction_to_subjects(m.id, source_document_id=doc.id)
    items = list(
        (
            await clinical_session.scalars(
                select(ItemData).where(ItemData.item_id == "sbp_item")
            )
        ).all()
    )
    assert len(items) == 1  # not duplicated
    fills = list(
        (
            await clinical_session.scalars(
                select(ExtractionFill).where(
                    ExtractionFill.mapping_id == m.id
                )
            )
        ).all()
    )
    assert len(fills) == 2  # both apply attempts audited


# ── extraction-table mode ──────────────────────────────────────────────


async def test_apply_to_table_returns_flat_rows_and_writes_fills(
    clinical_session: AsyncSession,
) -> None:
    """Table mode produces a flat per-row projection AND audit rows;
    NO ItemData / FormInstance writes."""
    dep, _, form = await _seed(clinical_session, n_subjects=0)
    repo = ClinicalRepository(clinical_session)
    csv = b"subject_id,sbp,dbp\nS-001,118,76\nS-002,122,80\n"
    doc, _ = await repo.ingest_source_document(
        dep.id,
        filename="ipd.csv",
        raw_bytes=csv,
        subject_code_field="subject_id",
    )
    m = await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={"sbp": "sbp_item", "dbp": "dbp_item"},
    )
    table = await repo.apply_extraction_to_table(
        m.id, source_document_id=doc.id
    )
    assert len(table) == 2
    assert table[0]["subject_code"] == "S-001"
    assert table[0]["sbp_item"] == "118"
    # NO FormInstance / ItemData rows written.
    fis_rs = await clinical_session.scalars(select(FormInstance))
    assert fis_rs.first() is None
    items_rs = await clinical_session.scalars(select(ItemData))
    assert items_rs.first() is None
    # 4 ExtractionFill rows with target_kind='extraction_cell'.
    fills = list(
        (
            await clinical_session.scalars(
                select(ExtractionFill).where(
                    ExtractionFill.target_kind == "extraction_cell"
                )
            )
        ).all()
    )
    assert len(fills) == 4


# ── audit / fills queries ──────────────────────────────────────────────


async def test_list_fills_filtered_by_target(
    clinical_session: AsyncSession,
) -> None:
    dep, _, form = await _seed(clinical_session, n_subjects=1)
    repo = ClinicalRepository(clinical_session)
    csv = b"subject_id,sbp\nS-001,118\n"
    doc, _ = await repo.ingest_source_document(
        dep.id,
        filename="x.csv",
        raw_bytes=csv,
        subject_code_field="subject_id",
    )
    m = await repo.create_extraction_mapping(
        dep.id,
        deployed_form_id=form.id,
        subject_code_field="subject_id",
        mapping={"sbp": "sbp_item"},
    )
    await repo.apply_extraction_to_subjects(m.id, source_document_id=doc.id)
    fills = await repo.list_extraction_fills(deployment_id=dep.id)
    assert len(fills) == 1
    target_id = fills[0].target_id
    by_target = await repo.list_extraction_fills(target_id=target_id)
    assert len(by_target) == 1 and by_target[0].id == fills[0].id
