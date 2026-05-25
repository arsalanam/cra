"""Unit tests for eCRF form-definition validation + the publish/version lifecycle."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.domain.ecrf import (
    CodeList,
    CodeListItem,
    FormDefinition,
    Item,
    Section,
)
from research_assistant.persistence.ecrf_repository import EcrfError, EcrfRepository


def _form(name: str = "demographics") -> FormDefinition:
    return FormDefinition(
        name=name,
        title="Demographics",
        code_lists=[
            CodeList(
                id="cl_sex",
                name="Sex",
                items=[
                    CodeListItem(code="M", label="Male"),
                    CodeListItem(code="F", label="Female"),
                ],
            )
        ],
        sections=[
            Section(
                id="main",
                title="Main",
                items=[
                    Item(
                        id="age", label="Age", data_type="integer", cdash_var="AGE", required=True
                    ),
                    Item(id="sex", label="Sex", data_type="single_select", code_list_ref="cl_sex"),
                ],
            )
        ],
    )


# ── definition integrity validator ────────────────────────────────────────────


def test_valid_form_passes() -> None:
    assert _form().sections[0].items[0].id == "age"


def test_duplicate_item_id_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate item id"):
        FormDefinition(
            name="f",
            title="F",
            sections=[
                Section(
                    id="s",
                    title="S",
                    items=[
                        Item(id="dup", label="A", data_type="text"),
                        Item(id="dup", label="B", data_type="text"),
                    ],
                )
            ],
        )


def test_select_without_code_list_rejected() -> None:
    with pytest.raises(ValueError, match="no code_list_ref"):
        FormDefinition(
            name="f",
            title="F",
            sections=[
                Section(
                    id="s", title="S", items=[Item(id="x", label="X", data_type="single_select")]
                )
            ],
        )


def test_unknown_code_list_ref_rejected() -> None:
    with pytest.raises(ValueError, match="unknown code list"):
        FormDefinition(
            name="f",
            title="F",
            sections=[
                Section(
                    id="s",
                    title="S",
                    items=[
                        Item(id="x", label="X", data_type="single_select", code_list_ref="nope")
                    ],
                )
            ],
        )


# ── repository lifecycle ──────────────────────────────────────────────────────


async def test_create_publish_and_immutability(db_session: AsyncSession) -> None:
    repo = EcrfRepository(db_session)
    study = await repo.create_study(name="SGLT2 trial")

    form = await repo.create_form(study.id, _form(), created_by="u1")
    assert form.version == 1 and form.status == "draft"

    # Editing a draft is fine.
    edited = _form()
    edited.title = "Demographics (rev)"
    await repo.update_form_draft(form.id, edited)

    # Duplicate form name in the same study is rejected.
    with pytest.raises(EcrfError, match="already exists"):
        await repo.create_form(study.id, _form())

    # Publish → immutable.
    published = await repo.publish_form(form.id)
    assert published.status == "published" and published.published_at is not None
    with pytest.raises(EcrfError, match="not editable"):
        await repo.update_form_draft(form.id, _form())
    with pytest.raises(EcrfError, match="already published"):
        await repo.publish_form(form.id)


async def test_new_version_supersedes_prior(db_session: AsyncSession) -> None:
    repo = EcrfRepository(db_session)
    study = await repo.create_study(name="S")
    v1 = await repo.create_form(study.id, _form())
    await repo.publish_form(v1.id)

    v2 = await repo.new_version(v1.id)
    assert v2.version == 2 and v2.status == "draft"

    await repo.publish_form(v2.id)
    refreshed_v1 = await repo.get_form(v1.id)
    assert refreshed_v1 is not None and refreshed_v1.status == "superseded"

    forms = await repo.list_forms(study.id)
    assert [(f.version, f.status) for f in forms] == [(1, "superseded"), (2, "published")]
