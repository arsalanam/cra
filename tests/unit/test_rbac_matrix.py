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


# ── SR-screening roles (top-6 #2) ────────────────────────────────────────


def test_researcher_can_create_sr_projects_and_ai_assist() -> None:
    """Researcher gets sr.create + sr.manage + sr.read + sr.ai_assist
    globally — they spin up projects and assign reviewers. The actual
    screening permissions (sr.screen / sr.adjudicate) come from project
    membership, not the researcher role."""
    perms = ROLE_PERMISSIONS[Role.RESEARCHER]
    assert Permission.SR_CREATE in perms
    assert Permission.SR_MANAGE in perms
    assert Permission.SR_READ in perms
    assert Permission.SR_AI_ASSIST in perms
    assert Permission.PRISMA_READ in perms
    # The screen-the-papers and tie-break perms come from membership
    # (sr_review-scoped), not from researcher globally.
    assert Permission.SR_SCREEN not in perms
    assert Permission.SR_ADJUDICATE not in perms


def test_reviewer_roles_have_screen_but_not_manage() -> None:
    for r in (Role.REVIEWER_1, Role.REVIEWER_2):
        perms = ROLE_PERMISSIONS[r]
        assert Permission.SR_READ in perms
        assert Permission.SR_SCREEN in perms
        assert Permission.PRISMA_READ in perms
        assert Permission.SR_MANAGE not in perms
        assert Permission.SR_ADJUDICATE not in perms


def test_adjudicator_is_screen_plus_tiebreak() -> None:
    perms = ROLE_PERMISSIONS[Role.ADJUDICATOR]
    assert Permission.SR_SCREEN in perms
    assert Permission.SR_ADJUDICATE in perms
    assert Permission.SR_READ in perms
    # Adjudicator does NOT manage the project's membership/criteria — the
    # researcher who created it does.
    assert Permission.SR_MANAGE not in perms
    assert Permission.SR_CREATE not in perms


def test_student_has_no_sr_permissions() -> None:
    """Student tier is meta-analysis-only; SR screening is explicitly off."""
    perms = ROLE_PERMISSIONS[Role.STUDENT]
    for sr_perm in (
        Permission.SR_CREATE,
        Permission.SR_MANAGE,
        Permission.SR_READ,
        Permission.SR_SCREEN,
        Permission.SR_ADJUDICATE,
        Permission.SR_AI_ASSIST,
        Permission.PRISMA_READ,
    ):
        assert sr_perm not in perms, f"student should not have {sr_perm.value}"


def test_auditor_can_read_sr_but_not_screen() -> None:
    perms = ROLE_PERMISSIONS[Role.AUDITOR]
    assert Permission.SR_READ in perms
    assert Permission.PRISMA_READ in perms
    assert Permission.SR_SCREEN not in perms
    assert Permission.SR_ADJUDICATE not in perms


# ── eCRF safety subsystem (AE/SAE + protocol deviations + CAPA) ──────────


def test_coordinator_records_ae_but_does_not_classify() -> None:
    """Coordinators capture safety events at the point of care; PI/DM
    handle classification + reporting + closure (separation of duties)."""
    perms = ROLE_PERMISSIONS[Role.COORDINATOR]
    assert Permission.AE_RECORD in perms
    assert Permission.DEVIATION_RECORD in perms
    # Higher-tier actions denied
    assert Permission.AE_CLASSIFY not in perms
    assert Permission.SAE_REPORT not in perms
    assert Permission.DEVIATION_CLASSIFY not in perms
    assert Permission.CAPA_AUTHOR not in perms
    assert Permission.CAPA_CLOSE not in perms


def test_pi_classifies_aes_and_closes_capas() -> None:
    perms = ROLE_PERMISSIONS[Role.PRINCIPAL_INVESTIGATOR]
    assert Permission.AE_CLASSIFY in perms
    assert Permission.SAE_REPORT in perms
    assert Permission.CAPA_CLOSE in perms
    # PI does not record AEs in the first place (oversight role) and is
    # not the CAPA author (data manager's job).
    assert Permission.AE_RECORD not in perms
    assert Permission.CAPA_AUTHOR not in perms
    assert Permission.DEVIATION_RECORD not in perms


def test_data_manager_authors_capas_and_classifies_deviations() -> None:
    perms = ROLE_PERMISSIONS[Role.DATA_MANAGER]
    assert Permission.DEVIATION_CLASSIFY in perms
    assert Permission.CAPA_AUTHOR in perms
    assert Permission.SAE_REPORT in perms
    # CAPA close is PI; AE classify is PI.
    assert Permission.CAPA_CLOSE not in perms
    assert Permission.AE_CLASSIFY not in perms


def test_monitor_logs_deviations_but_does_not_classify_them() -> None:
    perms = ROLE_PERMISSIONS[Role.MONITOR]
    assert Permission.DEVIATION_RECORD in perms
    # Monitors surface findings; classification + closure are DM/PI.
    assert Permission.DEVIATION_CLASSIFY not in perms
    assert Permission.CAPA_AUTHOR not in perms
    assert Permission.CAPA_CLOSE not in perms
    assert Permission.AE_RECORD not in perms


