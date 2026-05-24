"""Validated shape of one paper returned by any `PaperSource`.

Every source's parser builds a `StudyRecord` and emits `.model_dump()` into
its JSON envelope. The fan-out tool (`search_papers`) re-emits those dicts
verbatim to the agent, so this model is the cross-source contract that
dedup, the agent prompts, and downstream specialists all rely on.

Validation deliberately runs at the source boundary — if a new source's
parser starts producing a malformed record, it fails loudly at its own
JSON-serialisation step rather than silently downstream.

The wire format is stable: changing this model is a breaking change to the
agent's tool-result schema. Add fields conservatively.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StudyRecord(BaseModel):
    """One paper, normalised across sources."""

    # `extra="forbid"` catches accidental typos in source parsers (e.g.
    # `authers=[...]` would otherwise be silently dropped by `extra="ignore"`).
    # Sources needing extra metadata must add typed fields here.
    model_config = ConfigDict(extra="forbid")

    # Field declaration order is the wire-format order — Pydantic v2's
    # model_dump() preserves it. Matches the dicts the parsers produced
    # before validation was introduced so existing consumers (dedupe,
    # snapshot tests, agent tool results) see no change.
    source: str  # registered source id ("pubmed", "europepmc")
    source_id: str  # source-native identifier (PMID for PubMed; record id for EPMC)
    pmid: str | None = None
    title: str
    journal: str | None = None
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    publication_types: list[str] = Field(default_factory=list)
    mesh_headings: list[str] = Field(default_factory=list)
    doi: str | None = None
