"""SR-screening repository — project + membership + decision agreement rules.

The agreement rules are the heart of the dual-review model. These tests
lock the matrix:

  R1 + R2 both include → included_after_abstract
  R1 + R2 both exclude → excluded_at_abstract (+reason from R1)
  R1 vs R2 disagree    → pending_adjudication_abstract
  one reviewer only    → pending_abstract (no transition)
  adjudicator decides  → terminal include / exclude

Same shape applies to the full-text phase.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_assistant.persistence.models import Publication, SrCandidate, SrReview, User
from research_assistant.persistence.sr_repository import (
    PHASE_ABSTRACT,
    PHASE_FULLTEXT,
    ROLE_ADJ,
    ROLE_R1,
    ROLE_R2,
    STATUS_EXCLUDED_AT_ABSTRACT,
    STATUS_EXCLUDED_AT_FULLTEXT,
    STATUS_INCLUDED_AFTER_ABSTRACT,
    STATUS_PENDING_ABSTRACT,
    STATUS_PENDING_ADJ_ABSTRACT,
    STATUS_PENDING_FULLTEXT,
    SrError,
    SrReviewRepository,
)


async def _seed_project_with_candidate(
    db: AsyncSession,
) -> tuple[SrReview, SrCandidate, list[User]]:
    """Create one project + 3 users + one candidate paper.

    Returns (project, candidate, [creator, r1, r2, adjudicator]).
    """
    creator = User(cognito_sub="creator", email="creator@example.com")
    r1 = User(cognito_sub="r1", email="r1@example.com")
    r2 = User(cognito_sub="r2", email="r2@example.com")
    adj = User(cognito_sub="adj", email="adj@example.com")
    db.add_all([creator, r1, r2, adj])
    await db.flush()

    repo = SrReviewRepository(db)
    project = await repo.create_project(
        name="Test SR",
        description=None,
        pico={"population": "adults"},
        search_query="test",
        sources=["pubmed"],
        inclusion_criteria=["RCT"],
        exclusion_criteria=["preclinical"],
        created_by=creator.id,
    )
    await repo.add_member(project.id, user_id=r1.id, role=ROLE_R1, granted_by=creator.id)
    await repo.add_member(project.id, user_id=r2.id, role=ROLE_R2, granted_by=creator.id)
    await repo.add_member(project.id, user_id=adj.id, role=ROLE_ADJ, granted_by=creator.id)

    pub = Publication(id="pmid:1", source="pubmed", title="Some trial")
    db.add(pub)
    await db.flush()
    cand = SrCandidate(sr_review_id=project.id, publication_id=pub.id, pmid="1")
    db.add(cand)
    await db.flush()
    return project, cand, [creator, r1, r2, adj]


async def test_both_include_promotes_to_included_after_abstract(
    db_session: AsyncSession,
) -> None:
    project, cand, users = await _seed_project_with_candidate(db_session)
    _, r1, r2, _ = users
    repo = SrReviewRepository(db_session)

    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r1.id, role=ROLE_R1,
        phase=PHASE_ABSTRACT, decision="include", reason_code=None, notes=None,
    )
    # After R1 alone, no transition yet.
    cand_refresh = await db_session.get(SrCandidate, cand.id)
    assert cand_refresh.current_status == STATUS_PENDING_ABSTRACT

    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r2.id, role=ROLE_R2,
        phase=PHASE_ABSTRACT, decision="include", reason_code=None, notes=None,
    )
    cand_refresh = await db_session.get(SrCandidate, cand.id)
    assert cand_refresh.current_status == STATUS_INCLUDED_AFTER_ABSTRACT


async def test_both_exclude_promotes_to_excluded_at_abstract(
    db_session: AsyncSession,
) -> None:
    project, cand, users = await _seed_project_with_candidate(db_session)
    _, r1, r2, _ = users
    repo = SrReviewRepository(db_session)

    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r1.id, role=ROLE_R1,
        phase=PHASE_ABSTRACT, decision="exclude", reason_code="wrong_design", notes=None,
    )
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r2.id, role=ROLE_R2,
        phase=PHASE_ABSTRACT, decision="exclude", reason_code="wrong_design", notes=None,
    )
    cand_refresh = await db_session.get(SrCandidate, cand.id)
    assert cand_refresh.current_status == STATUS_EXCLUDED_AT_ABSTRACT
    assert cand_refresh.excluded_reason_code == "wrong_design"


async def test_disagreement_raises_pending_adjudication(
    db_session: AsyncSession,
) -> None:
    project, cand, users = await _seed_project_with_candidate(db_session)
    _, r1, r2, _ = users
    repo = SrReviewRepository(db_session)

    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r1.id, role=ROLE_R1,
        phase=PHASE_ABSTRACT, decision="include", reason_code=None, notes=None,
    )
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r2.id, role=ROLE_R2,
        phase=PHASE_ABSTRACT, decision="exclude", reason_code="population_mismatch", notes=None,
    )
    cand_refresh = await db_session.get(SrCandidate, cand.id)
    assert cand_refresh.current_status == STATUS_PENDING_ADJ_ABSTRACT


async def test_adjudicator_decision_locks_phase(db_session: AsyncSession) -> None:
    """When R1/R2 disagree and adjudicator decides, the adjudicator's vote
    overrides — no further R1/R2 churn matters."""
    project, cand, users = await _seed_project_with_candidate(db_session)
    _, r1, r2, adj = users
    repo = SrReviewRepository(db_session)

    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r1.id, role=ROLE_R1,
        phase=PHASE_ABSTRACT, decision="include", reason_code=None, notes=None,
    )
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r2.id, role=ROLE_R2,
        phase=PHASE_ABSTRACT, decision="exclude", reason_code="wrong_design", notes=None,
    )
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=adj.id, role=ROLE_ADJ,
        phase=PHASE_ABSTRACT, decision="include", reason_code=None, notes="judgment call",
    )
    cand_refresh = await db_session.get(SrCandidate, cand.id)
    assert cand_refresh.current_status == STATUS_INCLUDED_AFTER_ABSTRACT


async def test_fulltext_phase_blocked_before_abstract_passes(
    db_session: AsyncSession,
) -> None:
    """A reviewer can't write a full-text decision before the abstract
    phase moves the candidate to included_after_abstract — guards against
    out-of-order screening."""
    project, cand, users = await _seed_project_with_candidate(db_session)
    _, r1, _, _ = users
    repo = SrReviewRepository(db_session)
    with pytest.raises(SrError, match="finish the abstract phase first"):
        await repo.record_decision(
            candidate_id=cand.id, reviewer_user_id=r1.id, role=ROLE_R1,
            phase=PHASE_FULLTEXT, decision="include", reason_code=None, notes=None,
        )


async def test_fulltext_phase_passes_after_abstract_inclusion(
    db_session: AsyncSession,
) -> None:
    project, cand, users = await _seed_project_with_candidate(db_session)
    _, r1, r2, _ = users
    repo = SrReviewRepository(db_session)

    # First promote through abstract phase.
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r1.id, role=ROLE_R1,
        phase=PHASE_ABSTRACT, decision="include", reason_code=None, notes=None,
    )
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r2.id, role=ROLE_R2,
        phase=PHASE_ABSTRACT, decision="include", reason_code=None, notes=None,
    )
    # Now full-text decisions are accepted; first one transitions to pending.
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r1.id, role=ROLE_R1,
        phase=PHASE_FULLTEXT, decision="exclude", reason_code="outcome_mismatch", notes=None,
    )
    cand_refresh = await db_session.get(SrCandidate, cand.id)
    assert cand_refresh.current_status == STATUS_PENDING_FULLTEXT
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r2.id, role=ROLE_R2,
        phase=PHASE_FULLTEXT, decision="exclude", reason_code="outcome_mismatch", notes=None,
    )
    cand_refresh = await db_session.get(SrCandidate, cand.id)
    assert cand_refresh.current_status == STATUS_EXCLUDED_AT_FULLTEXT


async def test_queue_skips_already_decided_for_this_reviewer(
    db_session: AsyncSession,
) -> None:
    """A reviewer's queue must NOT return papers they already decided on."""
    project, cand, users = await _seed_project_with_candidate(db_session)
    _, r1, _, _ = users
    repo = SrReviewRepository(db_session)

    # R1 decides on the only candidate.
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r1.id, role=ROLE_R1,
        phase=PHASE_ABSTRACT, decision="include", reason_code=None, notes=None,
    )
    next_for_r1 = await repo.next_for_reviewer(project.id, user_id=r1.id, phase=PHASE_ABSTRACT)
    assert next_for_r1 is None, "queue should be empty for r1 — they already decided"


