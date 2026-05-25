"""Unit tests for the sr_protocol domain schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.meta_analysis import PicoTable
from research_assistant.domain.sr_protocol import (
    Citation,
    EligibilityCriteria,
    ProsperoFieldMap,
    ProtocolDocument,
    ProtocolMethods,
    RobToolChoice,
    SynthesisPlan,
)


def _sample_pico() -> PicoTable:
    return PicoTable(
        population="Adults ≥18 years with type 2 diabetes",
        intervention="SGLT2 inhibitor (any agent, ≥12 weeks)",
        comparison="Placebo or active comparator",
        outcomes=[
            "Heart-failure hospitalization",
            "All-cause mortality",
            "Major adverse cardiovascular events",
        ],
        inclusion_criteria=["Randomized controlled trial", "Adults ≥18", "≥12 weeks follow-up"],
        exclusion_criteria=["Case reports", "Non-English without translation"],
        study_types=["Randomized Controlled Trial"],
        age_range="≥18 years",
        notes=None,
    )


def _sample_methods() -> ProtocolMethods:
    return ProtocolMethods(
        title=(
            "Sodium-glucose co-transporter-2 inhibitors for prevention of "
            "heart-failure hospitalization in adults with type 2 diabetes: "
            "a systematic review and meta-analysis of randomised trials"
        ),
        review_type="intervention",
        pico=_sample_pico(),
        eligibility=EligibilityCriteria(
            inclusion=["Adults ≥18 with T2DM", "Active SGLT2i vs placebo/comparator"],
            exclusion=["Animal studies", "Conference abstracts without full data"],
            study_designs=["Randomized Controlled Trial"],
            language=["English"],
            date_range="2010–present",
            age_range="≥18 years",
            setting=None,
        ),
        information_sources=["PubMed", "Embase", "Cochrane CENTRAL", "ClinicalTrials.gov"],
        rob_tool=RobToolChoice(
            tool="RoB 2.0",
            rationale="Included designs are RCTs only.",
        ),
        effect_measures_plan={
            "Heart-failure hospitalization": "RR",
            "All-cause mortality": "RR",
            "Major adverse cardiovascular events": "RR",
        },
        synthesis_plan=SynthesisPlan(
            primary_method="random_effects_meta",
            heterogeneity_assessment=["I²", "Tau²", "Cochran's Q"],
            planned_subgroups=["Baseline HbA1c band", "Prior HF status"],
            planned_sensitivity_analyses=["Exclude high-RoB studies"],
            publication_bias_methods=["Funnel plot", "Egger's test"],
        ),
        use_grade=True,
        notes=None,
    )


def test_protocol_methods_round_trip() -> None:
    methods = _sample_methods()
    dumped = methods.model_dump_json()
    assert '"kind":"protocol_methods"' in dumped
    revived = ProtocolMethods.model_validate_json(dumped)
    assert revived.title == methods.title
    assert revived.pico.population == methods.pico.population
    assert revived.rob_tool.tool == "RoB 2.0"
    assert revived.effect_measures_plan["All-cause mortality"] == "RR"
    assert revived.synthesis_plan.primary_method == "random_effects_meta"


def test_protocol_document_round_trip() -> None:
    doc = ProtocolDocument(
        methods=_sample_methods(),
        background=(
            "Type 2 diabetes affects approximately 1 in 10 adults globally [1]. "
            "Heart failure is a common and serious complication [2]."
        ),
        references=[
            Citation(
                text="IDF Diabetes Atlas, 10th edition. 2021.",
                url="https://diabetesatlas.org/",
                origin="web_search",
            ),
            Citation(
                text="Smith J, et al. HF in T2DM. JACC 2022;80:1234.",
                pmid="35987654",
                doi="10.1016/j.jacc.2022.10.001",
                origin="search_papers",
            ),
        ],
        full_markdown=(
            "# Title\n\n## Background\nText with [1] and [2].\n\n## References\n1. ...\n2. ...\n"
        ),
        prospero_field_map=[
            ProsperoFieldMap(field="Review title", content=_sample_methods().title),
            ProsperoFieldMap(
                field="Named contact email",
                content="[USER INPUT NEEDED: corresponding author's institutional email]",
            ),
        ],
        is_final=False,
        notes=None,
    )
    dumped = doc.model_dump_json()
    revived = ProtocolDocument.model_validate_json(dumped)
    assert revived.kind == "protocol_document"
    assert len(revived.references) == 2
    assert revived.references[0].origin == "web_search"
    assert revived.references[1].pmid == "35987654"
    assert revived.is_final is False
    placeholder_field = next(
        f for f in revived.prospero_field_map if f.field == "Named contact email"
    )
    assert placeholder_field.content.startswith("[USER INPUT NEEDED:")


def test_citation_origin_is_constrained() -> None:
    with pytest.raises(ValidationError):
        Citation.model_validate({"text": "x", "origin": "training_data"})


def test_rob_tool_constrained() -> None:
    with pytest.raises(ValidationError):
        RobToolChoice.model_validate({"tool": "GRADE", "rationale": "wrong tool category"})


def test_effect_measure_validates_against_meta_analysis_literal() -> None:
    """effect_measures_plan values must be one of OR/RR/MD/SMD."""
    with pytest.raises(ValidationError):
        ProtocolMethods.model_validate(
            {
                **_sample_methods().model_dump(),
                "effect_measures_plan": {"Mortality": "HR"},  # HR not in EffectMeasure
            }
        )


def test_synthesis_plan_defaults_apply() -> None:
    plan = SynthesisPlan(primary_method="random_effects_meta")
    assert "I²" in plan.heterogeneity_assessment
    assert "Funnel plot" in plan.publication_bias_methods
    assert plan.planned_subgroups == []
