"""Integration tests for /api/sr — RBAC gating + end-to-end screening flow.

Covers:
  • researcher (sr.create) can create + manage projects;
  • a Student cannot reach any SR surface (matrix locks this);
  • R1/R2 see their own queue blind to the other reviewer's decision;
  • disagreement raises a conflict that only the adjudicator can resolve;
  • PRISMA counts reflect the recorded decisions.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

_SECRET = "test-session-secret-must-be-long-enough-for-itsdangerous"
_COGNITO_ENV = {
    "COGNITO_REGION": "us-east-1",
    "COGNITO_USER_POOL_ID": "us-east-1_TESTPOOL",
    "COGNITO_CLIENT_ID": "test-client-id",
    "COGNITO_CLIENT_SECRET": "test-client-secret",
    "COGNITO_DOMAIN": "https://cra-test.auth.us-east-1.amazoncognito.com",
    "COGNITO_REDIRECT_URI": "http://localhost:8000/auth/callback",
    "SESSION_COOKIE_SECRET": _SECRET,
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_assistant.persistence.clinical.database import reset_clinical_engine
    from research_assistant.persistence.database import reset_engine

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("CLINICAL_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    for k, v in _COGNITO_ENV.items():
        monkeypatch.setenv(k, v)
    reset_engine()
    reset_clinical_engine()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from research_assistant.persistence.clinical.database import init_clinical_db
    from research_assistant.persistence.database import init_db
    from research_assistant.web.app import create_app

    app = create_app()
    await init_db()
    await init_clinical_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _cookie(sub: str, email: str) -> str:
    from research_assistant.auth.session import _serializer

    return _serializer(_SECRET).dumps(
        {"sub": sub, "email": email, "expires_at": int(time.time()) + 3600}
    )


async def _seed(sub: str, email: str, role: str) -> str:
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import User
    from research_assistant.persistence.user_repository import UserRepository

    async with get_db_session() as session:
        u = User(cognito_sub=sub, email=email)
        session.add(u)
        await session.flush()
        await UserRepository(session).grant_role(u.id, role)
        return u.id


async def _seed_candidates(project_id: str, n: int = 3) -> list[str]:
    """Bypass the network search and seed n candidate papers directly."""
    from research_assistant.persistence.database import get_db_session
    from research_assistant.persistence.models import Publication, SrCandidate

    ids: list[str] = []
    async with get_db_session() as session:
        for i in range(n):
            pid = f"pmid:test-{i + 1}"
            pub = Publication(
                id=pid,
                source="pubmed",
                title=f"Paper {i + 1}",
                abstract=f"Abstract {i + 1} body.",
                pmid=str(i + 1),
            )
            session.add(pub)
            await session.flush()
            cand = SrCandidate(
                sr_review_id=project_id,
                publication_id=pid,
                pmid=str(i + 1),
            )
            session.add(cand)
            await session.flush()
            ids.append(cand.id)
    return ids


def _login(c: AsyncClient, sub: str, email: str) -> None:
    c.cookies.set("cra_session", _cookie(sub, email))


# ── Matrix gating ────────────────────────────────────────────────────────


async def test_researcher_can_create_project(client: AsyncClient) -> None:
    await _seed("sub-r", "r@example.com", "researcher")
    _login(client, "sub-r", "r@example.com")
    resp = await client.post(
        "/api/sr/projects",
        json={"name": "My SR", "search_query": "test"},
    )
    assert resp.status_code == 201, resp.text


async def test_student_cannot_create_project(client: AsyncClient) -> None:
    await _seed("sub-s", "s@example.com", "student")
    _login(client, "sub-s", "s@example.com")
    resp = await client.post("/api/sr/projects", json={"name": "X"})
    assert resp.status_code == 403


async def test_admin_can_list_projects(client: AsyncClient) -> None:
    await _seed("sub-a", "a@example.com", "admin")
    _login(client, "sub-a", "a@example.com")
    listing = await client.get("/api/sr/projects")
    assert listing.status_code == 200


# ── Full screening flow ──────────────────────────────────────────────────


async def test_dual_review_with_conflict_and_adjudication(client: AsyncClient) -> None:
    """End-to-end happy path: project created, members assigned, candidates
    seeded, R1+R2 disagree on one paper, adjudicator resolves it, PRISMA
    counts reflect the outcomes."""

    # Seed users + login as researcher.
    await _seed("sub-r", "r@example.com", "researcher")
    await _seed("sub-r1", "r1@example.com", "researcher")
    await _seed("sub-r2", "r2@example.com", "researcher")
    await _seed("sub-adj", "adj@example.com", "researcher")

    _login(client, "sub-r", "r@example.com")
    proj = (
        await client.post(
            "/api/sr/projects",
            json={
                "name": "Dual review test",
                "search_query": "test",
                "inclusion_criteria": ["RCT"],
                "exclusion_criteria": ["preclinical"],
            },
        )
    ).json()
    pid = proj["id"]

    # Add reviewer slots.
    for email, role in [
        ("r1@example.com", "reviewer_1"),
        ("r2@example.com", "reviewer_2"),
        ("adj@example.com", "adjudicator"),
    ]:
        resp = await client.post(
            f"/api/sr/projects/{pid}/members",
            json={"user_email": email, "role": role},
        )
        assert resp.status_code == 201, resp.text

    candidate_ids = await _seed_candidates(pid, n=2)

    # R1 includes both.
    _login(client, "sub-r1", "r1@example.com")
    for cid in candidate_ids:
        r = await client.post(
            f"/api/sr/projects/{pid}/decisions",
            json={"candidate_id": cid, "phase": "abstract", "decision": "include"},
        )
        assert r.status_code == 201, r.text

    # R2 excludes the FIRST candidate (conflict), agrees-include on the SECOND.
    _login(client, "sub-r2", "r2@example.com")
    r = await client.post(
        f"/api/sr/projects/{pid}/decisions",
        json={
            "candidate_id": candidate_ids[0],
            "phase": "abstract",
            "decision": "exclude",
            "reason_code": "wrong_design",
        },
    )
    assert r.status_code == 201, r.text
    r = await client.post(
        f"/api/sr/projects/{pid}/decisions",
        json={"candidate_id": candidate_ids[1], "phase": "abstract", "decision": "include"},
    )
    assert r.status_code == 201, r.text

    # Adjudicator sees one conflict.
    _login(client, "sub-adj", "adj@example.com")
    conflicts = (await client.get(f"/api/sr/projects/{pid}/conflicts?phase=abstract")).json()
    assert len(conflicts) == 1
    assert conflicts[0]["candidate_id"] == candidate_ids[0]

    # Adjudicator resolves -> include.
    r = await client.post(
        f"/api/sr/projects/{pid}/decisions",
        json={"candidate_id": candidate_ids[0], "phase": "abstract", "decision": "include"},
    )
    assert r.status_code == 201, r.text

    # Both candidates should now be included_after_abstract → PRISMA reflects it.
    counts = (await client.get(f"/api/sr/projects/{pid}/prisma")).json()
    assert counts["records_screened"] == 2
    assert counts["excluded_at_abstract"] == 0
    assert counts["full_text_assessed"] == 2


async def test_reviewer_cannot_see_other_reviewers_decisions_in_queue(
    client: AsyncClient,
) -> None:
    """Blind dual review — R2's queue doesn't expose R1's decision text."""
    await _seed("sub-r", "r@example.com", "researcher")
    await _seed("sub-r1", "r1@example.com", "researcher")
    await _seed("sub-r2", "r2@example.com", "researcher")
    _login(client, "sub-r", "r@example.com")
    proj = (
        await client.post("/api/sr/projects", json={"name": "Blind", "search_query": "x"})
    ).json()
    pid = proj["id"]
    await client.post(
        f"/api/sr/projects/{pid}/members",
        json={"user_email": "r1@example.com", "role": "reviewer_1"},
    )
    await client.post(
        f"/api/sr/projects/{pid}/members",
        json={"user_email": "r2@example.com", "role": "reviewer_2"},
    )
    cids = await _seed_candidates(pid, n=1)

    # R1 decides.
    _login(client, "sub-r1", "r1@example.com")
    await client.post(
        f"/api/sr/projects/{pid}/decisions",
        json={"candidate_id": cids[0], "phase": "abstract", "decision": "include"},
    )

    # R2's queue surface for the same candidate should not mention R1's vote.
    _login(client, "sub-r2", "r2@example.com")
    queue = (await client.get(f"/api/sr/projects/{pid}/queue?phase=abstract")).json()
    assert queue is not None
    assert queue["id"] == cids[0]
    # The response shape (CandidateOut) intentionally has no `decisions`
    # field — verify by checking the keys, not by asserting absence.
    assert set(queue.keys()) >= {"id", "title", "abstract", "current_status"}
    assert "decisions" not in queue
