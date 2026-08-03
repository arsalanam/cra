"""trial_stats domain — schema invariants + CSR artefact-id derivation.

The discriminated-union turn shape pins the workflow stages; the
`derived_from` field is the load-bearing anti-hallucination gate so we
verify both that it's required and that the CSR-handoff ids round-trip
in the canonical shape.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.trial_stats import (
    AnalysisPopulation,
    AnalysisPopulationsTurn,
    BinaryResult,
    BinaryResultsTurn,
    ContinuousResult,
    ContinuousResultsTurn,
    SubgroupAnalysis,
    SubgroupResultsTurn,
    SubgroupRow,
    TimeToEventResult,
    TimeToEventResultsTurn,
    TrialStatsDocument,
    TrialStatsIntake,
)


def _intake() -> TrialStatsIntake:
    return TrialStatsIntake(
        study_id="ABC-2026-001",
        study_name="DrugA in T2DM",
        primary_endpoint="Overall survival",
        secondary_endpoints=["PFS", "ORR"],
        arms=["Placebo", "Drug A"],
        input_shape="adam",
    )


def _population(kind: str = "ITT") -> AnalysisPopulation:
    return AnalysisPopulation(
        kind_name=kind,  # type: ignore[arg-type]
        n_total=200,
        n_per_arm={"Placebo": 100, "Drug A": 100},
        derived_from=f"ADSL.{kind}FL=Y",
        rationale="All randomised subjects.",
    )


def _tte(
    hr: float | None = 0.72,
    skip: str | None = None,
) -> TimeToEventResult:
    return TimeToEventResult(
        paramcd="OS",
        param_label="Overall survival",
        population="ITT",
        n_subjects=200,
        n_events=85,
        median_event_time="14.2 months",
        hazard_ratio=hr,
        hr_ci_lower=0.55 if hr else None,
        hr_ci_upper=0.94 if hr else None,
        logrank_p_value=0.014 if hr else None,
        comparison_label="Drug A vs Placebo",
        derived_from="sandbox:km:OS",
        skip_reason=skip,
    )


# ── Intake ───────────────────────────────────────────────────────────────


def test_intake_round_trips() -> None:
    intake = _intake()
    rebuilt = TrialStatsIntake.model_validate_json(intake.model_dump_json())
    assert rebuilt == intake


def test_intake_defaults_input_shape_to_adam() -> None:
    intake = TrialStatsIntake(
        study_id="X",
        study_name="X",
        primary_endpoint="OS",
        arms=["A", "B"],
    )
    assert intake.input_shape == "adam"


# ── Populations ──────────────────────────────────────────────────────────


def test_populations_turn_requires_at_least_one_row() -> None:
    with pytest.raises(ValidationError):
        AnalysisPopulationsTurn(populations=[])


def test_population_carries_required_derived_from_and_rationale() -> None:
    with pytest.raises(ValidationError):
        AnalysisPopulation(  # type: ignore[call-arg]
            kind_name="ITT",
            n_total=100,
            n_per_arm={},
            # missing derived_from + rationale
        )


# ── Time-to-event ────────────────────────────────────────────────────────


def test_tte_requires_ci_bounds_when_hr_supplied() -> None:
    with pytest.raises(ValidationError):
        TimeToEventResult(
            paramcd="OS",
            param_label="Overall survival",
            n_subjects=50,
            n_events=20,
            median_event_time="—",
            hazard_ratio=0.5,
            # missing CI bounds
            derived_from="sandbox:km:OS",
        )


def test_tte_rejects_both_hr_and_skip_reason() -> None:
    with pytest.raises(ValidationError):
        TimeToEventResult(
            paramcd="OS",
            param_label="Overall survival",
            n_subjects=50,
            n_events=20,
            median_event_time="—",
            hazard_ratio=0.5,
            hr_ci_lower=0.3,
            hr_ci_upper=0.8,
            skip_reason="impossible",
            derived_from="sandbox:km:OS",
        )


def test_tte_skip_reason_alone_is_valid() -> None:
    result = _tte(hr=None, skip="Single arm — Cox PH requires ≥2 arms.")
    assert result.hazard_ratio is None
    assert result.skip_reason
    assert "Single arm" in result.skip_reason


def test_tte_rejects_inverted_ci_bounds() -> None:
    with pytest.raises(ValidationError):
        TimeToEventResult(
            paramcd="OS",
            param_label="Overall survival",
            n_subjects=50,
            n_events=20,
            median_event_time="—",
            hazard_ratio=0.5,
            hr_ci_lower=0.9,  # > upper
            hr_ci_upper=0.4,
            derived_from="sandbox:km:OS",
        )


# ── Continuous (MMRM) ────────────────────────────────────────────────────


def test_continuous_requires_ci_when_lsmean_diff_supplied() -> None:
    with pytest.raises(ValidationError):
        ContinuousResult(
            paramcd="CHGFBL",
            param_label="Change from baseline",
            visit="Week 24",
            n_observed=150,
            lsmean_difference=-1.4,
            # missing CI bounds
            derived_from="sandbox:mmrm:CHGFBL",
        )


def test_continuous_allows_skip_reason() -> None:
    cr = ContinuousResult(
        paramcd="CHGFBL",
        param_label="Change from baseline",
        visit="Week 24",
        n_observed=0,
        derived_from="sandbox:mmrm:CHGFBL",
        skip_reason="No observed data after exclusions.",
    )
    assert cr.lsmean_difference is None


def test_continuous_results_turn_can_be_empty() -> None:
    """An MMRM step with no continuous endpoints is valid (e.g. survival-only
    trials) — the results list is optional."""
    turn = ContinuousResultsTurn(results=[])
    assert turn.results == []


# ── Binary ───────────────────────────────────────────────────────────────


def test_binary_rejects_events_exceeding_n() -> None:
    with pytest.raises(ValidationError):
        BinaryResult(
            paramcd="ORR",
            param_label="Overall response rate",
            method="fisher_exact",
            n_treatment=10,
            events_treatment=15,  # impossible
            n_comparator=10,
            events_comparator=3,
            derived_from="sandbox:binary:ORR",
        )


def test_binary_results_turn_round_trips_through_json() -> None:
    turn = BinaryResultsTurn(
        results=[
            BinaryResult(
                paramcd="ORR",
                param_label="Overall response rate",
                method="fisher_exact",
                n_treatment=50,
                events_treatment=25,
                n_comparator=50,
                events_comparator=15,
                risk_difference=0.20,
                rd_ci_lower=0.05,
                rd_ci_upper=0.35,
                p_value=0.018,
                comparison_label="Drug A vs Placebo",
                derived_from="sandbox:binary:ORR",
            )
        ]
    )
    rebuilt = BinaryResultsTurn.model_validate_json(turn.model_dump_json())
    assert rebuilt.results[0].risk_difference == 0.20


# ── Subgroup ─────────────────────────────────────────────────────────────


def test_subgroup_analysis_requires_at_least_two_rows() -> None:
    with pytest.raises(ValidationError):
        SubgroupAnalysis(
            parent_paramcd="OS",
            parent_param_label="Overall survival",
            subgroup_variable="SEX",
            rows=[SubgroupRow(subgroup_label="Female", n=100, n_events=40)],
            derived_from="sandbox:subgroup:OS:by:SEX",
        )


def test_subgroup_results_turn_can_be_empty() -> None:
    turn = SubgroupResultsTurn(analyses=[])
    assert turn.analyses == []


# ── Document + CSR-citable artefact ids ──────────────────────────────────


def test_document_csr_artefact_ids_follow_canonical_shape() -> None:
    doc = TrialStatsDocument(
        intake=_intake(),
        populations=[_population("ITT")],
        time_to_event=[_tte()],
        continuous=[
            ContinuousResult(
                paramcd="CHGFBL",
                param_label="Change from baseline HbA1c",
                visit="Week 24",
                n_observed=180,
                lsmean_difference=-0.8,
                diff_ci_lower=-1.1,
                diff_ci_upper=-0.5,
                p_value=0.0001,
                derived_from="sandbox:mmrm:CHGFBL",
            )
        ],
        binary=[
            BinaryResult(
                paramcd="ORR",
                param_label="Overall response rate",
                method="fisher_exact",
                n_treatment=100,
                events_treatment=42,
                n_comparator=100,
                events_comparator=21,
                risk_difference=0.21,
                rd_ci_lower=0.08,
                rd_ci_upper=0.34,
                p_value=0.001,
                derived_from="sandbox:binary:ORR",
            )
        ],
        subgroup=[
            SubgroupAnalysis(
                parent_paramcd="OS",
                parent_param_label="Overall survival",
                subgroup_variable="SEX",
                rows=[
                    SubgroupRow(
                        subgroup_label="Female",
                        n=100,
                        n_events=40,
                        effect=0.7,
                        ci_lower=0.5,
                        ci_upper=0.95,
                    ),
                    SubgroupRow(
                        subgroup_label="Male",
                        n=100,
                        n_events=45,
                        effect=0.75,
                        ci_lower=0.55,
                        ci_upper=1.0,
                    ),
                ],
                interaction_p_value=0.42,
                derived_from="sandbox:subgroup:OS:by:SEX",
            )
        ],
    )
    ids = doc.csr_artefact_ids
    assert "TrialStats t-km-OS" in ids
    assert "TrialStats mmrm-CHGFBL" in ids
    assert "TrialStats binary-ORR" in ids
    assert "TrialStats subgroup-OS-by-SEX" in ids
    assert len(ids) == 4


def test_document_round_trips_through_json() -> None:
    doc = TrialStatsDocument(
        intake=_intake(),
        populations=[_population()],
        time_to_event=[_tte()],
    )
    rebuilt = TrialStatsDocument.model_validate_json(doc.model_dump_json())
    assert rebuilt.intake.study_id == "ABC-2026-001"
    assert rebuilt.time_to_event[0].hazard_ratio == 0.72


def test_document_requires_at_least_one_population() -> None:
    with pytest.raises(ValidationError):
        TrialStatsDocument(
            intake=_intake(),
            populations=[],
            time_to_event=[_tte()],
        )


def test_time_to_event_turn_round_trips_through_json() -> None:
    turn = TimeToEventResultsTurn(results=[_tte()])
    rebuilt = TimeToEventResultsTurn.model_validate_json(turn.model_dump_json())
    assert rebuilt.results[0].derived_from == "sandbox:km:OS"
