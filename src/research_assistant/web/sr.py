"""Systematic-review screening API — /api/sr/*.

Three planes share this router:
  • project CRUD + membership (this module)
  • screening queue + decision recording (decisions submodule below)
  • PRISMA flow numbers + diagram render (prisma submodule below)

Permissions are layered: `sr.create` / `sr.manage` are typically granted
globally to `researcher`/`admin`; `sr.read` / `sr.screen` / `sr.adjudicate`
are granted at sr_review scope by the membership endpoint, which also
records the parallel `SrReviewMembership` row identifying which reviewer
slot the user fills (R1 vs R2 vs adjudicator).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from ..agent.specialists import sr_screening_assist
from ..auth import SessionPayload
from ..auth.rbac import Permission, ScopeType
from ..persistence.database import get_db_session
from ..persistence.models import Publication, SrReview, User
from ..persistence.sr_repository import (
    PHASE_ABSTRACT,
    PHASE_FULLTEXT,
    ROLE_ADJ,
    ROLE_R1,
    ROLE_R2,
    SrError,
    SrReviewRepository,
)
from ..persistence.user_repository import UserRepository
from ..tools.clinical.search_papers import _fan_out
from .auth import CurrentUser
from .authz import require_permission_scoped
from .threads import resolve_local_user_id

logger = logging.getLogger(__name__)


# ── DTOs ──────────────────────────────────────────────────────────────────


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str | None = None
    pico: dict[str, Any] | None = None
    search_query: str = ""
    sources: list[str] = ["pubmed", "europepmc"]
    inclusion_criteria: list[str] = []
    exclusion_criteria: list[str] = []


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    description: str | None = None
    pico: dict[str, Any] | None = None
    search_query: str | None = None
    sources: list[str] | None = None
    inclusion_criteria: list[str] | None = None
    exclusion_criteria: list[str] | None = None
    status: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str | None
    status: str
    pico: dict[str, Any]
    search_query: str
    sources: list[str]
    inclusion_criteria: list[str]
    exclusion_criteria: list[str]
    created_by: str | None
    created_at: datetime
    updated_at: datetime


def _project_out(p: SrReview) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description,
        status=p.status,
        pico=json.loads(p.pico_json or "{}"),
        search_query=p.search_query,
        sources=json.loads(p.sources_json or "[]"),
        inclusion_criteria=json.loads(p.inclusion_criteria_json or "[]"),
        exclusion_criteria=json.loads(p.exclusion_criteria_json or "[]"),
        created_by=p.created_by,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_email: str
    role: str  # reviewer_1 | reviewer_2 | adjudicator


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    user_email: str | None
    role: str
    granted_at: datetime


class IngestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # If omitted, the project's search_query is used.
    query_override: str | None = None
    max_results: int = 200


class IngestOut(BaseModel):
    records_identified: int
    duplicates_removed: int
    already_present: int
    added: int


class CandidateOut(BaseModel):
    """Public shape for screening — never reveals other reviewers' decisions."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    pmid: str | None
    title: str
    abstract: str | None
    authors: list[str]
    journal: str | None
    year: int | None
    source_origin: str | None
    current_status: str
    ai_suggestion: dict[str, Any] | None = None


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    phase: str  # abstract | fulltext
    decision: str  # include | exclude | maybe
    reason_code: str | None = None
    notes: str | None = None
    ai_suggested: bool = False


class DecisionOut(BaseModel):
    candidate_status: str
    decision_id: str
    accepted: bool = True


class ConflictItem(BaseModel):
    candidate_id: str
    pmid: str | None
    title: str
    decisions: list[dict[str, Any]]


class PrismaOut(BaseModel):
    records_screened: int
    excluded_at_abstract: int
    excluded_reasons_abstract: dict[str, int]
    full_text_assessed: int
    excluded_at_fulltext: int
    excluded_reasons_fulltext: dict[str, int]
    studies_included: int
    by_status: dict[str, int]


