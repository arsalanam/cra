"""ipd domain — schema invariants for the IPD workflow."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.ipd import (
    IpdBundleTurn,
    IpdDocument,
    IpdIntake,
    IpdMainResults,
    IpdPerTrialEffect,
    IpdPooledEffect,
    IpdSubgroupLevel,
    IpdSubgroupResults,
    IpdTrialEntry,
)


def _trial_entry(trial_id: str = "RCT-1") -> IpdTrialEntry:
    return IpdTrialEntry(
        trial_id=trial_id,
        n_subjects=200,
        treatment_column="trt",
        treatment_active_value="1",
        outcome_column="y",
        rows_csv="subject_id,trt,y\n1,1,1.5\n2,0,1.2\n",
    )


def test_intake_round_trips() -> None:
    intake = IpdIntake(
        research_question="IPD MA of statins on LDL across 6 RCTs",
        primary_endpoint="LDL change at 12 weeks",
        effect_measure="MD",
    )
    rebuilt = IpdIntake.model_validate_json(intake.model_dump_json())
    assert rebuilt == intake


def test_bundle_requires_at_least_two_trials() -> None:
    with pytest.raises(ValidationError):
        IpdBundleTurn(trials=[_trial_entry()])


def test_bundle_accepts_two_trials() -> None:
    b = IpdBundleTurn(trials=[_trial_entry("A"), _trial_entry("B")])
    assert len(b.trials) == 2


def test_trial_entry_requires_non_empty_csv() -> None:
    with pytest.raises(ValidationError):
        IpdTrialEntry(
            trial_id="X",
            n_subjects=10,
            treatment_column="trt",
            treatment_active_value="1",
            outcome_column="y",
            rows_csv="abc",  # too short
        )


def test_pooled_effect_requires_at_least_two_trials() -> None:
    with pytest.raises(ValidationError):
        IpdPooledEffect(
            effect=0.5,
            ci_lower=0.3,
            ci_upper=0.8,
            n_trials=1,
            n_subjects=200,
            method="MixedLM",
        )


def test_pooled_effect_i_squared_clamped() -> None:
    with pytest.raises(ValidationError):
        IpdPooledEffect(
            effect=0.5,
            ci_lower=0.3,
            ci_upper=0.8,
            n_trials=4,
            n_subjects=2000,
            i_squared=120.0,  # invalid
            method="MixedLM",
        )


def test_pooled_effect_tau_squared_non_negative() -> None:
    with pytest.raises(ValidationError):
        IpdPooledEffect(
            effect=0.5,
            ci_lower=0.3,
            ci_upper=0.8,
            n_trials=4,
            n_subjects=2000,
            tau_squared=-0.1,
            method="MixedLM",
        )


def _pooled() -> IpdPooledEffect:
    return IpdPooledEffect(
        effect=0.72,
        ci_lower=0.55,
        ci_upper=0.94,
        p_value=0.014,
        n_trials=6,
        n_subjects=4250,
        i_squared=28.0,
        tau_squared=0.05,
        method="MixedLM REML",
    )


def _main_results() -> IpdMainResults:
    return IpdMainResults(
        effect_measure="OR",
        one_stage=_pooled(),
        two_stage=IpdPooledEffect(
            effect=0.75,
            ci_lower=0.56,
            ci_upper=1.00,
            n_trials=6,
            n_subjects=4250,
            i_squared=24.0,
            tau_squared=0.04,
            method="DerSimonian-Laird",
        ),
        per_trial=[
            IpdPerTrialEffect(
                trial_id="A", n_subjects=500, effect=0.7,
                ci_lower=0.5, ci_upper=0.95, se=0.15,
            ),
            IpdPerTrialEffect(
                trial_id="B", n_subjects=700, effect=0.8,
                ci_lower=0.6, ci_upper=1.05, se=0.13,
            ),
        ],
        discrepancy_note="One-stage and two-stage agree closely.",
    )


def test_main_results_requires_at_least_two_per_trial_rows() -> None:
    with pytest.raises(ValidationError):
        IpdMainResults(
            effect_measure="OR",
            one_stage=_pooled(),
            two_stage=_pooled(),
            per_trial=[
                IpdPerTrialEffect(
                    trial_id="A", n_subjects=500, effect=0.7,
                    ci_lower=0.5, ci_upper=0.95, se=0.15,
                ),
            ],
            discrepancy_note="x",
        )


def test_main_results_round_trip() -> None:
    mr = _main_results()
    rebuilt = IpdMainResults.model_validate_json(mr.model_dump_json())
    assert rebuilt.one_stage.effect == 0.72
    assert len(rebuilt.per_trial) == 2


def test_subgroup_requires_at_least_two_levels() -> None:
    with pytest.raises(ValidationError):
        IpdSubgroupResults(
            subgroup_variable="sex",
            levels=[
                IpdSubgroupLevel(
                    level_label="F", n_trials=4, n_subjects=2000, effect=0.7,
                    ci_lower=0.5, ci_upper=0.95,
                ),
            ],
        )


def test_subgroup_interaction_p_clamped() -> None:
    with pytest.raises(ValidationError):
        IpdSubgroupResults(
            subgroup_variable="sex",
            levels=[
                IpdSubgroupLevel(level_label="F", n_trials=4, n_subjects=2000),
                IpdSubgroupLevel(level_label="M", n_trials=4, n_subjects=2000),
            ],
            interaction_p_value=1.2,  # invalid
        )


def test_subgroup_level_with_skip_reason_round_trips() -> None:
    sg = IpdSubgroupResults(
        subgroup_variable="region",
        levels=[
            IpdSubgroupLevel(
                level_label="EU", n_trials=3, n_subjects=1500, effect=0.7,
                ci_lower=0.5, ci_upper=0.95,
            ),
            IpdSubgroupLevel(
                level_label="US", n_trials=1, n_subjects=200,
                skip_reason="too few events",
            ),
        ],
        interaction_p_value=0.62,
    )
    rebuilt = IpdSubgroupResults.model_validate_json(sg.model_dump_json())
    assert rebuilt.levels[1].skip_reason == "too few events"


def test_document_round_trips() -> None:
    doc = IpdDocument(
        intake=IpdIntake(
            research_question="x",
            primary_endpoint="y",
            effect_measure="OR",
        ),
        main_results=_main_results(),
        studies_included=["A", "B"],
        interpretation="Drug reduces odds by ~28%.",
        direction="favours_intervention",
    )
    rebuilt = IpdDocument.model_validate_json(doc.model_dump_json())
    assert rebuilt.intake.effect_measure == "OR"
    assert rebuilt.direction == "favours_intervention"
