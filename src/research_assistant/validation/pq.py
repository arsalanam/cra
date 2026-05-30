"""PQ — Performance Qualification runbook (eCRF E7).

Distinct from IQ + OQ in that PQ is **executed by the customer at
their site** against real data and real users — what we can ship is
the procedure they follow + the sign-off table they record results
into. Renders to a documented PDF runbook downloadable by admin.

The procedure below is the platform-MVP smoke flow: login as admin,
create a deployment, sign a form (with re-auth), lock the study,
download an IQ snapshot. Customers add their own clinical-context
steps on top.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PqStep(BaseModel):
    ordinal: int
    title: str
    procedure: str = Field(description="What the operator does.")
    expected_result: str = Field(description="What the operator should observe.")
    regulatory_anchor: str | None = None


class PerformanceRunbook(BaseModel):
    """The PQ artefact — rendered to PDF by the validation_pack renderer."""

    title: str = "Performance Qualification Runbook — Clinical Research Assistant"
    version: str = "1.0.0"
    overview: str = (
        "This runbook documents the Performance Qualification (PQ) procedure that "
        "verifies the installed Clinical Research Assistant system behaves as "
        "specified under production-representative conditions with real users. "
        "It complements the IQ snapshot (auto-generated at install time) and the "
        "OQ requirements-traceability run (auto-generated against the test suite). "
        "Execute each step in order. The operator records actual results in the "
        "right-hand column and signs the table at the end of the runbook."
    )
    steps: list[PqStep] = Field(
        default_factory=lambda: [
            PqStep(
                ordinal=1,
                title="Authenticated login",
                procedure=(
                    "Navigate to the public URL of the installed instance. "
                    "Sign in with a Cognito-provisioned account. Confirm the "
                    "session cookie is set and the welcome screen loads."
                ),
                expected_result=(
                    "Welcome page renders within 5 seconds; the navigation "
                    "sidebar shows only the workflows the user's role permits "
                    "(RBAC frontend gating active)."
                ),
                regulatory_anchor="21 CFR Part 11 §11.10(d); RBAC-001",
            ),
            PqStep(
                ordinal=2,
                title="Deployment + subject creation",
                procedure=(
                    "As a study_designer, create a new study deployment. "
                    "Add one site. As a coordinator, enroll one subject "
                    "into the site with a unique subject_code."
                ),
                expected_result=(
                    "Subject appears in the subject list immediately. Audit "
                    "trail shows three rows: study_deployment.create, "
                    "site.create, subject.create — each with actor_sub, "
                    "timestamp, and no old_value."
                ),
                regulatory_anchor="ALCOA+ — Attributable, Original; AUDIT-002",
            ),
            PqStep(
                ordinal=3,
                title="Form save + hard edit-check rejection",
                procedure=(
                    "Open the subject's first form. Enter a value that fails "
                    "a hard edit check (e.g. age > 200). Submit."
                ),
                expected_result=(
                    "Submit is rejected with a 422 response. No row is "
                    "persisted into item_data — verify via the audit trail "
                    "that no entries exist for the offending item."
                ),
                regulatory_anchor="ICH E6(R2) §5.5.3(a); DATA-001",
            ),
            PqStep(
                ordinal=4,
                title="Form sign with password re-authentication",
                procedure=(
                    "Re-open the same form, enter valid data, mark complete. "
                    "Click Sign — the UI prompts for a password. Enter the "
                    "current session user's password and confirm."
                ),
                expected_result=(
                    "Signature is recorded with a non-empty content_hash. "
                    "Audit row sign captures the signer's sub + meaning. "
                    "Attempting the same flow with a deliberately wrong "
                    "password yields a 401."
                ),
                regulatory_anchor="21 CFR Part 11 §11.200; SIGN-001 / SIGN-002",
            ),
            PqStep(
                ordinal=5,
                title="Casebook signoff",
                procedure=(
                    "As PI, navigate to the subject panel. Click Casebook "
                    "signoff with a meaning string + password reauth."
                ),
                expected_result=(
                    "subject_signatures row created. Subject is now locked "
                    "for further data entry — attempting to edit any item "
                    "via the form returns 409."
                ),
                regulatory_anchor="21 CFR Part 11 §11.50/§11.70",
            ),
            PqStep(
                ordinal=6,
                title="Study lock",
                procedure=(
                    "As a data_manager, navigate to the deployment-level "
                    "Submissions card. Confirm the study has no open "
                    "queries. Click Lock study with a reason captured."
                ),
                expected_result=(
                    "Lock recorded; subsequent attempts to submit data, "
                    "sign forms, or SDV-verify against any subject in the "
                    "deployment return 409 with 'Study deployment {...} is "
                    "locked'."
                ),
                regulatory_anchor="ICH E6(R2) §5.5.3(c); LOCK-001 / LOCK-002",
            ),
            PqStep(
                ordinal=7,
                title="IQ snapshot download + comparison",
                procedure=(
                    "Navigate to admin → Validation Pack → Installation "
                    "Qualification. Download the IQ PDF. Compare the "
                    "captured package version + dependency pins against "
                    "the customer's expected manifest."
                ),
                expected_result=(
                    "Package version + python version + DB dialect + "
                    "Cognito pool ID match the customer's IT-issued "
                    "installation record. Audit-immutability trigger row "
                    "is present (✓)."
                ),
                regulatory_anchor="21 CFR Part 11 §11.10(a); AUDIT-001",
            ),
            PqStep(
                ordinal=8,
                title="OQ requirements run",
                procedure=(
                    "From the same admin panel, click Run OQ. When the "
                    "run completes, download the OQ PDF and review the "
                    "per-requirement pass/fail table."
                ),
                expected_result=(
                    "Every requirement in the matrix is in the 'passed' "
                    "column with no 'missing' rows. If any requirement "
                    "fails, the OQ run blocks site go-live until resolved."
                ),
                regulatory_anchor="FDA 2018 CSV guidance §3.2.1",
            ),
        ]
    )

    signoff_instructions: str = (
        "Operator: print this runbook, complete the actual-results column "
        "for each step, sign + date below. Retain alongside the IQ + OQ PDFs "
        "in the customer's validation binder. Re-run on every major version "
        "upgrade, on any change to Cognito configuration, and at least "
        "annually."
    )


__all__ = ["PerformanceRunbook", "PqStep"]