def create_sr_router() -> APIRouter:
    router = APIRouter(prefix="/sr", tags=["sr"])

    # ── Projects ─────────────────────────────────────────────────────────

    @router.post("/projects", response_model=ProjectOut, status_code=201)
    async def create_project(
        body: ProjectIn,
        user: SessionPayload = require_permission_scoped(Permission.SR_CREATE),
    ) -> ProjectOut:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            review = await repo.create_project(
                name=body.name,
                description=body.description,
                pico=body.pico,
                search_query=body.search_query,
                sources=body.sources,
                inclusion_criteria=body.inclusion_criteria,
                exclusion_criteria=body.exclusion_criteria,
                created_by=owner,
            )
            return _project_out(review)

    @router.get("/projects", response_model=list[ProjectOut])
    async def list_projects(user: CurrentUser) -> list[ProjectOut]:
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            projects = await repo.list_projects_for_user(owner)
            return [_project_out(p) for p in projects]

    @router.get("/projects/{sr_project_id}", response_model=ProjectOut)
    async def get_project(
        sr_project_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_READ, resource_param="sr_project_id"
        ),
    ) -> ProjectOut:
        async with get_db_session() as session:
            review = await SrReviewRepository(session).get_project(sr_project_id)
            if review is None:
                raise HTTPException(404, "SR project not found")
            return _project_out(review)

    @router.patch("/projects/{sr_project_id}", response_model=ProjectOut)
    async def update_project(
        sr_project_id: str,
        body: ProjectPatch,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_MANAGE, resource_param="sr_project_id"
        ),
    ) -> ProjectOut:
        updates = body.model_dump(exclude_unset=True)
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            review = await repo.update_project(sr_project_id, **updates)
            if review is None:
                raise HTTPException(404, "SR project not found")
            return _project_out(review)

    @router.delete("/projects/{sr_project_id}", status_code=204)
    async def delete_project(
        sr_project_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_MANAGE, resource_param="sr_project_id"
        ),
    ) -> None:
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            ok = await repo.delete_project(sr_project_id)
            if not ok:
                raise HTTPException(404, "SR project not found")
            return None

    # ── Membership (creates parallel RoleAssignment for RBAC) ────────────

    @router.get("/projects/{sr_project_id}/members", response_model=list[MemberOut])
    async def list_members(
        sr_project_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_READ, resource_param="sr_project_id"
        ),
    ) -> list[MemberOut]:
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            members = await repo.list_members(sr_project_id)
            # Hydrate email for the UI list.
            out: list[MemberOut] = []
            for m in members:
                u = await session.get(User, m.user_id)
                out.append(
                    MemberOut(
                        id=m.id,
                        user_id=m.user_id,
                        user_email=u.email if u else None,
                        role=m.role,
                        granted_at=m.granted_at,
                    )
                )
            return out

    @router.post(
        "/projects/{sr_project_id}/members",
        response_model=MemberOut,
        status_code=201,
    )
    async def add_member(
        sr_project_id: str,
        body: MemberIn,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_MANAGE, resource_param="sr_project_id"
        ),
    ) -> MemberOut:
        """Assign a reviewer slot AND grant the matching sr_review-scoped role.

        Two DB writes in one transaction:
          1. SrReviewMembership row (the slot identity)
          2. RoleAssignment with scope=sr_review, scope_id=project_id (the
             RBAC grant that satisfies sr.read / sr.screen / sr.adjudicate
             checks on this project for this user).

        Both writes are idempotent on (project, user, role); re-posting the
        same trio is a clean no-op.
        """
        if body.role not in (ROLE_R1, ROLE_R2, ROLE_ADJ):
            raise HTTPException(
                422,
                f"Role must be one of {ROLE_R1}, {ROLE_R2}, {ROLE_ADJ}; "
                f"got {body.role!r}.",
            )
        granter = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            urepo = UserRepository(session)
            target = await urepo.get_by_email(body.user_email.lower().strip())
            if target is None:
                raise HTTPException(
                    404,
                    f"No user with email {body.user_email!r}. "
                    f"Invite them via the admin panel first.",
                )
            try:
                mem = await repo.add_member(
                    sr_project_id,
                    user_id=target.id,
                    role=body.role,
                    granted_by=granter,
                )
            except SrError as e:
                raise HTTPException(422, str(e)) from e
            await urepo.grant_role(
                target.id,
                body.role,
                scope_type=ScopeType.SR_REVIEW.value,
                scope_id=sr_project_id,
                granted_by=granter,
            )
            logger.info(
                "SR project %s — granted %s to user %s (by %s)",
                sr_project_id,
                body.role,
                target.id,
                granter,
            )
            return MemberOut(
                id=mem.id,
                user_id=target.id,
                user_email=target.email,
                role=mem.role,
                granted_at=mem.granted_at,
            )

    @router.delete(
        "/projects/{sr_project_id}/members/{membership_id}", status_code=204
    )
    async def remove_member(
        sr_project_id: str,
        membership_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_MANAGE, resource_param="sr_project_id"
        ),
    ) -> None:
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            members = await repo.list_members(sr_project_id)
            target = next((m for m in members if m.id == membership_id), None)
            if target is None:
                raise HTTPException(404, "Membership not found")
            await repo.remove_member(membership_id)
            # Revoke the RBAC grant in parallel — find the matching assignment.
            urepo = UserRepository(session)
            assignments = await urepo.assignments_for_user(target.user_id)
            for a in assignments:
                if (
                    a.role == target.role
                    and a.scope_type == ScopeType.SR_REVIEW.value
                    and a.scope_id == sr_project_id
                ):
                    await urepo.revoke_role(a.id)
                    break
            return None

    # ── Ingest: run the search and snapshot candidates ───────────────────

    @router.post(
        "/projects/{sr_project_id}/ingest", response_model=IngestOut, status_code=201
    )
    async def ingest_candidates(
        sr_project_id: str,
        body: IngestIn,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_MANAGE, resource_param="sr_project_id"
        ),
    ) -> IngestOut:
        """Fan out the project's search query and stage candidates for screening.

        Reuses the multi-source `search_papers._fan_out` so the SR project
        sees the same deduped, write-through-cached result set the agent
        would. Re-running the endpoint adds only NEW candidates — existing
        ones are counted in `already_present` and skipped.
        """
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            review = await repo.get_project(sr_project_id)
            if review is None:
                raise HTTPException(404, "SR project not found")
            query = body.query_override or review.search_query
            if not query.strip():
                raise HTTPException(
                    422,
                    "No search query — set the project's `search_query` or "
                    "pass `query_override` in the body.",
                )
        envelope = json.loads(await _fan_out(query, max_results=body.max_results))
        if envelope.get("error"):
            raise HTTPException(503, str(envelope["error"]))
        studies = envelope.get("studies") or []
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            counts = await repo.ingest_candidates(sr_project_id, studies)
            # Flip status from draft → ingested if this is the first ingest.
            review = await repo.get_project(sr_project_id)
            if review is not None and review.status == "draft" and counts["added"] > 0:
                review.status = "abstract_screening"
                await session.flush()
        return IngestOut(**counts)

    # ── Screening queue + decisions ──────────────────────────────────────

    @router.get("/projects/{sr_project_id}/queue", response_model=CandidateOut | None)
    async def next_in_queue(
        sr_project_id: str,
        phase: str = PHASE_ABSTRACT,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_SCREEN, resource_param="sr_project_id"
        ),
    ) -> CandidateOut | None:
        """Return the next candidate this reviewer hasn't decided yet.

        Blind: the response does NOT include any OTHER reviewer's decisions
        (R1 only sees their own queue, never R2's votes — and vice versa).
        Adjudicators additionally see pending_adjudication candidates for
        the phase in the same queue.
        """
        if phase not in (PHASE_ABSTRACT, PHASE_FULLTEXT):
            raise HTTPException(400, f"phase must be 'abstract' or 'fulltext'; got {phase!r}.")
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            cand = await repo.next_for_reviewer(
                sr_project_id, user_id=owner, phase=phase
            )
            if cand is None:
                return None
            pub = await session.get(Publication, cand.publication_id)
            if pub is None:
                raise HTTPException(409, "Candidate references a missing publication")
            ai = await repo.get_ai_suggestion(cand.id, phase)
            ai_payload: dict[str, Any] | None = None
            if ai is not None:
                ai_payload = {
                    "predicted_decision": ai.predicted_decision,
                    "predicted_reason_code": ai.predicted_reason_code,
                    "confidence": ai.confidence,
                    "rationale": ai.rationale,
                }
            return CandidateOut(
                id=cand.id,
                pmid=cand.pmid,
                title=pub.title,
                abstract=pub.abstract,
                authors=json.loads(pub.authors_json or "[]"),
                journal=pub.journal,
                year=pub.year,
                source_origin=cand.source_origin,
                current_status=cand.current_status,
                ai_suggestion=ai_payload,
            )

    @router.post(
        "/projects/{sr_project_id}/decisions",
        response_model=DecisionOut,
        status_code=201,
    )
    async def submit_decision(
        sr_project_id: str,
        body: DecisionIn,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_SCREEN, resource_param="sr_project_id"
        ),
    ) -> DecisionOut:
        """Record this reviewer's decision and recompute the candidate's status.

        Role is derived from the caller's membership row(s) on the project
        (R1 / R2 / Adjudicator). Adjudicators submitting in the
        pending_adjudication phase override; R1/R2 submitting after
        adjudicator has decided gets rejected with 409 (the phase is closed
        for them).
        """
        owner = await resolve_local_user_id(user)
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            roles = await repo.role_for_user_on_project(sr_project_id, owner)
            if not roles:
                raise HTTPException(
                    403,
                    "You are not assigned a reviewer slot on this project.",
                )
            # Pick the most-authoritative role the caller holds. Adjudicator
            # outranks R1/R2 — submitting from the adjudicator chair locks
            # the phase.
            role = (
                ROLE_ADJ
                if ROLE_ADJ in roles
                else (ROLE_R1 if ROLE_R1 in roles else ROLE_R2)
            )
            try:
                decision, cand = await repo.record_decision(
                    candidate_id=body.candidate_id,
                    reviewer_user_id=owner,
                    role=role,
                    phase=body.phase,
                    decision=body.decision,
                    reason_code=body.reason_code,
                    notes=body.notes,
                    ai_suggested=body.ai_suggested,
                )
            except SrError as e:
                raise HTTPException(422, str(e)) from e
            # Double-check the candidate belongs to this project — the
            # require_permission_scoped above gates on sr_project_id but the
            # candidate could theoretically be from another project; refuse.
            if cand.sr_review_id != sr_project_id:
                raise HTTPException(404, "Candidate not part of this project")
            return DecisionOut(
                candidate_status=cand.current_status,
                decision_id=decision.id,
            )

    # ── Conflicts (adjudicator view) ─────────────────────────────────────

    @router.get(
        "/projects/{sr_project_id}/conflicts", response_model=list[ConflictItem]
    )
    async def list_conflicts(
        sr_project_id: str,
        phase: str = PHASE_ABSTRACT,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_ADJUDICATE, resource_param="sr_project_id"
        ),
    ) -> list[ConflictItem]:
        if phase not in (PHASE_ABSTRACT, PHASE_FULLTEXT):
            raise HTTPException(400, f"phase must be 'abstract' or 'fulltext'; got {phase!r}.")
        async with get_db_session() as session:
            repo = SrReviewRepository(session)
            conflicts = await repo.list_conflicts(sr_project_id, phase=phase)
            out: list[ConflictItem] = []
            for cand, decisions in conflicts:
                pub = await session.get(Publication, cand.publication_id)
                out.append(
                    ConflictItem(
                        candidate_id=cand.id,
                        pmid=cand.pmid,
                        title=pub.title if pub else "(missing)",
                        decisions=[
                            {
                                "reviewer_user_id": d.reviewer_user_id,
                                "role": d.role,
                                "decision": d.decision,
                                "reason_code": d.reason_code,
                                "notes": d.notes,
                            }
                            for d in decisions
                        ],
                    )
                )
            return out

    @router.get("/projects/{sr_project_id}/progress")
    async def get_progress(
        sr_project_id: str,
        phase: str = PHASE_ABSTRACT,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_READ, resource_param="sr_project_id"
        ),
    ) -> dict[str, Any]:
        if phase not in (PHASE_ABSTRACT, PHASE_FULLTEXT):
            raise HTTPException(400, f"phase must be 'abstract' or 'fulltext'; got {phase!r}.")
        async with get_db_session() as session:
            return await SrReviewRepository(session).progress(sr_project_id, phase=phase)

    # ── PRISMA flow numbers ──────────────────────────────────────────────

    @router.get("/projects/{sr_project_id}/prisma", response_model=PrismaOut)
    async def get_prisma(
        sr_project_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.PRISMA_READ, resource_param="sr_project_id"
        ),
    ) -> PrismaOut:
        async with get_db_session() as session:
            counts = await SrReviewRepository(session).prisma_counts(sr_project_id)
            return PrismaOut(**counts)

    # ── AI-assist batch classifier ───────────────────────────────────────

    @router.post("/projects/{sr_project_id}/ai-suggest")
    async def ai_suggest_batch(
        sr_project_id: str,
        phase: str = PHASE_ABSTRACT,
        max_candidates: int = 25,
        user: SessionPayload = require_permission_scoped(
            Permission.SR_AI_ASSIST, resource_param="sr_project_id"
        ),
    ) -> dict[str, Any]:
        """Run the screening-assist LLM on up to `max_candidates` papers
        without an existing AiSuggestion for this phase.

        Sequential under a per-batch concurrency cap so a 200-paper queue
        doesn't fan out 200 Bedrock calls at once. The classifier runs the
        cheap Bedrock model — same identity used elsewhere via
        `settings.bedrock_model_id`. Returns counts + any per-paper errors.
        """
        if phase not in (PHASE_ABSTRACT, PHASE_FULLTEXT):
            raise HTTPException(400, f"phase must be 'abstract' or 'fulltext'; got {phase!r}.")
        if max_candidates < 1 or max_candidates > 200:
            raise HTTPException(400, "max_candidates must be in [1, 200]")

        # Pull the project context + candidates that still need a suggestion.
        async with get_db_session() as session:
            review = await SrReviewRepository(session).get_project(sr_project_id)
            if review is None:
                raise HTTPException(404, "SR project not found")
            project_pico = json.loads(review.pico_json or "{}")
            inclusion_criteria = json.loads(review.inclusion_criteria_json or "[]")
            exclusion_criteria = json.loads(review.exclusion_criteria_json or "[]")
            repo = SrReviewRepository(session)
            relevant_statuses = (
                ["pending_abstract"]
                if phase == PHASE_ABSTRACT
                else ["included_after_abstract", "pending_fulltext"]
            )
            cands = await repo.list_candidates(
                sr_project_id, statuses=relevant_statuses, limit=max_candidates * 3
            )
            # Filter to those without a suggestion already.
            need: list[tuple[str, str, str | None]] = []
            for c in cands:
                if len(need) >= max_candidates:
                    break
                if await repo.get_ai_suggestion(c.id, phase) is not None:
                    continue
                pub = await session.get(Publication, c.publication_id)
                if pub is None:
                    continue
                need.append((c.id, pub.title, pub.abstract))

        if not need:
            return {"classified": 0, "skipped": 0, "errors": []}

        classified = 0
        errors: list[dict[str, str]] = []
        for candidate_id, title, abstract in need:
            try:
                prediction, meta = await sr_screening_assist.classify_abstract(
                    pico=project_pico,
                    inclusion_criteria=inclusion_criteria,
                    exclusion_criteria=exclusion_criteria,
                    paper_title=title,
                    paper_abstract=abstract,
                )
            except Exception as exc:
                logger.exception(
                    "sr_screening_assist failed for candidate=%s", candidate_id
                )
                errors.append({"candidate_id": candidate_id, "error": str(exc)[:200]})
                continue
            async with get_db_session() as session:
                await SrReviewRepository(session).upsert_ai_suggestion(
                    candidate_id=candidate_id,
                    phase=phase,
                    predicted_decision=prediction.predicted_decision,
                    predicted_reason_code=prediction.predicted_reason_code,
                    confidence=prediction.confidence,
                    rationale=prediction.rationale,
                    model_id=str(meta.get("model_id") or ""),
                )
            classified += 1

        return {"classified": classified, "skipped": 0, "errors": errors}

    @router.get("/projects/{sr_project_id}/prisma/diagram.svg")
    async def get_prisma_diagram(
        sr_project_id: str,
        user: SessionPayload = require_permission_scoped(
            Permission.PRISMA_READ, resource_param="sr_project_id"
        ),
    ) -> Response:
        """PRISMA flow diagram as SVG.

        Hand-rolled rather than rendered via sandbox_exec — the diagram has
        a fixed 4-row shape and no real layout decisions; SVG is text so
        the browser can render it inline + the export is small.
        """
        async with get_db_session() as session:
            review = await SrReviewRepository(session).get_project(sr_project_id)
            if review is None:
                raise HTTPException(404, "SR project not found")
            counts = await SrReviewRepository(session).prisma_counts(sr_project_id)
        svg = _render_prisma_svg(name=review.name, counts=counts)
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    return router


