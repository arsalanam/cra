"""Pydantic schemas for the living-review (literature watch) feature.

Unlike the dispatcher specialists, this isn't a turn-based workflow — it
runs in the background, scheduled by APScheduler. The shapes here are:

  WatchSpec        — payload accepted by POST /api/watches when creating
                     a watch from a finalised strategy_result
  PaperTriage      — the triage agent's per-paper verdict
  WatchRunSummary  — the triage agent's full output for one run: per-paper
                     triages + a 1–2 sentence significance summary +
                     whether to raise a notification
  NotificationView — what the sidebar bell + watches dashboard render

`PaperTriage` provenance: the triage agent receives the saved PICO + a
single paper's title/abstract and produces a verdict. No tools — the
agent works from passed-in text only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .meta_analysis import PicoTable

Relevance = Literal["high", "moderate", "low", "off-topic"]
DesignFit = Literal["matches", "partial", "no"]


class PaperTriage(BaseModel):
    """One triage verdict from the watch_triage agent."""

    pmid: str
    title: str = Field(
        description="Echoed back so the notification UI can show it without a fetch.",
    )
    relevance: Relevance = Field(
        description=(
            "Does this paper address the saved PICO? 'high' = exact match; "
            "'moderate' = adjacent (e.g. same intervention, broader population); "
            "'low' = tangential; 'off-topic' = unrelated, likely indexing noise."
        ),
    )
    design_fit: DesignFit = Field(
        description=(
            "Does the study design match the saved eligibility (study_types)? "
            "'matches' = yes; 'partial' = related design (e.g. cohort when RCT "
            "wanted but evidence is sparse); 'no' = wrong design."
        ),
    )
    materiality: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Probability (0–1) this paper could change the conclusions of a "
            "meta-analysis on the saved PICO. Drives the notification gate. "
            "Anchor: 0.0 = irrelevant; 0.3 = relevant but small/underpowered; "
            "0.6 = high-quality RCT in scope; 0.9 = landmark / practice-changing."
        ),
    )
    note: str = Field(
        description="One sentence explaining the triage verdict.",
    )


class WatchRunSummary(BaseModel):
    """The triage agent's output for one watch run."""

    triages: list[PaperTriage] = Field(
        default_factory=list,
        description="One entry per new paper passed to the agent (may be empty).",
    )
    significance_summary: str = Field(
        description=(
            "1–2 sentence summary suitable for the notification body. Lead "
            "with the count of material papers and the most consequential "
            "finding. Honest about uncertainty — do not overstate impact."
        ),
    )
    notify: bool = Field(
        description=(
            "True if at least one triage's materiality >= the watch's "
            "configured threshold. The runner gates Notification creation "
            "on this flag."
        ),
    )


class WatchSpec(BaseModel):
    """Create-watch payload — accepted by POST /api/watches.

    The frontend builds this from a finalised strategy_result turn plus a
    small modal asking for name, schedule, and threshold. The runner
    snapshots the validated query + PICO so future re-runs are reproducible
    even if the source strategy_result is later deleted.
    """

    name: str = Field(min_length=1, max_length=200)
    pico: PicoTable
    search_query: str = Field(
        description="The validated PubMed Boolean string to re-execute on schedule.",
    )
    sources: list[str] = Field(
        default_factory=lambda: ["pubmed", "europepmc"],
        description="Source ids to query at each run.",
    )
    schedule_cron: str = Field(
        description=(
            "APScheduler cron expression. Five fields: minute hour day month "
            "dow. e.g. '0 9 * * 1' = Mondays 09:00; '*/2 * * * *' = every 2 "
            "minutes (demo only); '0 9 * * *' = daily 09:00."
        ),
    )
    triage_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Min materiality score (0–1) for a paper to trigger notification.",
    )
    baseline_pmids: list[str] = Field(
        default_factory=list,
        description=(
            "PMIDs already known at watch creation. Typically populated from "
            "the sample_hits + a one-shot full search at create time so the "
            "first scheduled run only surfaces genuinely new papers."
        ),
    )


# ── Read-side projections (REST responses) ───────────────────────────────


class WatchView(BaseModel):
    """What GET /api/watches returns per row."""

    id: str
    name: str
    status: str
    schedule_cron: str
    triage_threshold: float
    sources: list[str]
    baseline_size: int
    last_run_at: datetime | None
    last_run_status: str | None
    next_run_at: datetime | None
    created_at: datetime


class WatchRunView(BaseModel):
    """What GET /api/watches/{id}/runs returns per row."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    total_hits: int
    new_pmids: list[str]
    triages: list[PaperTriage]
    significance_summary: str | None
    error_message: str | None


class NotificationView(BaseModel):
    """What GET /api/notifications returns per row."""

    id: str
    watch_id: str
    watch_name: str
    run_id: str
    title: str
    summary: str
    new_paper_count: int
    created_at: datetime
    read_at: datetime | None
