"""CDISC2 pipeline — _gather_form_instances_by_domain resolves form_to_domain_map."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.cdisc.pipeline import (
    _gather_form_instances_by_domain,
    run_derivation,
)
from research_assistant.cdisc.sdtm_mapper import ItemMappingConfig
from research_assistant.persistence.clinical.repository import (
    ClinicalRepository,
    FormSnapshot,
)

_LB_FORM = FormSnapshot(
    form_def_id="lf",
    form_name="lab_results",
    version=1,
    title="Lab Results",
    definition_json='{"name":"lab_results","title":"Lab Results","sections":[]}',
)
_DM_FORM = FormSnapshot(
    form_def_id="df",
    form_name="demographics",
    version=1,
    title="Demographics",
    definition_json='{"name":"demographics","title":"Demographics","sections":[]}',
)


async def _setup(repo: ClinicalRepository, clinical_session: AsyncSession) -> dict[str, str]:
    dep = await repo.deploy_study(
        research_study_id="rs-1", name="LB trial", forms=[_LB_FORM, _DM_FORM], actor_sub="u"
    )
    forms = await repo.list_deployed_forms(dep.id)
    by_name = {f.form_name: f.id for f in forms}
    site = await repo.add_site(dep.id, name="Site A")
    subj = await repo.add_subject(dep.id, site_id=site.id, subject_code="S-001")
    # Two lab measurements + a demographics form (which should NOT be picked up by LB).
    lab1 = await repo.open_form_instance(
        subj.id, deployed_form_id=by_name["lab_results"], actor_sub="u"
    )
    await repo.submit_item_data(
        lab1.id, {"test": "hgb", "result": "13.5"}, actor_sub="u"
    )
    lab2 = await repo.open_form_instance(
        subj.id, deployed_form_id=by_name["lab_results"], actor_sub="u"
    )
    await repo.submit_item_data(
        lab2.id, {"test": "glucose", "result": "180"}, actor_sub="u"
    )
    demo = await repo.open_form_instance(
        subj.id, deployed_form_id=by_name["demographics"], actor_sub="u"
    )
    await repo.submit_item_data(demo.id, {"age": "55", "sex": "F"}, actor_sub="u")
    return {"dep": dep.id, "subj": subj.id}


async def test_gather_returns_only_matched_form_instances(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    ids = await _setup(repo, clinical_session)
    by_domain = await _gather_form_instances_by_domain(
        clinical_session,
        ids["dep"],
        {"lab_results": "LB"},
    )
    # Only the two lab form-instances picked up; demographics excluded.
    assert len(by_domain["LB"]) == 2
    for _fi, items in by_domain["LB"]:
        assert any(it.item_id == "test" for it in items)


async def test_gather_is_case_insensitive_on_form_name(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    ids = await _setup(repo, clinical_session)
    by_domain = await _gather_form_instances_by_domain(
        clinical_session,
        ids["dep"],
        {"LAB_RESULTS": "LB"},
    )
    assert len(by_domain["LB"]) == 2


async def test_gather_with_empty_mapping_returns_empty(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    ids = await _setup(repo, clinical_session)
    by_domain = await _gather_form_instances_by_domain(
        clinical_session, ids["dep"], {}
    )
    assert by_domain == {}


async def test_run_derivation_persists_lb_rows(
    clinical_session: AsyncSession,
) -> None:
    repo = ClinicalRepository(clinical_session)
    ids = await _setup(repo, clinical_session)
    result = await run_derivation(
        clinical_session,
        deployment_id=ids["dep"],
        triggered_by="u",
        config=ItemMappingConfig(),
    )
    assert result.counts["lb"] == 2
    # Other repeating-form counts should be 0 because no EX/CM/MH forms exist.
    assert result.counts["ex"] == 0
    assert result.counts["cm"] == 0
    assert result.counts["mh"] == 0
