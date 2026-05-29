"""Systematic-review screening repository.

CRUD + agreement-rule recomputation for `SrReview`, `SrReviewMembership`,
`SrCandidate`, `ScreeningDecision`, and `AiSuggestion`. All methods take
an `AsyncSession` so callers control the transaction boundary.

The agreement rules (D1) live in `_recompute_status` and are referenced
by `record_decision` so a decision write atomically updates the candidate's
`current_status`. Two-reviewer agreement collapses to included/excluded
immediately; disagreement raises pending_adjudication; the adjudicator's
decision overrides and locks the phase.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .library.repository import publication_id as compute_publication_id
from .library.repository import upsert_publication
from .models import (
    AiSuggestion,
    Publication,
    ScreeningDecision,
    SrCandidate,
    SrReview,
    SrReviewMembership,
)

logger = logging.getLogger(__name__)


# ── Status constants — keep aligned with the SrCandidate.current_status doc ─

STATUS_PENDING_ABSTRACT = "pending_abstract"
STATUS_PENDING_ADJ_ABSTRACT = "pending_adjudication_abstract"
STATUS_INCLUDED_AFTER_ABSTRACT = "included_after_abstract"
STATUS_EXCLUDED_AT_ABSTRACT = "excluded_at_abstract"
STATUS_PENDING_FULLTEXT = "pending_fulltext"
STATUS_PENDING_ADJ_FULLTEXT = "pending_adjudication_fulltext"
STATUS_INCLUDED_AFTER_FULLTEXT = "included_after_fulltext"
STATUS_EXCLUDED_AT_FULLTEXT = "excluded_at_fulltext"

PHASE_ABSTRACT = "abstract"
PHASE_FULLTEXT = "fulltext"

ROLE_R1 = "reviewer_1"
ROLE_R2 = "reviewer_2"
ROLE_ADJ = "adjudicator"


class SrError(Exception):
    """Invalid screening operation (e.g. unknown project, bad role)."""


class SrReviewRepository:
    """Async data access for the SR-screening tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Projects ─────────────────────────────────────────────────────────

    async def create_project(
        self,
        *,
        name: str,
        description: str | None,
        pico: Mapping[str, Any] | None,
        search_query: str,
        sources: list[str],
        inclusion_criteria: list[str],
        exclusion_criteria: list[str],
        created_by: str | None,
    ) -> SrReview:
        review = SrReview(
            name=name[:200],
            description=description,
            pico_json=json.dumps(pico or {}),
            search_query=search_query,
            sources_json=json.dumps(sources),
            inclusion_criteria_json=json.dumps(inclusion_criteria),
            exclusion_criteria_json=json.dumps(exclusion_criteria),
            created_by=created_by,
        )
        self._s.add(review)
        await self._s.flush()
        return review

    async def get_project(self, project_id: str) -> SrReview | None:
        return await self._s.get(SrReview, project_id)

    async def list_projects_for_user(self, user_id: str) -> list[SrReview]:
        """Projects where the user is either the creator OR holds a membership.

        Visibility on the UI mirrors RBAC `sr.read` — the creator implicitly
        sees their projects (they granted themselves any role they like) and
        anyone with a R1/R2/adjudicator slot sees the projects they screen.
        """
        stmt = (
            select(SrReview)
            .where(
                or_(
                    SrReview.created_by == user_id,
                    SrReview.id.in_(
                        select(SrReviewMembership.sr_review_id).where(
                            SrReviewMembership.user_id == user_id
                        )
                    ),
                )
            )
            .order_by(SrReview.updated_at.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def update_project(self, project_id: str, **fields: object) -> SrReview | None:
        review = await self._s.get(SrReview, project_id)
        if review is None:
            return None
        SETTABLE = {
            "name",
            "description",
            "status",
            "search_query",
        }
        SETTABLE_JSON = {
            "pico": "pico_json",
            "sources": "sources_json",
            "inclusion_criteria": "inclusion_criteria_json",
            "exclusion_criteria": "exclusion_criteria_json",
        }
        for key, value in fields.items():
            if key in SETTABLE and value is not None:
                setattr(review, key, value)
            elif key in SETTABLE_JSON and value is not None:
                setattr(review, SETTABLE_JSON[key], json.dumps(value))
        await self._s.flush()
        return review

    async def delete_project(self, project_id: str) -> bool:
        review = await self._s.get(SrReview, project_id)
        if review is None:
            return False
        await self._s.delete(review)
        await self._s.flush()
        return True

    # ── Memberships ──────────────────────────────────────────────────────

    async def add_member(
        self,
        project_id: str,
        *,
        user_id: str,
        role: str,
        granted_by: str | None = None,
    ) -> SrReviewMembership:
        if role not in (ROLE_R1, ROLE_R2, ROLE_ADJ):
            raise SrError(
                f"Unknown SR role {role!r}; expected reviewer_1 | reviewer_2 | adjudicator."
            )
        # Idempotent — the unique constraint would block dupes, but a clean
        # no-op is friendlier than an IntegrityError surfacing.
        existing = (
            await self._s.execute(
                select(SrReviewMembership).where(
                    SrReviewMembership.sr_review_id == project_id,
                    SrReviewMembership.user_id == user_id,
                    SrReviewMembership.role == role,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        mem = SrReviewMembership(
            sr_review_id=project_id,
            user_id=user_id,
            role=role,
            granted_by=granted_by,
        )
        self._s.add(mem)
        await self._s.flush()
        return mem

    async def list_members(self, project_id: str) -> list[SrReviewMembership]:
        return list(
            (
                await self._s.execute(
                    select(SrReviewMembership)
                    .where(SrReviewMembership.sr_review_id == project_id)
                    .order_by(SrReviewMembership.granted_at)
                )
            )
            .scalars()
            .all()
        )

    async def remove_member(self, membership_id: str) -> bool:
        row = await self._s.get(SrReviewMembership, membership_id)
        if row is None:
            return False
        await self._s.delete(row)
        await self._s.flush()
        return True

    async def role_for_user_on_project(
        self, project_id: str, user_id: str
    ) -> list[str]:
        """All membership roles a user holds on a project (a user CAN hold
        more than one — e.g. R1 on the abstract phase and adjudicator on
        the full-text phase — though the UI normally enforces one at a time)."""
        rows = (
            await self._s.execute(
                select(SrReviewMembership.role).where(
                    SrReviewMembership.sr_review_id == project_id,
                    SrReviewMembership.user_id == user_id,
                )
            )
        ).scalars().all()
        return list(rows)

    # ── Candidate ingest ─────────────────────────────────────────────────

    async def ingest_candidates(
        self,
        project_id: str,
        studies: Iterable[Mapping[str, Any]],
    ) -> dict[str, int]:
        """Snapshot a search result set into the project as candidates.

        Each study is also write-through-cached into the shared `Publication`
        store (R0) so the abstract is available to the screening UI.
        Dedupe happens twice: across the input studies (by computed
        publication_id), and against the project's existing SrCandidate
        rows (the unique constraint).
        """
        identified = 0
        duplicates_within_query = 0
        already_in_project = 0
        added = 0
        seen_ids: set[str] = set()
        existing_in_project = {
            row
            for row in (
                await self._s.execute(
                    select(SrCandidate.publication_id).where(
                        SrCandidate.sr_review_id == project_id
                    )
                )
            )
            .scalars()
            .all()
        }
        for study in studies:
            identified += 1
            pid = compute_publication_id(study)
            if pid in seen_ids:
                duplicates_within_query += 1
                continue
            seen_ids.add(pid)
            await upsert_publication(self._s, study)
            if pid in existing_in_project:
                already_in_project += 1
                continue
            cand = SrCandidate(
                sr_review_id=project_id,
                publication_id=pid,
                pmid=str(study.get("pmid")) if study.get("pmid") else None,
                source_origin=str(study.get("source")) if study.get("source") else None,
            )
            self._s.add(cand)
            added += 1
        await self._s.flush()
        return {
            "records_identified": identified,
            "duplicates_removed": duplicates_within_query,
            "already_present": already_in_project,
            "added": added,
        }

    async def list_candidates(
        self,
        project_id: str,
        *,
        statuses: list[str] | None = None,
        limit: int | None = None,
    ) -> list[SrCandidate]:
        stmt = select(SrCandidate).where(SrCandidate.sr_review_id == project_id)
        if statuses:
            stmt = stmt.where(SrCandidate.current_status.in_(statuses))
        stmt = stmt.order_by(SrCandidate.added_at)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._s.execute(stmt)).scalars().all())

    async def get_candidate_with_publication(
        self, candidate_id: str
    ) -> tuple[SrCandidate, Publication] | None:
        cand = await self._s.get(SrCandidate, candidate_id)
        if cand is None:
            return None
        pub = await self._s.get(Publication, cand.publication_id)
        if pub is None:
            return None
        return cand, pub

    # ── Screening queue ──────────────────────────────────────────────────

    async def next_for_reviewer(
        self,
        project_id: str,
        *,
        user_id: str,
        phase: str,
    ) -> SrCandidate | None:
        """Pop the next candidate this reviewer hasn't decided on yet.

        Filters by the candidate statuses relevant to the requested phase
        (`pending_abstract` for abstract phase; `included_after_abstract`
        + `pending_fulltext` for full-text phase). Excludes candidates the
        caller already wrote a `ScreeningDecision` row for in this phase.
        Adjudicators see `pending_adjudication_*` candidates as well.
        """
        relevant_statuses = _statuses_for_phase(phase)
        # Adjudicators ALSO see pending-adjudication rows in this phase.
        adj_statuses: list[str] = []
        if phase == PHASE_ABSTRACT:
            adj_statuses = [STATUS_PENDING_ADJ_ABSTRACT]
        elif phase == PHASE_FULLTEXT:
            adj_statuses = [STATUS_PENDING_ADJ_FULLTEXT]
        adj_membership = await self.role_for_user_on_project(project_id, user_id)
        if ROLE_ADJ in adj_membership:
            relevant_statuses = list(relevant_statuses) + adj_statuses

        already_decided = select(ScreeningDecision.sr_candidate_id).where(
            ScreeningDecision.reviewer_user_id == user_id,
            ScreeningDecision.phase == phase,
        )
        stmt = (
            select(SrCandidate)
            .where(
                SrCandidate.sr_review_id == project_id,
                SrCandidate.current_status.in_(relevant_statuses),
                ~SrCandidate.id.in_(already_decided),
            )
            .order_by(SrCandidate.added_at)
            .limit(1)
        )
        return (await self._s.execute(stmt)).scalars().first()

    # ── Decisions + agreement rules ──────────────────────────────────────

    async def record_decision(
        self,
        *,
        candidate_id: str,
        reviewer_user_id: str,
        role: str,
        phase: str,
        decision: str,
        reason_code: str | None,
        notes: str | None,
        ai_suggested: bool = False,
    ) -> tuple[ScreeningDecision, SrCandidate]:
        """Upsert a screening decision and recompute the candidate's status.

        Returns the persisted decision + the refreshed candidate. Raises
        `SrError` if the candidate is unknown, the decision shape is
        invalid, or the role/phase combination doesn't make sense (e.g.
        someone writes a phase-2 decision before phase 1 closed).
        """
        if role not in (ROLE_R1, ROLE_R2, ROLE_ADJ):
            raise SrError(f"Unknown reviewer role {role!r}.")
        if phase not in (PHASE_ABSTRACT, PHASE_FULLTEXT):
            raise SrError(f"Unknown phase {phase!r}.")
        if decision not in ("include", "exclude", "maybe"):
            raise SrError(f"Decision must be include | exclude | maybe (got {decision!r}).")

        cand = await self._s.get(SrCandidate, candidate_id)
        if cand is None:
            raise SrError(f"Candidate {candidate_id!r} not found.")
        if phase == PHASE_FULLTEXT and cand.current_status not in (
            STATUS_INCLUDED_AFTER_ABSTRACT,
            STATUS_PENDING_FULLTEXT,
            STATUS_PENDING_ADJ_FULLTEXT,
            STATUS_INCLUDED_AFTER_FULLTEXT,
            STATUS_EXCLUDED_AT_FULLTEXT,
        ):
            raise SrError(
                f"Cannot record full-text decision while candidate is "
                f"{cand.current_status!r} — finish the abstract phase first."
            )

        existing = (
            await self._s.execute(
                select(ScreeningDecision).where(
                    ScreeningDecision.sr_candidate_id == candidate_id,
                    ScreeningDecision.reviewer_user_id == reviewer_user_id,
                    ScreeningDecision.phase == phase,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ScreeningDecision(
                sr_candidate_id=candidate_id,
                reviewer_user_id=reviewer_user_id,
                role=role,
                phase=phase,
                decision=decision,
                reason_code=reason_code,
                notes=notes,
                ai_suggested=ai_suggested,
            )
            self._s.add(existing)
        else:
            existing.role = role
            existing.decision = decision
            existing.reason_code = reason_code
            existing.notes = notes
            existing.ai_suggested = ai_suggested
        await self._s.flush()
        await self._recompute_status(cand, phase=phase)
        return existing, cand

    async def _recompute_status(
        self,
        candidate: SrCandidate,
        *,
        phase: str,
    ) -> None:
        """Apply the agreement rules and update `current_status` in place.

        Abstract phase:
          • R1+R2 both include      → included_after_abstract
          • R1+R2 both exclude       → excluded_at_abstract (+reason from R1)
          • disagreement or maybe(s) → pending_adjudication_abstract
          • only one reviewer so far → pending_abstract (no transition)
          • adjudicator decision     → terminal include / exclude

        Full-text phase: same shape, terminal statuses `included_after_fulltext`
        / `excluded_at_fulltext`. The candidate must already be
        `included_after_abstract` to start phase 2 (enforced in record_decision).
        """
        decisions = (
            (
                await self._s.execute(
                    select(ScreeningDecision).where(
                        ScreeningDecision.sr_candidate_id == candidate.id,
                        ScreeningDecision.phase == phase,
                    )
                )
            )
            .scalars()
            .all()
        )
        by_role: dict[str, ScreeningDecision] = {d.role: d for d in decisions}
        adj = by_role.get(ROLE_ADJ)
        if phase == PHASE_ABSTRACT:
            included = STATUS_INCLUDED_AFTER_ABSTRACT
            excluded = STATUS_EXCLUDED_AT_ABSTRACT
            pending_adj = STATUS_PENDING_ADJ_ABSTRACT
            pending_default = STATUS_PENDING_ABSTRACT
        else:
            included = STATUS_INCLUDED_AFTER_FULLTEXT
            excluded = STATUS_EXCLUDED_AT_FULLTEXT
            pending_adj = STATUS_PENDING_ADJ_FULLTEXT
            pending_default = STATUS_PENDING_FULLTEXT

        if adj is not None:
            if adj.decision == "include":
                candidate.current_status = included
                candidate.excluded_reason_code = None
                candidate.excluded_at_phase = None
            elif adj.decision == "exclude":
                candidate.current_status = excluded
                candidate.excluded_reason_code = adj.reason_code
                candidate.excluded_at_phase = phase
            else:
                candidate.current_status = pending_adj
            await self._s.flush()
            return

        r1 = by_role.get(ROLE_R1)
        r2 = by_role.get(ROLE_R2)
        if r1 is None or r2 is None:
            # Phase 2 candidate must be at least pending_fulltext before the
            # first review lands — flip from included_after_abstract on the
            # first phase-2 decision arrival.
            if (
                phase == PHASE_FULLTEXT
                and candidate.current_status == STATUS_INCLUDED_AFTER_ABSTRACT
            ):
                candidate.current_status = STATUS_PENDING_FULLTEXT
            elif candidate.current_status not in (
                STATUS_INCLUDED_AFTER_ABSTRACT,
                STATUS_EXCLUDED_AT_ABSTRACT,
                STATUS_INCLUDED_AFTER_FULLTEXT,
                STATUS_EXCLUDED_AT_FULLTEXT,
            ):
                candidate.current_status = pending_default
            await self._s.flush()
            return

        if r1.decision == "include" and r2.decision == "include":
            candidate.current_status = included
            candidate.excluded_reason_code = None
            candidate.excluded_at_phase = None
        elif r1.decision == "exclude" and r2.decision == "exclude":
            candidate.current_status = excluded
            candidate.excluded_reason_code = r1.reason_code or r2.reason_code
            candidate.excluded_at_phase = phase
        else:
            candidate.current_status = pending_adj
        await self._s.flush()

    async def list_conflicts(
        self, project_id: str, *, phase: str
    ) -> list[tuple[SrCandidate, list[ScreeningDecision]]]:
        """Candidates needing adjudicator review in the requested phase."""
        if phase == PHASE_ABSTRACT:
            target_status = STATUS_PENDING_ADJ_ABSTRACT
        elif phase == PHASE_FULLTEXT:
            target_status = STATUS_PENDING_ADJ_FULLTEXT
        else:
            raise SrError(f"Unknown phase {phase!r}.")

        candidates = (
            (
                await self._s.execute(
                    select(SrCandidate).where(
                        SrCandidate.sr_review_id == project_id,
                        SrCandidate.current_status == target_status,
                    )
                )
            )
            .scalars()
            .all()
        )
        out: list[tuple[SrCandidate, list[ScreeningDecision]]] = []
        for cand in candidates:
            decisions = (
                (
                    await self._s.execute(
                        select(ScreeningDecision).where(
                            ScreeningDecision.sr_candidate_id == cand.id,
                            ScreeningDecision.phase == phase,
                        )
                    )
                )
                .scalars()
                .all()
            )
            out.append((cand, list(decisions)))
        return out

    # ── PRISMA counts ────────────────────────────────────────────────────

    async def prisma_counts(self, project_id: str) -> dict[str, Any]:
        """The 4-box PRISMA shape, computed from candidate statuses + reasons.

        `records_identified` and `duplicates_removed` aren't recoverable
        from candidate rows alone (the dedupe happened at ingest time); we
        derive `records_identified` as the union of the project's
        candidates (post-dedupe) plus a separately tracked dupes count.
        For now we report only the candidate-derived numbers; the ingest
        endpoint returns dupes for the caller to display.
        """
        candidates = await self.list_candidates(project_id)
        total = len(candidates)
        by_status: dict[str, int] = {}
        excluded_reasons_abstract: dict[str, int] = {}
        excluded_reasons_fulltext: dict[str, int] = {}
        for c in candidates:
            by_status[c.current_status] = by_status.get(c.current_status, 0) + 1
            if c.current_status == STATUS_EXCLUDED_AT_ABSTRACT and c.excluded_reason_code:
                excluded_reasons_abstract[c.excluded_reason_code] = (
                    excluded_reasons_abstract.get(c.excluded_reason_code, 0) + 1
                )
            if c.current_status == STATUS_EXCLUDED_AT_FULLTEXT and c.excluded_reason_code:
                excluded_reasons_fulltext[c.excluded_reason_code] = (
                    excluded_reasons_fulltext.get(c.excluded_reason_code, 0) + 1
                )

        records_screened = total
        excluded_at_abstract = by_status.get(STATUS_EXCLUDED_AT_ABSTRACT, 0)
        full_text_assessed = (
            by_status.get(STATUS_INCLUDED_AFTER_ABSTRACT, 0)
            + by_status.get(STATUS_PENDING_FULLTEXT, 0)
            + by_status.get(STATUS_PENDING_ADJ_FULLTEXT, 0)
            + by_status.get(STATUS_INCLUDED_AFTER_FULLTEXT, 0)
            + by_status.get(STATUS_EXCLUDED_AT_FULLTEXT, 0)
        )
        excluded_at_fulltext = by_status.get(STATUS_EXCLUDED_AT_FULLTEXT, 0)
        studies_included = by_status.get(STATUS_INCLUDED_AFTER_FULLTEXT, 0)
        return {
            "records_screened": records_screened,
            "excluded_at_abstract": excluded_at_abstract,
            "excluded_reasons_abstract": excluded_reasons_abstract,
            "full_text_assessed": full_text_assessed,
            "excluded_at_fulltext": excluded_at_fulltext,
            "excluded_reasons_fulltext": excluded_reasons_fulltext,
            "studies_included": studies_included,
            "by_status": by_status,
        }

    async def progress(
        self, project_id: str, *, phase: str
    ) -> dict[str, Any]:
        """Per-reviewer screened count + agreement rate + remaining queue size.

        Powers the screening UI's progress bar; cheap enough to refresh on
        every queue advance.
        """
        candidates = await self.list_candidates(project_id)
        total = len(candidates)
        relevant_statuses = set(_statuses_for_phase(phase))
        remaining = sum(1 for c in candidates if c.current_status in relevant_statuses)
        decisions = (
            (
                await self._s.execute(
                    select(ScreeningDecision).where(
                        ScreeningDecision.sr_candidate_id.in_(
                            [c.id for c in candidates] or [""]
                        ),
                        ScreeningDecision.phase == phase,
                    )
                )
            )
            .scalars()
            .all()
        )
        by_reviewer: dict[str, int] = {}
        for d in decisions:
            by_reviewer[d.reviewer_user_id] = by_reviewer.get(d.reviewer_user_id, 0) + 1
        return {
            "total_candidates": total,
            "remaining": remaining,
            "decisions_by_reviewer": by_reviewer,
            "phase": phase,
        }

    # ── AI suggestions ───────────────────────────────────────────────────

    async def upsert_ai_suggestion(
        self,
        *,
        candidate_id: str,
        phase: str,
        predicted_decision: str,
        predicted_reason_code: str | None,
        confidence: float,
        rationale: str | None,
        model_id: str | None,
    ) -> AiSuggestion:
        existing = (
            await self._s.execute(
                select(AiSuggestion).where(
                    AiSuggestion.sr_candidate_id == candidate_id,
                    AiSuggestion.phase == phase,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = AiSuggestion(
                sr_candidate_id=candidate_id,
                phase=phase,
                predicted_decision=predicted_decision,
                predicted_reason_code=predicted_reason_code,
                confidence=confidence,
                rationale=rationale,
                model_id=model_id,
            )
            self._s.add(existing)
        else:
            existing.predicted_decision = predicted_decision
            existing.predicted_reason_code = predicted_reason_code
            existing.confidence = confidence
            existing.rationale = rationale
            existing.model_id = model_id
        await self._s.flush()
        return existing

    async def get_ai_suggestion(
        self, candidate_id: str, phase: str
    ) -> AiSuggestion | None:
        return (
            await self._s.execute(
                select(AiSuggestion).where(
                    AiSuggestion.sr_candidate_id == candidate_id,
                    AiSuggestion.phase == phase,
                )
            )
        ).scalar_one_or_none()


def _statuses_for_phase(phase: str) -> list[str]:
    if phase == PHASE_ABSTRACT:
        return [STATUS_PENDING_ABSTRACT]
    if phase == PHASE_FULLTEXT:
        return [STATUS_INCLUDED_AFTER_ABSTRACT, STATUS_PENDING_FULLTEXT]
    raise SrError(f"Unknown phase {phase!r}.")


__all__ = [
    "PHASE_ABSTRACT",
    "PHASE_FULLTEXT",
    "ROLE_ADJ",
    "ROLE_R1",
    "ROLE_R2",
    "STATUS_EXCLUDED_AT_ABSTRACT",
    "STATUS_EXCLUDED_AT_FULLTEXT",
    "STATUS_INCLUDED_AFTER_ABSTRACT",
    "STATUS_INCLUDED_AFTER_FULLTEXT",
    "STATUS_PENDING_ABSTRACT",
    "STATUS_PENDING_ADJ_ABSTRACT",
    "STATUS_PENDING_ADJ_FULLTEXT",
    "STATUS_PENDING_FULLTEXT",
    "SrError",
    "SrReviewRepository",
]
