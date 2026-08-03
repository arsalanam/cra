"""Unit tests for the search-strategy domain schemas.

Schema-level tests only — provenance enforcement (MeSH IDs / sample-hit
PMIDs must come from real tool calls in the same turn) is handled by the
system prompt, not pydantic validators, mirroring the meta-analysis
posture.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.search_strategy import (
    MeshTerm,
    QueryBlock,
    QueryBlocks,
    QueryPlan,
    Refinement,
    StrategyResult,
    StudyRef,
)


def _ppi_block() -> QueryBlock:
    return QueryBlock(
        label="intervention",
        concept="proton pump inhibitor",
        mesh_terms=[
            MeshTerm(
                descriptor="Proton Pump Inhibitors",
                mesh_id="D054328",
                entry_terms=["Omeprazole", "Lansoprazole"],
            )
        ],
        free_text_synonyms=["PPI", "omeprazole", "pantoprazole"],
        composed=(
            '("Proton Pump Inhibitors"[MeSH] OR "PPI"[tiab] '
            'OR "omeprazole"[tiab] OR "pantoprazole"[tiab])'
        ),
    )


def _acs_block() -> QueryBlock:
    return QueryBlock(
        label="population",
        concept="acute coronary syndrome",
        mesh_terms=[
            MeshTerm(
                descriptor="Acute Coronary Syndrome",
                mesh_id="D054058",
                entry_terms=["ACS"],
            )
        ],
        free_text_synonyms=["ACS", "post-PCI"],
        composed='("Acute Coronary Syndrome"[MeSH] OR "ACS"[tiab] OR "post-PCI"[tiab])',
    )


def test_query_blocks_round_trip() -> None:
    qb = QueryBlocks(
        research_question="Does adding PPI to DAPT reduce GI bleeding post-PCI?",
        blocks=[_ppi_block(), _acs_block()],
        notes=None,
    )
    dumped = qb.model_dump_json()
    assert '"kind":"query_blocks"' in dumped
    revived = QueryBlocks.model_validate_json(dumped)
    assert revived.research_question == qb.research_question
    assert len(revived.blocks) == 2
    assert revived.blocks[0].mesh_terms[0].mesh_id == "D054328"


def test_strategy_result_full_round_trip() -> None:
    sr = StrategyResult(
        query_plans=[
            QueryPlan(
                database="pubmed",
                syntax_dialect="PubMed Boolean",
                composed_query="(...) AND humans[mh] AND English[la]",
                executable=True,
                estimated_hits=247,
            ),
            QueryPlan(
                database="europepmc",
                syntax_dialect="Europe PMC search syntax",
                composed_query="(...) AND humans[mh] AND English[la]",
                executable=True,
                estimated_hits=312,
            ),
            QueryPlan(
                database="cochrane_central",
                syntax_dialect="Cochrane Library CENTRAL",
                composed_query=(
                    "MeSH descriptor: [Proton Pump Inhibitors] explode all trees "
                    "AND MeSH descriptor: [Acute Coronary Syndrome] explode all trees"
                ),
                executable=False,
                caveats=["Run inside Cochrane Library — limit to CENTRAL trial register"],
            ),
            QueryPlan(
                database="embase",
                syntax_dialect="Emtree + Embase syntax",
                composed_query=("'proton pump inhibitor'/exp AND 'acute coronary syndrome'/exp"),
                executable=False,
                caveats=[
                    "Emtree terms are unverified — confirm against Embase Emtree "
                    "thesaurus before running."
                ],
                access_note="Requires institutional Embase subscription",
            ),
        ],
        target_band=(50, 500),
        band_status="in_band",
        sample_hits=[
            StudyRef(
                source="pubmed",
                source_id="20925534",
                pmid="20925534",
                doi="10.1056/NEJMoa1007964",
                title="Clopidogrel with or without omeprazole in coronary artery disease",
                journal="N Engl J Med",
                year=2010,
            ),
        ],
        refinement_suggestions=[],
        is_final=False,
    )
    dumped = sr.model_dump_json()
    revived = StrategyResult.model_validate_json(dumped)
    assert revived.kind == "strategy_result"
    assert len(revived.query_plans) == 4
    pubmed = next(p for p in revived.query_plans if p.database == "pubmed")
    assert pubmed.executable
    assert pubmed.estimated_hits == 247
    embase = next(p for p in revived.query_plans if p.database == "embase")
    assert not embase.executable
    assert embase.estimated_hits is None
    assert any("Emtree" in c for c in embase.caveats)
    assert revived.band_status == "in_band"


def test_executable_false_plan_can_omit_estimated_hits() -> None:
    plan = QueryPlan(
        database="embase",
        syntax_dialect="Emtree",
        composed_query="...",
        executable=False,
        caveats=[
            "Emtree terms are unverified — confirm against Embase Emtree thesaurus before running."
        ],
    )
    assert plan.estimated_hits is None


def test_refinement_continuation_must_be_present() -> None:
    """Refinement requires continuation — schema enforces presence (not prefix)."""
    with pytest.raises(ValidationError):
        Refinement.model_validate({"direction": "narrow", "label": "x", "rationale": "y"})


def test_target_band_is_a_tuple() -> None:
    """Two-int tuple. Pydantic v2 coerces lists to tuples."""
    sr = StrategyResult(
        query_plans=[
            QueryPlan(
                database="pubmed",
                syntax_dialect="PubMed Boolean",
                composed_query="x",
                executable=True,
                estimated_hits=100,
            ),
            QueryPlan(
                database="europepmc",
                syntax_dialect="Europe PMC search syntax",
                composed_query="x",
                executable=True,
                estimated_hits=120,
            ),
        ],
        target_band=(10, 200),
        band_status="in_band",
        sample_hits=[],
    )
    assert sr.target_band == (10, 200)