def test_student_has_no_safety_perms() -> None:
    """Student tier is meta-analysis only — explicitly no eCRF safety access."""
    perms = ROLE_PERMISSIONS[Role.STUDENT]
    for safety_perm in (
        Permission.AE_RECORD,
        Permission.AE_CLASSIFY,
        Permission.SAE_REPORT,
        Permission.DEVIATION_RECORD,
        Permission.DEVIATION_CLASSIFY,
        Permission.CAPA_AUTHOR,
        Permission.CAPA_CLOSE,
    ):
        assert safety_perm not in perms, f"student must not have {safety_perm.value}"


# ── CDISC submission pipeline (SDTM → ADaM → TLF) ────────────────────────


def test_data_manager_runs_cdisc_derivation_and_exports() -> None:
    """The DM owns the derivation pipeline; PI signs off + exports."""
    perms = ROLE_PERMISSIONS[Role.DATA_MANAGER]
    assert Permission.CDISC_DERIVE in perms
    assert Permission.CDISC_READ in perms
    assert Permission.CDISC_EXPORT in perms


def test_pi_can_export_but_does_not_derive() -> None:
    """PI signs off the submission bundle but doesn't run the pipeline.
    Separation-of-duties — derivation + sign-off are different actions."""
    perms = ROLE_PERMISSIONS[Role.PRINCIPAL_INVESTIGATOR]
    assert Permission.CDISC_READ in perms
    assert Permission.CDISC_EXPORT in perms
    assert Permission.CDISC_DERIVE not in perms


def test_monitor_and_auditor_can_read_cdisc_but_not_derive_or_export() -> None:
    """Monitors verify derived data against source; auditors inspect.
    Neither runs the pipeline nor produces the regulator-facing export."""
    for r in (Role.MONITOR, Role.AUDITOR):
        perms = ROLE_PERMISSIONS[r]
        assert Permission.CDISC_READ in perms, f"{r.value} needs cdisc.read"
        assert Permission.CDISC_DERIVE not in perms
        assert Permission.CDISC_EXPORT not in perms


def test_coordinator_blocked_from_cdisc() -> None:
    """Coordinators capture data; they don't touch the derivation pipeline."""
    perms = ROLE_PERMISSIONS[Role.COORDINATOR]
    for p in (
        Permission.CDISC_DERIVE,
        Permission.CDISC_READ,
        Permission.CDISC_EXPORT,
    ):
        assert p not in perms, f"coordinator must not have {p.value}"


def test_student_blocked_from_all_cdisc() -> None:
    perms = ROLE_PERMISSIONS[Role.STUDENT]
    for p in (
        Permission.CDISC_DERIVE,
        Permission.CDISC_READ,
        Permission.CDISC_EXPORT,
    ):
        assert p not in perms, f"student must not have {p.value}"


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
        # sap_drafter (sample-size + SAP)
        "sap_drafter",
        # manuscript_drafter (IMRaD + reviewer-response loop)
        "manuscript_drafter",
        # Start-up tier: trial registration (CT.gov + EU CTR) + IRB packet
        "registration_drafter",
        "irb_drafter",
        # Analysis-finale tier: CSR (ICH E3) drafter
        "csr_drafter",
    }
    assert set(SKILL_PERMISSION) == expected_workflows


def test_researcher_can_run_sap_drafter_but_student_cannot() -> None:
    """Prospective-trial design lives in the researcher tier; student is
    explicitly meta-analysis-only (per the locked decision)."""
    assert Permission.SKILL_SAP_DRAFTER in ROLE_PERMISSIONS[Role.RESEARCHER]
    assert Permission.SKILL_SAP_DRAFTER in ROLE_PERMISSIONS[Role.ADMIN]
    assert Permission.SKILL_SAP_DRAFTER not in ROLE_PERMISSIONS[Role.STUDENT]


def test_researcher_can_run_registration_drafter_but_student_cannot() -> None:
    """Start-up tier: trial-registration drafter is researcher-tier."""
    assert Permission.SKILL_REGISTRATION_DRAFTER in ROLE_PERMISSIONS[Role.RESEARCHER]
    assert Permission.SKILL_REGISTRATION_DRAFTER in ROLE_PERMISSIONS[Role.ADMIN]
    assert Permission.SKILL_REGISTRATION_DRAFTER not in ROLE_PERMISSIONS[Role.STUDENT]


def test_researcher_can_run_irb_drafter_but_student_cannot() -> None:
    """Start-up tier: IRB packet drafter is researcher-tier."""
    assert Permission.SKILL_IRB_DRAFTER in ROLE_PERMISSIONS[Role.RESEARCHER]
    assert Permission.SKILL_IRB_DRAFTER in ROLE_PERMISSIONS[Role.ADMIN]
    assert Permission.SKILL_IRB_DRAFTER not in ROLE_PERMISSIONS[Role.STUDENT]


def test_researcher_can_run_csr_drafter_but_student_cannot() -> None:
    """Analysis-finale tier: CSR (ICH E3) drafter is researcher-tier."""
    assert Permission.SKILL_CSR_DRAFTER in ROLE_PERMISSIONS[Role.RESEARCHER]
    assert Permission.SKILL_CSR_DRAFTER in ROLE_PERMISSIONS[Role.ADMIN]
    assert Permission.SKILL_CSR_DRAFTER not in ROLE_PERMISSIONS[Role.STUDENT]


