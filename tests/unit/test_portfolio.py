"""Portfolio rollup tests (P1 #9)."""

from __future__ import annotations

from research_assistant.auth.rbac import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
)


def test_portfolio_read_org_admin_only() -> None:
    """The new portfolio.read_org permission is admin-only — every other
    role gets the per-user portfolio routes via the inherited
    auth dependency, but only admin sees the institutional rollup."""
    assert Permission.PORTFOLIO_READ_ORG in ROLE_PERMISSIONS[Role.ADMIN]
    for role in (
        Role.RESEARCHER,
        Role.STUDENT,
        Role.AUDITOR,
        Role.STUDY_DESIGNER,
        Role.PRINCIPAL_INVESTIGATOR,
        Role.COORDINATOR,
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.REVIEWER_1,
        Role.REVIEWER_2,
        Role.ADJUDICATOR,
    ):
        assert Permission.PORTFOLIO_READ_ORG not in ROLE_PERMISSIONS[role], (
            f"{role.value} should not have portfolio.read_org"
        )


def test_portfolio_cost_dtos_round_trip() -> None:
    """CostRollupOut + OrgCostRollupOut validate against shaped data
    (P2 #6 budget rollup)."""
    from research_assistant.web.portfolio import (
        CostRollupOut,
        OrgCostRollupOut,
    )

    cost = CostRollupOut(
        usd_total=1.23,
        usd_this_month=0.42,
        input_tokens=15000,
        output_tokens=8000,
        by_workflow={"meta_analysis": 0.8, "nma": 0.43},
        by_model_family={"haiku": 0.1, "sonnet": 1.13},
        n_turns=12,
    )
    assert cost.n_turns == 12
    assert cost.by_workflow["meta_analysis"] == 0.8

    org = OrgCostRollupOut(
        org_totals=cost,
        users=[{"user_id": "u1", "email": "a@b.com", "cost": cost.model_dump()}],
    )
    assert org.users[0]["email"] == "a@b.com"


def test_portfolio_router_factory_smoke() -> None:
    """Importing + instantiating the router smoke-tests the endpoint
    registrations and DTO definitions."""
    from research_assistant.web.portfolio import create_portfolio_router

    router = create_portfolio_router()
    paths = {r.path for r in router.routes}
    assert "/portfolio/threads" in paths
    assert "/portfolio/sr-projects" in paths
    assert "/portfolio/deployments" in paths
    assert "/portfolio/summary" in paths
    assert "/portfolio/org" in paths
    # P2 #6 budget rollup endpoints
    assert "/portfolio/costs" in paths
    assert "/portfolio/org/costs" in paths


def test_portfolio_dtos_round_trip() -> None:
    """DTOs validate against shaped sample data."""
    from datetime import UTC, datetime

    from research_assistant.web.portfolio import (
        OrgRolloutOut,
        PortfolioDeploymentOut,
        PortfolioRolloutOut,
        PortfolioSrReviewOut,
        PortfolioThreadOut,
        UserTotalsOut,
    )

    now = datetime.now(UTC)
    t = PortfolioThreadOut(
        id="t1",
        title="x",
        workflow="meta_analysis",
        last_turn_kind="meta_analysis",
        message_count=4,
        last_activity=now,
    )
    assert t.workflow == "meta_analysis"

    sr = PortfolioSrReviewOut(
        id="s1",
        title="My SR",
        status="abstract_screening",
        n_candidates=120,
        n_included=10,
        n_excluded=50,
        n_pending=60,
        last_activity=now,
    )
    assert sr.n_pending == 60

    dep = PortfolioDeploymentOut(
        id="d1",
        name="Trial A",
        status="active",
        is_locked=False,
        n_subjects=42,
        last_activity=now,
    )
    assert dep.n_subjects == 42

    summary = PortfolioRolloutOut(
        threads_by_workflow={"meta_analysis": 3, "nma": 1},
        threads_total=4,
        sr_projects_total=2,
        deployments_total=1,
    )
    assert summary.threads_total == 4

    org = OrgRolloutOut(
        org_totals=summary,
        users=[
            UserTotalsOut(
                sub="abc",
                email="a@b.com",
                name="A",
                role_summary=["researcher"],
                thread_count=4,
                sr_project_count=2,
                deployment_count=0,
                last_activity=now,
            )
        ],
    )
    assert org.users[0].role_summary == ["researcher"]


def test_last_turn_kind_helper() -> None:
    """The `_last_turn_kind` helper is the bridge between persisted
    Message.final_answer JSON and the portfolio's `last_turn_kind`
    column — pin its behaviour."""
    from research_assistant.web.portfolio import _last_turn_kind

    assert _last_turn_kind(None) is None
    assert _last_turn_kind("not-json") is None
    assert _last_turn_kind('{"kind": "meta_analysis"}') == "meta_analysis"
    assert _last_turn_kind("{}") == "None"  # json.dumps(None) → "None"