def _render_prisma_svg(*, name: str, counts: dict[str, Any]) -> str:
    """Build the standard 4-row PRISMA-2020 flow diagram as SVG.

    Boxes (top→bottom):
      1. Identification — `records_screened` (post-dedupe count we have)
      2. Screening      — `excluded_at_abstract` siphon
      3. Eligibility    — `full_text_assessed` + `excluded_at_fulltext` siphon
      4. Included       — `studies_included`

    No external deps; the SVG renders in any modern browser.
    """
    records_screened = int(counts.get("records_screened") or 0)
    excluded_abs = int(counts.get("excluded_at_abstract") or 0)
    full_text = int(counts.get("full_text_assessed") or 0)
    excluded_ft = int(counts.get("excluded_at_fulltext") or 0)
    included = int(counts.get("studies_included") or 0)
    abs_reasons = counts.get("excluded_reasons_abstract") or {}
    ft_reasons = counts.get("excluded_reasons_fulltext") or {}

    def _reason_lines(d: dict[str, int]) -> str:
        if not d:
            return ""
        items = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
        return "; ".join(f"{k} ({v})" for k, v in items[:6])

    def _esc(s: str) -> str:
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    abs_reason_text = _reason_lines(abs_reasons)
    ft_reason_text = _reason_lines(ft_reasons)

    # Layout constants
    W, H = 720, 720
    main_x, main_w = 220, 280
    side_x, side_w = 520, 180
    box_h = 100
    rows_y = [60, 220, 380, 560]  # top of each main box

    def _box(x: int, y: int, w: int, h: int, title: str, body: str, *, side: bool = False) -> str:
        fill = "#f3f6fb" if not side else "#fff7e6"
        stroke = "#1f3a66" if not side else "#a07300"
        return (
            f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" ry="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f'<text x="{x + 12}" y="{y + 22}" font-family="Helvetica,Arial,sans-serif" '
            f'font-size="12" font-weight="700" fill="{stroke}">{_esc(title)}</text>'
            f'<text x="{x + 12}" y="{y + 44}" font-family="Helvetica,Arial,sans-serif" '
            f'font-size="11" fill="#1d2026">{_esc(body)}</text></g>'
        )

    # Main column
    boxes = [
        _box(main_x, rows_y[0], main_w, box_h,
             "Identification (post-dedupe)",
             f"Records screened: {records_screened}"),
        _box(main_x, rows_y[1], main_w, box_h,
             "Screening (title/abstract)",
             f"Records after abstract: {records_screened - excluded_abs}"),
        _box(main_x, rows_y[2], main_w, box_h,
             "Eligibility (full text)",
             f"Full-text assessed: {full_text}"),
        _box(main_x, rows_y[3], main_w, box_h,
             "Included",
             f"Studies included: {included}"),
    ]

    # Side exclusion boxes
    side_boxes = [
        _box(side_x, rows_y[1], side_w, box_h,
             f"Excluded at abstract: {excluded_abs}",
             abs_reason_text or "(no reasons recorded)",
             side=True),
        _box(side_x, rows_y[2], side_w, box_h,
             f"Excluded at full text: {excluded_ft}",
             ft_reason_text or "(no reasons recorded)",
             side=True),
    ]

    # Arrows between main rows
    arrows = []
    for i in range(len(rows_y) - 1):
        y0 = rows_y[i] + box_h
        y1 = rows_y[i + 1]
        arrows.append(
            f'<line x1="{main_x + main_w // 2}" y1="{y0}" '
            f'x2="{main_x + main_w // 2}" y2="{y1}" '
            f'stroke="#1f3a66" stroke-width="1.5" marker-end="url(#arrow)"/>'
        )
    # Side arrows
    for y in (rows_y[1], rows_y[2]):
        arrows.append(
            f'<line x1="{main_x + main_w}" y1="{y + box_h // 2}" '
            f'x2="{side_x}" y2="{y + box_h // 2}" '
            f'stroke="#a07300" stroke-width="1.5" marker-end="url(#arrow-amber)"/>'
        )

    header = (
        f'<text x="{W // 2}" y="34" text-anchor="middle" '
        f'font-family="Helvetica,Arial,sans-serif" font-size="13" font-weight="700" '
        f'fill="#1f3a66">PRISMA flow — {_esc(name)}</text>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">'
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        'markerHeight="8" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 Z" fill="#1f3a66"/>'
        '</marker>'
        '<marker id="arrow-amber" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        'markerHeight="8" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 Z" fill="#a07300"/>'
        '</marker>'
        '</defs>'
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>'
        + header
        + "".join(boxes)
        + "".join(side_boxes)
        + "".join(arrows)
        + "</svg>"
    )
