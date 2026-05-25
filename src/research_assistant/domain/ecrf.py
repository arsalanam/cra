"""eCRF form-definition schema (E0).

The validated, JSON-native model of an electronic Case Report Form — the
authoring artefact that gets versioned and published, then later rendered by
the Data Collector and used to capture subject data. This is metadata only
(no PHI); it lives in the research-app database.

Design decisions (see `ecrf-design.md`):
  - D1: JSON-native internal model (this module), with CDISC ODM-XML export at
    the boundary (`ecrf.odm_export`) and CDASH-aligned item naming.
  - The `FormDefinition` is the unit that is versioned + published; a study is
    a container that also carries the visit schedule.

Edit-check rules are *carried* here but NOT executed in E0 — the evaluation
engine lands in E2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Item value types the renderer + (later) capture layer understand.
ItemDataType = Literal[
    "text",
    "integer",
    "decimal",
    "date",
    "datetime",
    "boolean",
    "single_select",
    "multi_select",
    "file",
]

# Status of a published-form lifecycle and a study lifecycle.
FormStatus = Literal["draft", "published", "superseded"]
StudyStatus = Literal["draft", "active", "closed"]

# Select types must reference a CodeList; these are validated below.
_SELECT_TYPES = {"single_select", "multi_select"}


class CodeListItem(BaseModel):
    """One coded value in a controlled vocabulary."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Submission value stored for this choice, e.g. 'M'.")
    label: str = Field(description="Human-readable label shown in the form, e.g. 'Male'.")
    ordinal: int = Field(default=0, description="Display order (ascending).")


class CodeList(BaseModel):
    """A controlled vocabulary referenced by one or more items (ODM CodeList)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Form-unique code-list id, referenced by Item.code_list_ref.")
    name: str
    items: list[CodeListItem] = Field(default_factory=list)


class EditCheck(BaseModel):
    """A validation rule attached to an item.

    `expression` is a declarative rule string (grammar defined in E2); E0
    stores and round-trips it but does not evaluate it. `severity` 'hard'
    blocks save, 'soft' raises a query but allows the value.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    severity: Literal["hard", "soft"] = "hard"
    expression: str = Field(description="Declarative rule, evaluated by the E2 engine.")
    message: str = Field(description="Shown to the data-entry user when the check fails.")


class Item(BaseModel):
    """A single CRF field."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Form-unique item id; base for the ODM ItemDef OID.")
    label: str = Field(description="Question/prompt text shown on the form.")
    data_type: ItemDataType
    cdash_var: str | None = Field(
        default=None, description="CDASH-aligned variable name, e.g. 'AGE', 'SEX'."
    )
    required: bool = False
    units: str | None = None
    code_list_ref: str | None = Field(
        default=None, description="Required for select types; references a CodeList.id."
    )
    edit_checks: list[EditCheck] = Field(default_factory=list)
    help_text: str | None = None


class Section(BaseModel):
    """A grouping of items (ODM ItemGroupDef)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    items: list[Item] = Field(default_factory=list)
    repeating: bool = Field(
        default=False, description="Whether the section repeats (e.g. concomitant meds)."
    )


class FormDefinition(BaseModel):
    """A complete CRF form — the versioned, publishable unit.

    Item ids must be unique across the whole form, and any `code_list_ref`
    must resolve to a defined `CodeList` — enforced here so a malformed
    definition fails loudly at author/publish time, not during capture.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Machine key, unique within a study, e.g. 'demographics'.")
    title: str = Field(description="Human-readable form title, e.g. 'Demographics'.")
    sections: list[Section] = Field(default_factory=list)
    code_lists: list[CodeList] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_integrity(self) -> FormDefinition:
        code_list_ids = {cl.id for cl in self.code_lists}
        seen_items: set[str] = set()
        for section in self.sections:
            for item in section.items:
                if item.id in seen_items:
                    raise ValueError(f"Duplicate item id {item.id!r} in form {self.name!r}")
                seen_items.add(item.id)
                if item.data_type in _SELECT_TYPES and not item.code_list_ref:
                    raise ValueError(f"Item {item.id!r} is a select but has no code_list_ref")
                if item.code_list_ref and item.code_list_ref not in code_list_ids:
                    raise ValueError(
                        f"Item {item.id!r} references unknown code list {item.code_list_ref!r}"
                    )
        return self


class ScheduledEvent(BaseModel):
    """A planned visit/event in the study schedule."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(description="e.g. 'Screening', 'Baseline', 'Week 4'.")
    ordinal: int = 0
    window_days_min: int | None = None
    window_days_max: int | None = None


class FormEventMapping(BaseModel):
    """Which form is collected at which scheduled event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    form_name: str
    required: bool = True


class VisitSchedule(BaseModel):
    """The study's planned events + the form-to-event map (ODM StudyEventDef-lite)."""

    model_config = ConfigDict(extra="forbid")

    events: list[ScheduledEvent] = Field(default_factory=list)
    form_event_map: list[FormEventMapping] = Field(default_factory=list)


class StudyDraft(BaseModel):
    """An AI-generated draft of a study's CRFs for human review (E3).

    Produced by the `ecrf_design` specialist from a protocol. It is always
    reviewed/edited by a designer and saved through the normal draft→publish
    lifecycle — never auto-published. Each `FormDefinition` is validated by its
    own integrity validator, so a malformed draft fails loudly.
    """

    model_config = ConfigDict(extra="forbid")

    forms: list[FormDefinition] = Field(
        default_factory=list, description="One CRF per measurable data-collection need."
    )
    visit_schedule: VisitSchedule | None = Field(
        default=None, description="Planned events + which forms are collected at each."
    )
    notes: str = Field(
        default="",
        description="Assumptions, gaps, or caveats the reviewer should check.",
    )
