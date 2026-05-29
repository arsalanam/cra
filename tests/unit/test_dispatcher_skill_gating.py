"""Dispatcher skill gating — RBAC-1 enforcement at the routing seam.

`classify()` remains a pure heuristic; gating happens in
`authorize_workflow()`, called from `dispatch()` after classification.
Tests here cover the gate, not the routing logic itself (that's in
test_dispatcher.py).
"""

from __future__ import annotations

import pytest

from research_assistant.agent.dispatcher import (
    SkillNotAuthorizedError,
    authorize_workflow,
)
from research_assistant.auth.rbac import (
    ROLE_PERMISSIONS,
    SKILL_PERMISSION,
    Permission,
    Role,
)


def test_none_permissions_bypasses_gate() -> None:
    """auth-disabled / test mode passes None → every workflow reachable."""
    for workflow in SKILL_PERMISSION:
        # Should not raise
        authorize_workflow(workflow, None)


def test_admin_can_run_every_workflow() -> None:
    admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
    for workflow in SKILL_PERMISSION:
        authorize_workflow(workflow, admin_perms)


def test_researcher_can_run_evidence_workflows_but_not_ecrf_design() -> None:
    researcher_perms = ROLE_PERMISSIONS[Role.RESEARCHER]
    for workflow in (
        "meta_analysis",
        "general_qa",
        "search_strategy",
        "sr_protocol",
        "risk_of_bias",
    ):
        authorize_workflow(workflow, researcher_perms)
    with pytest.raises(SkillNotAuthorizedError) as exc:
        authorize_workflow("ecrf_design", researcher_perms)
    assert exc.value.required == Permission.SKILL_ECRF_DESIGN
    assert exc.value.workflow == "ecrf_design"


def test_student_can_run_meta_analysis_and_general_qa_only() -> None:
    """The core Student promise: meta-analysis + dispatcher fallback only."""
    student_perms = ROLE_PERMISSIONS[Role.STUDENT]
    authorize_workflow("meta_analysis", student_perms)
    authorize_workflow("general_qa", student_perms)
    for blocked in ("search_strategy", "sr_protocol", "risk_of_bias", "ecrf_design"):
        with pytest.raises(SkillNotAuthorizedError) as exc:
            authorize_workflow(blocked, student_perms)
        assert exc.value.workflow == blocked


def test_unknown_workflow_fails_closed() -> None:
    """A specialist not in SKILL_PERMISSION must be rejected, not silently
    let through — fail-closed catches a half-finished new specialist."""
    with pytest.raises(SkillNotAuthorizedError):
        authorize_workflow("does_not_exist", ROLE_PERMISSIONS[Role.ADMIN])


def test_skill_error_carries_workflow_and_required_perm() -> None:
    student_perms = ROLE_PERMISSIONS[Role.STUDENT]
    with pytest.raises(SkillNotAuthorizedError) as exc:
        authorize_workflow("risk_of_bias", student_perms)
    assert exc.value.workflow == "risk_of_bias"
    assert exc.value.required == Permission.SKILL_RISK_OF_BIAS
    assert "risk_of_bias" in str(exc.value)
    assert "skill.risk_of_bias" in str(exc.value)