def test_clinical_roles_do_not_get_csr_drafter_permission() -> None:
    for role in (
        Role.COORDINATOR,
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.PRINCIPAL_INVESTIGATOR,
        Role.AUDITOR,
    ):
        assert Permission.SKILL_CSR_DRAFTER not in ROLE_PERMISSIONS[role]


def test_data_manager_generates_randomization_schedule() -> None:
    """E8: DM is the only role that creates schedules. PI does NOT
    (separation-of-duties: PI breaks codes, DM creates schedules)."""
    dm_perms = ROLE_PERMISSIONS[Role.DATA_MANAGER]
    pi_perms = ROLE_PERMISSIONS[Role.PRINCIPAL_INVESTIGATOR]
    assert Permission.RANDOMIZATION_GENERATE in dm_perms
    assert Permission.RANDOMIZATION_GENERATE not in pi_perms


def test_pi_owns_the_codebreak_authority() -> None:
    """Only PI + admin can emergency-unblind."""
    pi_perms = ROLE_PERMISSIONS[Role.PRINCIPAL_INVESTIGATOR]
    assert Permission.RANDOMIZATION_CODEBREAK in pi_perms
    for r in (
        Role.COORDINATOR,
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.AUDITOR,
    ):
        assert Permission.RANDOMIZATION_CODEBREAK not in ROLE_PERMISSIONS[r]
    assert Permission.RANDOMIZATION_CODEBREAK in ROLE_PERMISSIONS[Role.ADMIN]


def test_coordinator_and_pi_can_allocate_subjects() -> None:
    """The randomisation call happens at enrolment — coordinator + PI."""
    assert Permission.RANDOMIZATION_ALLOCATE in ROLE_PERMISSIONS[Role.COORDINATOR]
    assert Permission.RANDOMIZATION_ALLOCATE in ROLE_PERMISSIONS[Role.PRINCIPAL_INVESTIGATOR]
    # DM does NOT allocate — DM only generates the schedule + reads.
    assert Permission.RANDOMIZATION_ALLOCATE not in ROLE_PERMISSIONS[Role.DATA_MANAGER]


def test_monitor_and_auditor_can_read_allocations_but_not_break() -> None:
    for r in (Role.MONITOR, Role.AUDITOR):
        perms = ROLE_PERMISSIONS[r]
        assert Permission.RANDOMIZATION_READ in perms
        assert Permission.RANDOMIZATION_CODEBREAK not in perms
        assert Permission.RANDOMIZATION_GENERATE not in perms
        assert Permission.RANDOMIZATION_ALLOCATE not in perms


def test_clinical_roles_do_not_get_start_up_drafter_permissions() -> None:
    """Coordinator / data_manager / monitor / PI / auditor don't draft
    registrations or IRB packets — those are researcher-side artefacts."""
    for role in (
        Role.COORDINATOR,
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.PRINCIPAL_INVESTIGATOR,
        Role.AUDITOR,
    ):
        perms = ROLE_PERMISSIONS[role]
        assert Permission.SKILL_REGISTRATION_DRAFTER not in perms
        assert Permission.SKILL_IRB_DRAFTER not in perms


def test_researcher_can_run_manuscript_drafter_but_student_cannot() -> None:
    """IMRaD manuscript drafting + reviewer-response loop is researcher
    tier — same posture as sap_drafter."""
    assert Permission.SKILL_MANUSCRIPT_DRAFTER in ROLE_PERMISSIONS[Role.RESEARCHER]
    assert Permission.SKILL_MANUSCRIPT_DRAFTER in ROLE_PERMISSIONS[Role.ADMIN]
    assert Permission.SKILL_MANUSCRIPT_DRAFTER not in ROLE_PERMISSIONS[Role.STUDENT]
    # eCRF + SR-screening roles + auditor get no evidence skills either.
    for r in (
        Role.STUDY_DESIGNER,
        Role.PRINCIPAL_INVESTIGATOR,
        Role.COORDINATOR,
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.AUDITOR,
        Role.REVIEWER_1,
        Role.REVIEWER_2,
        Role.ADJUDICATOR,
    ):
        assert Permission.SKILL_MANUSCRIPT_DRAFTER not in ROLE_PERMISSIONS[r], (
            f"{r.value} should not have skill.manuscript_drafter"
        )
    # eCRF-side roles + reviewers don't get evidence skills either.
    for r in (
        Role.STUDY_DESIGNER,
        Role.PRINCIPAL_INVESTIGATOR,
        Role.COORDINATOR,
        Role.DATA_MANAGER,
        Role.MONITOR,
        Role.AUDITOR,
        Role.REVIEWER_1,
        Role.REVIEWER_2,
        Role.ADJUDICATOR,
    ):
        assert Permission.SKILL_SAP_DRAFTER not in ROLE_PERMISSIONS[r], (
            f"{r.value} should not have skill.sap_drafter"
        )


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
