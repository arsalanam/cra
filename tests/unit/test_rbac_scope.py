"""RBAC scope resolution — `global ⊃ study ⊃ site` from rbac-design.md §4.1.

These are pure-data tests; the eCRF resource→scope resolvers (RBAC-2)
sit on top of the same primitives.
"""

from __future__ import annotations

import pytest

from research_assistant.auth.rbac import (
    Permission,
    ScopeType,
    assignment_applies,
    effective_permissions,
)

# ── assignment_applies — the scope match primitive ───────────────────────


def test_global_assignment_always_applies() -> None:
    assert assignment_applies(ScopeType.GLOBAL, None, study_id=None, site_id=None)
    assert assignment_applies(ScopeType.GLOBAL, None, study_id="S1", site_id=None)
    assert assignment_applies(ScopeType.GLOBAL, None, study_id="S1", site_id="T1")


def test_study_assignment_applies_only_for_matching_study() -> None:
    # Study-level check passes for the same study
    assert assignment_applies(ScopeType.STUDY, "S1", study_id="S1", site_id=None)
    # Different study — no match
    assert not assignment_applies(ScopeType.STUDY, "S1", study_id="S2", site_id=None)
    # Global check (no study context) — never satisfied by a study-scoped row
    assert not assignment_applies(ScopeType.STUDY, "S1", study_id=None, site_id=None)


def test_site_assignment_applies_only_for_matching_site() -> None:
    assert assignment_applies(ScopeType.SITE, "T1", study_id="S1", site_id="T1")
    assert not assignment_applies(ScopeType.SITE, "T1", study_id="S1", site_id="T2")
    assert not assignment_applies(ScopeType.SITE, "T1", study_id="S1", site_id=None)


def test_unknown_scope_type_never_applies() -> None:
    """Defensive — a corrupted DB row mustn't accidentally grant access."""
    assert not assignment_applies("garbage", None, study_id=None, site_id=None)
    assert not assignment_applies("garbage", "S1", study_id="S1", site_id="T1")


# ── effective_permissions — the aggregation under scope filtering ─────────


def test_global_check_unions_only_global_assignments() -> None:
    """A study-scoped grant cannot satisfy a request asked at global scope."""
    perms = effective_permissions(
        [
            ("researcher", ScopeType.GLOBAL.value, None),
            ("data_manager", ScopeType.STUDY.value, "S1"),
        ],
        study_id=None,
        site_id=None,
    )
    # researcher (global) grants library/skills
    assert Permission.SKILL_META_ANALYSIS in perms
    assert Permission.LIBRARY_READ in perms
    # data_manager (study-scoped) MUST NOT bleed into global checks
    assert Permission.QUERY_RAISE not in perms
    assert Permission.FORM_UNLOCK not in perms


def test_study_check_aggregates_global_plus_matching_study_grants() -> None:
    """A check at (study=S1) honours global grants AND study=S1 grants."""
    assignments = [
        ("auditor", ScopeType.GLOBAL.value, None),
        ("data_manager", ScopeType.STUDY.value, "S1"),
        ("data_manager", ScopeType.STUDY.value, "S2"),  # noise
    ]
    perms = effective_permissions(assignments, study_id="S1", site_id=None)
    assert Permission.AUDIT_READ in perms  # from global auditor
    assert Permission.QUERY_RAISE in perms  # from data_manager@S1
    assert Permission.FORM_UNLOCK in perms  # from data_manager@S1
    # The S2 grant did NOT slip into S1's check
    # (we verify this by asking the S2 check separately and seeing those
    # perms appear there, not by an absence here — but the dedup happens
    # via the set anyway)


def test_study_check_does_not_pick_up_other_studies_grants() -> None:
    assignments = [("data_manager", ScopeType.STUDY.value, "S2")]
    perms_s1 = effective_permissions(assignments, study_id="S1", site_id=None)
    perms_s2 = effective_permissions(assignments, study_id="S2", site_id=None)
    assert Permission.QUERY_RAISE not in perms_s1
    assert Permission.QUERY_RAISE in perms_s2


def test_site_check_honours_global_only_in_rbac1() -> None:
    """Site-scoped assignments work for the matching site. The 'study scope
    satisfies a site check whose site belongs to that study' arm requires
    the caller to provide both study_id and site_id — the resolver in
    rbac.py has no DB to look up the parent study of a site (deferred to
    RBAC-2's resource→scope resolvers)."""
    assignments = [
        ("coordinator", ScopeType.SITE.value, "T1"),
        ("coordinator", ScopeType.SITE.value, "T2"),
    ]
    perms_t1 = effective_permissions(assignments, study_id="S1", site_id="T1")
    perms_t2 = effective_permissions(assignments, study_id="S1", site_id="T2")
    assert Permission.DATA_ENTER in perms_t1
    assert Permission.DATA_ENTER in perms_t2

    # A site-scoped grant does NOT satisfy a study-level check (no site_id given)
    perms_study_only = effective_permissions(assignments, study_id="S1", site_id=None)
    assert Permission.DATA_ENTER not in perms_study_only


@pytest.mark.parametrize(
    "scope_type,scope_id,check_study,check_site,expected",
    [
        # global always applies
        (ScopeType.GLOBAL.value, None, None, None, True),
        (ScopeType.GLOBAL.value, None, "S1", "T1", True),
        # study-scoped: only when study_id matches
        (ScopeType.STUDY.value, "S1", "S1", None, True),
        (ScopeType.STUDY.value, "S1", "S2", None, False),
        (ScopeType.STUDY.value, "S1", None, None, False),
        # site-scoped: only when site_id matches
        (ScopeType.SITE.value, "T1", "S1", "T1", True),
        (ScopeType.SITE.value, "T1", "S1", "T2", False),
        (ScopeType.SITE.value, "T1", "S1", None, False),
    ],
)
def test_assignment_applies_parametric(
    scope_type: str,
    scope_id: str | None,
    check_study: str | None,
    check_site: str | None,
    expected: bool,
) -> None:
    assert (
        assignment_applies(scope_type, scope_id, study_id=check_study, site_id=check_site)
        is expected
    )
