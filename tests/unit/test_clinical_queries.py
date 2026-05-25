"""Edit-check enforcement + query lifecycle at the repository level (E2)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.domain.ecrf import EditCheck, FormDefinition, Item, Section
from research_assistant.persistence.clinical.models import ItemData, QueryResponse
from research_assistant.persistence.clinical.repository import (
    ClinicalRepository,
    FormSnapshot,
    HardCheckError,
)


def _definition_json() -> str:
    fd = FormDefinition(
        name="vitals",
        title="Vitals",
        sections=[
            Section(
                id="s",
                title="S",
                items=[
                    Item(
                        id="age",
                        label="Age",
                        data_type="integer",
                        required=True,
                        edit_checks=[
                            EditCheck(
                                id="age_range",
                                severity="hard",
                                expression="is_blank(age) or (age >= 0 and age < 120)",
                                message="Age must be 0-119",
                            )
                        ],
                    ),
                    Item(
                        id="sbp",
                        label="Systolic BP",
                        data_type="integer",
                        edit_checks=[
                            EditCheck(
                                id="sbp_high",
                                severity="soft",
                                expression="is_blank(sbp) or sbp <= 200",
                                message="Systolic BP unusually high — please confirm",
                            )
                        ],
                    ),
                ],
            )
        ],
    )
    return fd.model_dump_json()


async def _open_instance(repo: ClinicalRepository) -> str:
    dep = await repo.deploy_study(
        research_study_id="rs",
        name="T",
        forms=[
            FormSnapshot(
                form_def_id="f1",
                form_name="vitals",
                version=1,
                title="Vitals",
                definition_json=_definition_json(),
            )
        ],
        actor_sub="u1",
    )
    form_id = (await repo.list_deployed_forms(dep.id))[0].id
    site = await repo.add_site(dep.id, name="A")
    subject = await repo.add_subject(dep.id, site_id=site.id, subject_code="S1")
    fi = await repo.open_form_instance(subject.id, deployed_form_id=form_id, actor_sub="u1")
    return fi.id


async def test_hard_check_blocks_and_saves_nothing(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    fi_id = await _open_instance(repo)

    with pytest.raises(HardCheckError) as exc:
        await repo.submit_item_data(fi_id, {"age": "200", "sbp": "120"}, actor_sub="u1")
    assert any(f.check_id == "age_range" for f in exc.value.failures)

    # Atomic: nothing persisted from the rejected submit.
    n = await clinical_session.scalar(
        select(func.count()).select_from(ItemData).where(ItemData.form_instance_id == fi_id)
    )
    assert n == 0


async def test_required_blank_blocks_completion(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    fi_id = await _open_instance(repo)
    with pytest.raises(HardCheckError) as exc:
        await repo.submit_item_data(fi_id, {"sbp": "120"}, actor_sub="u1", mark_complete=True)
    assert any(f.check_id == "required" for f in exc.value.failures)


async def test_soft_check_opens_and_auto_closes_query(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    fi_id = await _open_instance(repo)

    # Soft violation: value saved, auto-query opened.
    await repo.submit_item_data(fi_id, {"sbp": "250"}, actor_sub="u1")
    queries = await repo.list_queries(fi_id)
    assert len(queries) == 1
    assert (queries[0].query_type, queries[0].status, queries[0].item_id) == ("auto", "open", "sbp")

    # Correcting the value auto-closes the query (no duplicate opened).
    await repo.submit_item_data(fi_id, {"sbp": "180"}, actor_sub="u1", reason="typo")
    queries = await repo.list_queries(fi_id)
    assert len(queries) == 1 and queries[0].status == "closed"


async def test_manual_query_lifecycle(clinical_session: AsyncSession) -> None:
    repo = ClinicalRepository(clinical_session)
    fi_id = await _open_instance(repo)
    await repo.submit_item_data(fi_id, {"age": "45"}, actor_sub="u1")

    q = await repo.create_manual_query(
        fi_id, item_id="age", text="Please verify against source", actor_sub="dm"
    )
    assert q.status == "open" and q.query_type == "manual"

    await repo.respond_query(q.id, text="Verified, correct", author_sub="crc")
    await repo.close_query(q.id, actor_sub="dm")

    refreshed = (await repo.list_queries(fi_id))[0]
    assert refreshed.status == "closed"
    # The response thread is recorded.
    responses = (
        await clinical_session.scalars(select(QueryResponse).where(QueryResponse.query_id == q.id))
    ).all()
    assert responses[0].text == "Verified, correct"
