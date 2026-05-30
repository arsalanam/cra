"""Computer-system-validation pack (eCRF E7).

Three documented artefacts shipped here:

  • IQ — Installation Qualification: snapshot of the running system
    (Python version, package version, pinned deps, DB schema, audit-
    trail trigger presence, Cognito pool ID, env summary). Generated
    at request time from `iq.collect_iq_snapshot`.

  • OQ — Operational Qualification: pytest-driven requirements
    traceability matrix. Each requirement (RBAC-001 …) maps to one or
    more test cases (file:test_name) plus the regulatory anchor
    (Part 11 §11.10(e), ICH E6 R2 §5.5.3, etc.). The runner joins
    pytest JSON output with the matrix to produce a structured
    pass/fail report. See `rtm.py` + `oq.py`.

  • PQ — Performance Qualification: customer-side runbook PDF
    documenting the smoke-flow they execute against their installed
    instance (login → study create → form sign with reauth → study
    lock → IQ snapshot download). See `pq.py`.

The PDF renderers live in `research_assistant.reports.validation_pack`
following the same reportlab posture as the other report modules.
"""

from __future__ import annotations

__all__ = [
    "iq",
    "oq",
    "pq",
    "rtm",
]
