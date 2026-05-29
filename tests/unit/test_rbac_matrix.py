"""RBAC permission matrix — `auth.rbac` pure-data resolver tests.

Locks the role → permission mapping in `rbac-design.md` §4.5. When the
matrix changes intentionally, update both the doc and these tests.
"""

from __future__ import annotations

import pytest

from research_assistant.auth.rbac import (
    ROLE_PERMISSIONS,
    SKILL_PERMISSION,
    Permission,
    Role,
    ScopeType,
    effective_permissions,
    normalize_legacy_role,
    permissions_for_role,
)


def test_admin_holds_every_permission() -> None:
    assert ROLE_PERMISSIONS[Role.ADMIN] == frozenset(Permission)


def test_researcher_holds_all_evidence_skills_plus_library() -> None:
    perms = ROLE_PERMISSIONS[Role.RESEARCHER]
    assert Permission.SKILL_META_ANALYSIS in perms
    assert Permission.SKILL_GENERAL_QA in perms
    assert Permission.SKILL_SEARCH_STRATEGY in perms
    assert Permission.SKILL_SR_PROTOCOL in perms
    assert Permission.SKILL_RISK_OF_BIAS in perms
    assert Permission.LIBRARY_READ in perms
    assert Permission.LIBRARY_WRITE in perms
    assert Permission.WATCH_MANAGE in perms
    # Researcher is NOT an eCRF designer or admin
    assert Permission.SKILL_ECRF_DESIGN not in perms
    assert Permission.USER_MANAGE not in perms
    assert Permission.DATA_ENTER not in perms


def test_student_is_meta_analysis_plus_general_qa_only() -> None:
    """The Student tier added in this slice — locked to evidence-light use."""
    perms = ROLE_PERMISSIONS[Role.STUDENT]
    assert perms == frozenset({Permission.SKILL_META_ANALYSIS, Permission.SKILL_GENERAL_QA})
    # Explicitly NOT granted — these are the ones a frontend Student
    # account must not be able to reach.
    assert Permission.SKILL_SEARCH_STRATEGY not in perms
    assert Permission.SKILL_SR_PROTOCOL not in perms
    assert Permission.SKILL_RISK_OF_BIAS not in perms
    assert Permission.SKILL_ECRF_DESIGN not in perms
    assert Permission.LIBRARY_READ not in perms
    assert Permission.WATCH_READ not in perms
    assert Permission.DATA_ENTER not in perms
    assert Permission.USER_MANAGE not in perms


def test_auditor_is_read_only() -> None:
    perms = ROLE_PERMISSIONS[Role.AUDITOR]
    assert Permission.AUDIT_READ in perms
    assert Permission.DATA_READ in perms
    assert Permission.LIBRARY_READ in perms
    # No write capabilities anywhere
    assert Permission.DATA_ENTER not in perms
    assert Permission.QUERY_RAISE not in perms
    assert Permission.LIBRARY_WRITE not in perms
    assert Permission.USER_MANAGE not in perms


def test_coordinator_can_enter_data_but_not_sign_or_lock() -> None:
    perms = ROLE_PERMISSIONS[Role.COORDINATOR]
    assert Permission.DATA_ENTER in perms
    assert Permission.QUERY_RESPOND in perms
    # Sign and unlock are PI / DM territory respectively
    assert Permission.FORM_SIGN not in perms
    assert Permission.FORM_UNLOCK not in perms
    assert Permission.SDV_VERIFY not in perms


def test_pi_signs_and_signs_off_casebook() -> None:
    perms = ROLE_PERMISSIONS[Role.PRINCIPAL_INVESTIGATOR]
    assert Permission.FORM_SIGN in perms
    assert Permission.CASEBOOK_SIGNOFF in perms
    assert Permission.QUERY_CLOSE in perms
    # PI is oversight — should not be entering data themselves
    assert Permission.DATA_ENTER not in perms


def test_data_manager_holds_query_and_unlock_powers() -> None:
    perms = ROLE_PERMISSIONS[Role.DATA_MANAGER]
    assert Permission.QUERY_RAISE in perms
    assert Permission.QUERY_CLOSE in perms
    assert Permission.FORM_UNLOCK in perms
    assert Permission.SUBJECT_UNLOCK in perms
    # DM cannot sign forms (that's PI)
    assert Permission.FORM_SIGN not in perms


def test_monitor_does_sdv_but_not_data_entry() -> None:
    perms = ROLE_PERMISSIONS[Role.MONITOR]
    assert Permission.SDV_VERIFY in perms
    assert Permission.DATA_READ in perms
    assert Permission.QUERY_RAISE in perms
    # Crucial separation of duties
    assert Permission.DATA_ENTER not in perms
    assert Permission.QUERY_CLOSE not in perms


def test_skill_permission_covers_every_specialist() -> None:
    """Every workflow registered in `agent/specialists/` must map to a
    skill permission, or the dispatcher will fail-closed on it."""
    # WORKFLOW_NAME constants from agent/specialists/*.py
    expected_workflows = {
        "meta_analysis",
        "general_qa",
        "search_strategy",
        "sr_protocol",
        "risk_of_bias",
        # ecrf_design specialist exists; gated separately
        "ecrf_design",
    }
    assert set(SKILL_PERMISSION) == expected_workflows


def test_permissions_for_role_accepts_legacy_data_entry_alias() -> None:
    """Pre-RBAC-1 'data_entry' rows must still resolve to the coordinator
    permission set so existing assignments keep working."""
    assert permissions_for_role("data_entry") == ROLE_PERMISSIONS[Role.COORDINATOR]


def test_normalize_unknown_role_returns_none() -> None:
    assert normalize_legacy_role("nonexistent") is None
    # An unknown role yields the empty permission set, not a crash
    assert permissions_for_role("nonexistent") == frozenset()


@pytest.mark.parametrize(
    "assignments,expected_subset",
    [
        # Single global admin grant
        ([("admin", "global", None)], frozenset(Permission)),
        # Two roles unioned at global scope
        (
            [("researcher", "global", None), ("auditor", "global", None)],
            ROLE_PERMISSIONS[Role.RESEARCHER] | ROLE_PERMISSIONS[Role.AUDITOR],
        ),
        # Unknown role contributes nothing; researcher still grants its perms
        (
            [("researcher", "global", None), ("ghost", "global", None)],
            ROLE_PERMISSIONS[Role.RESEARCHER],
        ),
    ],
)
def test_effective_permissions_unions_at_global_scope(
    assignments: list[tuple[str, str, str | None]],
    expected_subset: frozenset[Permission],
) -> None:
    perms = effective_permissions(assignments)
    assert perms == expected_subset


def test_effective_permissions_filters_legacy_data_entry_through_alias() -> None:
    """A pre-RBAC-1 'data_entry' string in a (role, scope, id) tuple must
    still grant the Coordinator perm set after going through the alias map."""
    perms = effective_permissions([("data_entry", ScopeType.GLOBAL.value, None)])
    assert Permission.DATA_ENTER in perms
    assert Permission.QUERY_RESPOND in perms
