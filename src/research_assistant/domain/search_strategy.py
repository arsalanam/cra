"""Pydantic schemas for the search-strategy specialist's turn-based workflow.

Each agent run returns one `SearchStrategyTurn` — a discriminated union over:

  clarification    — needs more info (shared shape from common)
  query_blocks     — parsed PICO blocks with MeSH + free-text terms for review
  strategy_result  — composed Boolean query + per-database hit counts +
                     sample hits + refinement suggestions; iterable until
                     the user clicks Finalize

Anti-hallucination posture mirrors meta_analysis:
  - Every `MeshTerm` cited must come from a real `mesh_lookup` call this turn.
  - Every `StudyRef` in `sample_hits` must come from a real `search_papers`
    call this turn.

Validators live in the specialist module (where they have access to
AgentDeps recording tool calls), not on the schemas themselves — schemas
must remain serialisable across the wire.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .common import ClarificationRequest

PicoLabel = Literal["population", "intervention", "comparison", "outcome"]

DatabaseId = Literal[
    "pubmed",
    "europepmc",
    "embase",
    "cochrane_central",
    "cinahl",
    "scopus",
]

BandStatus = Literal["below", "in_band", "above"]


class MeshTerm(BaseModel):
    """One MeSH descriptor returned by `mesh_lookup`."""

    descriptor: str = Field(description="Canonical MeSH descriptor, e.g. 'Proton Pump Inhibitors'.")
    mesh_id: str = Field(
        description=(
            "MeSH UID returned by mesh_lookup. Must match a mesh_lookup result "
            "from this turn — the validator rejects fabricated IDs."
        ),
    )
    entry_terms: list[str] = Field(
        default_factory=list,
        description="Synonyms PubMed indexes the descriptor under. Used as [tiab] fallbacks.",
    )


class QueryBlock(BaseModel):
    """One PICO concept expanded into a Boolean fragment."""

    label: PicoLabel
    concept: str = Field(description="Plain-English concept the block covers.")
    mesh_terms: list[MeshTerm] = Field(
        default_factory=list,
        description="MeSH descriptors verified via mesh_lookup.",
    )
    free_text_synonyms: list[str] = Field(
        default_factory=list,
        description=(
            "Title/abstract synonyms the agent adds (used as [tiab] fallbacks). "
            "May include MeSH entry terms the agent surfaces from mesh_terms."
        ),
    )
    composed: str = Field(
        description=(
            "The Boolean fragment for this block, e.g. "
            '("Proton Pump Inhibitors"[MeSH] OR "PPI"[tiab] OR "omeprazole"[tiab])'
        ),
    )


class QueryBlocks(BaseModel):
    """Specialist has parsed the question into PICO blocks; user reviews/edits."""

    kind: Literal["query_blocks"] = "query_blocks"
    research_question: str
    blocks: list[QueryBlock] = Field(
        description="One block per PICO concept used in the query (typically 2–4).",
    )
    notes: str | None = Field(
        default=None,
        description="Caveats, e.g. 'Comparator omitted because question is single-arm'.",
    )


class StudyRef(BaseModel):
    """Lightweight reference to a sample hit from search_papers.

    Subset of `domain.meta_analysis.StudyCandidate` fields — kept thin
    because strategy_result only displays a handful of titles for sanity
    checking, not the full extraction-ready record. The hand-off to
    meta_analysis re-runs the search there.
    """

    source: str
    source_id: str
    pmid: str | None = None
    doi: str | None = None
    title: str
    journal: str | None = None
    year: int | None = None


class QueryPlan(BaseModel):
    """One database's query.

    `executable=True` means we ran it via search_papers and `estimated_hits`
    is real. `executable=False` means it's a translated plan for the user
    to paste into a database we don't have programmatic access to (Embase,
    Cochrane CENTRAL, etc.).
    """

    database: DatabaseId
    syntax_dialect: str = Field(
        description="Human-readable dialect label, e.g. 'PubMed Boolean', 'Emtree + .lim'.",
    )
    composed_query: str = Field(description="The string the user pastes / we execute.")
    executable: bool = Field(
        description="True only for databases this app can query (pubmed, europepmc).",
    )
    estimated_hits: int | None = Field(
        default=None,
        description="Populated only when executable=True.",
    )
    caveats: list[str] = Field(
        default_factory=list,
        description=(
            "Per-plan warnings. Embase plans MUST include "
            "'Emtree terms are unverified — confirm against Embase Emtree thesaurus'."
        ),
    )
    access_note: str | None = Field(
        default=None,
        description="e.g. 'Requires institutional Embase subscription'.",
    )


class Refinement(BaseModel):
    """One concrete next move the user can apply to narrow / broaden."""

    direction: Literal["narrow", "broaden"]
    label: str = Field(description="Short button label, e.g. 'Restrict to last 10 years'.")
    rationale: str = Field(description="Why this move addresses the current band miss.")
    continuation: str = Field(
        description=(
            "Exact continuation message the frontend sends when the user clicks. "
            "Must start with 'Tighten:' or 'Broaden:' so the dispatcher routes "
            "back to this specialist."
        ),
    )


class StrategyResult(BaseModel):
    """Composed query + executed hit count + samples + refinement options.

    Iterable: the user clicks a Refinement (or types freeform tightening) to
    get another StrategyResult. Once they click Finalize, the agent returns
    one with `is_final=True` and the handoff CTA appears in the UI.
    """

    kind: Literal["strategy_result"] = "strategy_result"
    query_plans: list[QueryPlan] = Field(
        description=(
            "Always includes pubmed and europepmc plans (executable=True). "
            "May include embase / cochrane_central / cinahl / scopus plans "
            "(executable=False) for the user to run manually."
        ),
    )
    target_band: tuple[int, int] = Field(
        default=(50, 500),
        description="Lower/upper hit-count target (judged on PubMed count).",
    )
    band_status: BandStatus = Field(
        description="Whether the executed PubMed hit count falls inside the target band.",
    )
    sample_hits: list[StudyRef] = Field(
        description="Up to 5 PubMed-preferred sample hits for sanity checking.",
    )
    refinement_suggestions: list[Refinement] = Field(
        default_factory=list,
        description=(
            "Empty when band_status='in_band' or is_final=True; otherwise 2–3 "
            "concrete moves the user can take with one click."
        ),
    )
    is_final: bool = Field(
        default=False,
        description="True after the user clicks Finalize; enables the meta-analysis handoff CTA.",
    )
    notes: str | None = None


# Discriminated union of every shape the search-strategy specialist may emit.
SearchStrategyTurn = Annotated[
    ClarificationRequest | QueryBlocks | StrategyResult,
    Field(discriminator="kind"),
]
