"""Pydantic schemas for the manuscript_drafter specialist (top-6 #5).

Each agent run returns one `ManuscriptTurn` — a discriminated union over:

  clarification        — needs more info (shared shape from common)
  manuscript_intake    — target journal + structured-abstract flag +
                         word-count budget + section seeds the user
                         confirms before drafting
  manuscript_draft     — full IMRaD draft (title + abstract +
                         introduction + methods + results + discussion +
                         references), iterable until Finalize
  reviewer_response    — point-by-point responses to peer reviewers,
                         iterable, only available after a final draft

Composition workflow: the manuscript drafter is downstream of
meta_analysis / sr_protocol / risk_of_bias. The user typically pastes
the JSON / summary of an upstream artefact into the intake and the
specialist composes the IMRaD around it. The Results section
specifically MUST NOT invent effect-size numbers — those come from the
pasted source verbatim.

Anti-hallucination posture mirrors the other specialists:
  - Every Citation must come from a real tool call this turn
    (search_papers / web_search / wikipedia).
  - Every numerical claim in Results must trace to the pasted source
    artefact — no training-data effect sizes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .common import ClarificationRequest

# ── Categorical vocabularies ─────────────────────────────────────────────


JournalTarget = Literal[
    "nejm",  # New England Journal of Medicine — structured abstract, long Methods
    "lancet",  # The Lancet — structured abstract, narrative Methods
    "bmj",  # The BMJ — structured abstract, IMRaD with Strengths/Limitations
    "jama",  # JAMA — structured abstract + key points
    "annals",  # Annals of Internal Medicine — structured abstract
    "plos_one",  # PLOS ONE — no length limit, IMRaD
    "generic",  # Generic IMRaD shape; default
]

ArtefactKind = Literal[
    "systematic_review",
    "meta_analysis",
    "rct_report",
    "observational_study",
    "scoping_review",
    "narrative_review",
    "other",
]

CitationOrigin = Literal["search_papers", "web_search", "wikipedia", "pasted_source"]


# ── Stage 1 — intake ─────────────────────────────────────────────────────


class ManuscriptIntake(BaseModel):
    """Stage 1 turn — the user confirms the target journal + section
    seeds before drafting begins.

    `source_artefact_paste` is the raw JSON / Markdown the user paste
    from a prior workflow (e.g. a `meta_analysis` turn's full output).
    The specialist's STEP 2 system prompt instructs it to ground all
    Results numbers in this paste; without it, the drafter can only
    produce a skeleton with `[USER INPUT NEEDED]` placeholders.
    """

    kind: Literal["manuscript_intake"] = "manuscript_intake"

    artefact_kind: ArtefactKind = Field(
        description=(
            "What kind of study the manuscript is reporting. Drives the "
            "Methods section shape (RCT → CONSORT structure, systematic "
            "review → PRISMA structure, etc.)."
        ),
    )
    journal_target: JournalTarget = Field(
        default="generic",
        description=(
            "Target journal. Drives section-length budgets + abstract "
            "shape (structured vs free-form). `generic` is a safe default."
        ),
    )
    structured_abstract: bool = Field(
        default=True,
        description=(
            "True for most clinical-research journals (Background / Methods "
            "/ Results / Conclusions sub-headings). False for short reports "
            "or commentary."
        ),
    )
    abstract_word_budget: int = Field(
        default=250,
        ge=100,
        le=600,
        description="Word limit for the abstract. NEJM 250, Lancet 300, BMJ 400.",
    )
    body_word_budget: int = Field(
        default=3500,
        ge=500,
        le=10000,
        description=(
            "Word limit for the body (Introduction + Methods + Results + "
            "Discussion). NEJM 2700, Lancet 4500, BMJ 4000, PLOS ONE no limit."
        ),
    )

    working_title: str = Field(
        description="Working title — the model may refine it during drafting."
    )
    research_question: str = Field(
        description=(
            "The single research question the manuscript answers. Becomes "
            "the lead sentence of the Introduction and frames the abstract."
        ),
    )
    key_findings_paste: str | None = Field(
        default=None,
        description=(
            "Free-form summary of the main findings — pooled effect, "
            "confidence intervals, sample size, study count. The drafter "
            "uses this verbatim in the Results section."
        ),
    )
    source_artefact_paste: str | None = Field(
        default=None,
        description=(
            "Raw JSON or Markdown of the upstream artefact "
            "(meta_analysis / sr_protocol / risk_of_bias turn output). "
            "Becomes the source of truth for Results numbers."
        ),
    )
    notes: str | None = None


# ── Stage 2 — full draft ─────────────────────────────────────────────────


class ManuscriptCitation(BaseModel):
    """One reference. Same shape as sr_protocol's Citation."""

    n: int = Field(description="Bracketed citation index (1-based).")
    title: str
    authors: str | None = None
    journal: str | None = None
    year: int | None = None
    pmid: str | None = None
    doi: str | None = None
    url: str | None = None
    origin: CitationOrigin = Field(
        description=(
            "Which tool surfaced this reference this turn. `pasted_source` "
            "means the reference was already in the upstream artefact the "
            "user pasted — preserved verbatim, not re-fetched."
        ),
    )


class StructuredAbstract(BaseModel):
    """Conventional Background / Methods / Results / Conclusions shape.

    Used when `intake.structured_abstract=True`. The free-form variant
    is just a single block of text in `ManuscriptDraft.abstract_text`.
    """

    background: str = Field(description="1-2 sentences framing the gap.")
    methods: str = Field(description="Design, population, intervention, primary outcome.")
    results: str = Field(description="Key effect estimates with 95% CIs and N.")
    conclusions: str = Field(description="What the findings mean clinically.")


class ManuscriptDraft(BaseModel):
    """Stage 2 turn — the assembled IMRaD draft.

    Iterable: refinement messages produce another `ManuscriptDraft`
    with `is_final=False`. The user clicks Finalize to lock the draft
    and enable the Stage 3 reviewer-response loop + the PDF/DOCX
    download buttons.

    Every Citation in `references` MUST originate from a real tool call
    this turn OR be carried forward verbatim from the user's pasted
    source artefact (`origin="pasted_source"`). The system prompt
    enforces this — no training-data citations.
    """

    kind: Literal["manuscript_draft"] = "manuscript_draft"

    title: str
    short_title: str | None = Field(
        default=None,
        description="Running head for the journal — typically <40 chars.",
    )

    # Abstract — only ONE of the two fields is populated. The intake's
    # `structured_abstract` flag drives which.
    structured_abstract: StructuredAbstract | None = Field(
        default=None,
        description="Populated when intake.structured_abstract=True.",
    )
    abstract_text: str | None = Field(
        default=None,
        description="Populated when intake.structured_abstract=False.",
    )
    abstract_word_count: int = Field(
        default=0, ge=0, description="Computed; the model fills it in."
    )

    introduction: str = Field(
        description=(
            "Background + rationale + the explicit research question. "
            "1-2 paragraphs. Every concrete claim is backed by a "
            "Citation."
        ),
    )
    methods: str = Field(
        description=(
            "Study design + population + intervention/exposure + outcomes "
            "+ analysis plan. For meta-analysis manuscripts, mirrors the "
            "sr_protocol's Methods section."
        ),
    )
    results: str = Field(
        description=(
            "Key findings with effect estimates + 95% CIs + N. EVERY "
            "number must trace to the pasted source artefact — never "
            "invented."
        ),
    )
    discussion: str = Field(
        description=(
            "Interpretation + comparison with prior literature + "
            "strengths + limitations + clinical implications + future "
            "directions. The most opinion-loaded section."
        ),
    )

    references: list[ManuscriptCitation] = Field(default_factory=list)
    full_markdown: str = Field(
        description=(
            "Complete manuscript in Markdown — title, structured / free-form "
            "abstract, IMRaD body with inline [n] citations, references list. "
            "Copy-paste ready into a journal submission system."
        ),
    )

    body_word_count: int = Field(default=0, ge=0)
    is_final: bool = Field(default=False)
    notes: str | None = None


# ── Stage 3 — reviewer response ──────────────────────────────────────────


class ReviewerComment(BaseModel):
    """One comment from a reviewer."""

    reviewer_id: str = Field(
        description="Which reviewer raised the comment (e.g. 'R1', 'R2', 'Editor')."
    )
    comment_text: str = Field(
        description="The reviewer's verbatim comment (as the user pasted it)."
    )


class ResponseItem(BaseModel):
    """One point-by-point response."""

    reviewer_id: str
    comment_excerpt: str = Field(
        description=(
            "A short verbatim quote of the comment so the reviewer can "
            "see what we're responding to (peer-review convention)."
        ),
    )
    response_text: str = Field(
        description=(
            "The author's response. Direct + concrete. Cites manuscript "
            "page/line numbers where applicable."
        ),
    )
    suggested_manuscript_edits: str | None = Field(
        default=None,
        description=(
            "Optional concrete text the author should add to the "
            "manuscript to address this comment. Markdown formatted."
        ),
    )
    is_addressed: bool = Field(
        default=True,
        description=(
            "False when the response is to push back on the reviewer's "
            "request rather than accommodate it. Useful for tracking "
            "which comments are still under negotiation."
        ),
    )


class ReviewerResponse(BaseModel):
    """Stage 3 turn — point-by-point responses to peer reviewers.

    Iterable: the user pastes a fresh round of comments and the model
    emits another ReviewerResponse keyed to the same manuscript. The
    final round is marked `is_final=True` to enable a downloadable
    "responses to reviewers" PDF (handled by the report builder).
    """

    kind: Literal["reviewer_response"] = "reviewer_response"

    cover_letter_text: str = Field(
        description=(
            "Short cover-letter paragraph to the editor, before the "
            "point-by-point responses. Acknowledges the reviewers + "
            "summarises the major revisions."
        ),
    )
    responses: list[ResponseItem] = Field(
        default_factory=list,
        description="One ResponseItem per reviewer comment, in the order received.",
    )
    is_final: bool = Field(default=False)
    notes: str | None = None


# ── Discriminated union ──────────────────────────────────────────────────


ManuscriptTurn = Annotated[
    ClarificationRequest | ManuscriptIntake | ManuscriptDraft | ReviewerResponse,
    Field(discriminator="kind"),
]


__all__ = [
    "ArtefactKind",
    "CitationOrigin",
    "JournalTarget",
    "ManuscriptCitation",
    "ManuscriptDraft",
    "ManuscriptIntake",
    "ManuscriptTurn",
    "ResponseItem",
    "ReviewerComment",
    "ReviewerResponse",
    "StructuredAbstract",
]