async def test_prisma_counts_match_seeded_decisions(db_session: AsyncSession) -> None:
    project, cand, users = await _seed_project_with_candidate(db_session)
    _, r1, r2, _ = users
    repo = SrReviewRepository(db_session)

    # Both exclude the only candidate.
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r1.id, role=ROLE_R1,
        phase=PHASE_ABSTRACT, decision="exclude", reason_code="wrong_design", notes=None,
    )
    await repo.record_decision(
        candidate_id=cand.id, reviewer_user_id=r2.id, role=ROLE_R2,
        phase=PHASE_ABSTRACT, decision="exclude", reason_code="wrong_design", notes=None,
    )
    counts = await repo.prisma_counts(project.id)
    assert counts["records_screened"] == 1
    assert counts["excluded_at_abstract"] == 1
    assert counts["full_text_assessed"] == 0
    assert counts["studies_included"] == 0
    assert counts["excluded_reasons_abstract"] == {"wrong_design": 1}


async def test_add_member_rejects_unknown_role(db_session: AsyncSession) -> None:
    project, _, users = await _seed_project_with_candidate(db_session)
    repo = SrReviewRepository(db_session)
    with pytest.raises(SrError, match="Unknown SR role"):
        await repo.add_member(project.id, user_id=users[0].id, role="ghost")
