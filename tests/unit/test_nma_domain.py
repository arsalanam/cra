"""nma domain — schema invariants for the league table + SUCRA roundtrip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.nma import (
    LeagueRow,
    LeagueTable,
    NetworkEdge,
    NetworkGraph,
    NetworkNode,
    NmaArmData,
    NmaDataExtraction,
    NmaIntake,
    NmaPicoTurn,
    NmaResults,
    NmaSearchResults,
    NmaStudyCandidate,
    NmaStudyExtractedData,
    PicoNetwork,
    SucraRow,
)


def _pico() -> PicoNetwork:
    return PicoNetwork(
        population="Adults with AF",
        interventions=["Placebo", "Drug A", "Drug B", "Drug C"],
        outcome="Stroke prevention",
        effect_measure="OR",
    )


def test_intake_round_trips() -> None:
    intake = NmaIntake(research_question="Compare 4 DOACs for AF stroke prevention")
    rebuilt = NmaIntake.model_validate_json(intake.model_dump_json())
    assert rebuilt == intake


def test_pico_requires_three_or_more_interventions() -> None:
    with pytest.raises(ValidationError):
        PicoNetwork(
            population="x",
            interventions=["A", "B"],
            outcome="y",
            effect_measure="OR",
        )


def test_pico_turn_requires_rationale() -> None:
    with pytest.raises(ValidationError):
        NmaPicoTurn(  # type: ignore[call-arg]
            pico=_pico(),
            # missing rationale
        )


def test_study_candidate_requires_two_arms() -> None:
    with pytest.raises(ValidationError):
        NmaStudyCandidate(
            source="pubmed",
            source_id="12345",
            title="One-arm study",
            arms_evaluated=["Drug A"],
        )


def test_study_candidate_accepts_two_or_more_arms() -> None:
    s = NmaStudyCandidate(
        source="pubmed",
        source_id="12345",
        title="Three-arm",
        arms_evaluated=["Drug A", "Placebo", "Drug B"],
    )
    assert len(s.arms_evaluated) == 3


def test_data_extraction_requires_two_studies() -> None:
    with pytest.raises(ValidationError):
        NmaDataExtraction(
            studies=[
                NmaStudyExtractedData(
                    source="pubmed",
                    source_id="1",
                    arms=[
                        NmaArmData(intervention="A", n=10, events=2),
                        NmaArmData(intervention="B", n=10, events=4),
                    ],
                )
            ],
            summary="one study",
        )


def test_search_results_round_trip() -> None:
    sr = NmaSearchResults(
        pubmed_query="(AF) AND (DOAC)",
        studies=[
            NmaStudyCandidate(
                source="pubmed",
                source_id="1",
                title="t1",
                arms_evaluated=["Drug A", "Placebo"],
            ),
            NmaStudyCandidate(
                source="pubmed",
                source_id="2",
                title="t2",
                arms_evaluated=["Drug B", "Placebo"],
            ),
        ],
        total_found=2,
    )
    rebuilt = NmaSearchResults.model_validate_json(sr.model_dump_json())
    assert len(rebuilt.studies) == 2


# ── Results assembly ────────────────────────────────────────────────────


def _league_row(row: str, col: str, effect: float = 0.72) -> LeagueRow:
    return LeagueRow(
        row_intervention=row,
        col_intervention=col,
        effect=effect,
        ci_lower=0.55,
        ci_upper=0.94,
        n_direct_trials=2,
        n_indirect_paths=0,
    )


def _sucra_row(name: str, rank: int) -> SucraRow:
    return SucraRow(intervention=name, sucra=0.5, mean_rank=float(rank), rank=rank)


def test_results_round_trip_through_json() -> None:
    pico = _pico()
    results = NmaResults(
        pico=pico,
        backend="frequentist",
        league_table=LeagueTable(
            effect_measure="OR",
            rows=[
                _league_row("Drug A", "Placebo"),
                _league_row("Drug B", "Placebo", 0.85),
                _league_row("Drug C", "Placebo", 0.90),
            ],
        ),
        sucra=[
            _sucra_row("Drug A", 1),
            _sucra_row("Drug B", 2),
            _sucra_row("Drug C", 3),
            _sucra_row("Placebo", 4),
        ],
        network=NetworkGraph(
            nodes=[
                NetworkNode(intervention="Placebo", n_studies=8, n_participants=2400),
                NetworkNode(intervention="Drug A", n_studies=3, n_participants=900),
                NetworkNode(intervention="Drug B", n_studies=3, n_participants=900),
                NetworkNode(intervention="Drug C", n_studies=2, n_participants=600),
            ],
            edges=[
                NetworkEdge(
                    source_intervention="Placebo", target_intervention="Drug A", n_trials=2
                ),
                NetworkEdge(
                    source_intervention="Placebo", target_intervention="Drug B", n_trials=2
                ),
                NetworkEdge(
                    source_intervention="Placebo", target_intervention="Drug C", n_trials=1
                ),
                NetworkEdge(source_intervention="Drug A", target_intervention="Drug B", n_trials=1),
            ],
        ),
        studies_included=["12345", "12346"],
        interpretation="Drug A has highest SUCRA.",
    )
    rebuilt = NmaResults.model_validate_json(results.model_dump_json())
    assert rebuilt.backend == "frequentist"
    assert len(rebuilt.league_table.rows) == 3
    assert rebuilt.sucra[0].rank == 1


def test_results_requires_three_or_more_sucra_rows() -> None:
    pico = _pico()
    with pytest.raises(ValidationError):
        NmaResults(
            pico=pico,
            backend="frequentist",
            league_table=LeagueTable(effect_measure="OR", rows=[]),
            sucra=[_sucra_row("A", 1), _sucra_row("B", 2)],  # too few
            network=NetworkGraph(nodes=[], edges=[]),
            studies_included=[],
            interpretation="x",
        )


def test_sucra_clamps_to_unit_interval() -> None:
    with pytest.raises(ValidationError):
        SucraRow(intervention="X", sucra=1.2, mean_rank=1.0, rank=1)


def test_network_edge_requires_at_least_one_trial() -> None:
    with pytest.raises(ValidationError):
        NetworkEdge(source_intervention="A", target_intervention="B", n_trials=0)
