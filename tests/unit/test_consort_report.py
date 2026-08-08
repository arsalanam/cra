"""CONSORT enrolment flow-diagram PDF builder."""

from __future__ import annotations

from datetime import UTC, datetime

from research_assistant.reports.consort import build_consort_flow_pdf

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _funnel(**totals: int) -> dict[str, object]:
    base = {"screened": 0, "eligible": 0, "consented": 0, "enrolled": 0}
    base.update(totals)
    return {
        "deployment_id": "dep-1",
        "totals": base,
        "screen_failures_by_reason": {},
        "per_week_per_site": {},
    }


def test_build_returns_pdf_bytes() -> None:
    funnel = _funnel(screened=120, eligible=90, consented=80, enrolled=75)
    funnel["screen_failures_by_reason"] = {"age_out_of_range": 20, "pregnancy": 10}
    pdf = build_consort_flow_pdf(funnel, deployment_name="ACME-01", generated_at=_NOW)
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000  # a real, non-trivial document


def test_build_handles_empty_funnel() -> None:
    """A deployment with no screening yet must still render (all zeros), not
    crash."""
    pdf = build_consort_flow_pdf(_funnel(), deployment_name="Empty", generated_at=_NOW)
    assert pdf[:5] == b"%PDF-"


def test_build_clamps_inconsistent_counts() -> None:
    """Out-of-order counts (e.g. enrolled > screened from a data anomaly)
    must not raise or emit a negative — the builder clamps at 0."""
    funnel = _funnel(screened=10, eligible=12, consented=15, enrolled=20)
    pdf = build_consort_flow_pdf(funnel, deployment_name="Weird", generated_at=_NOW)
    assert pdf[:5] == b"%PDF-"


def test_build_with_many_reasons_still_one_document() -> None:
    funnel = _funnel(screened=200, eligible=100, consented=95, enrolled=90)
    funnel["screen_failures_by_reason"] = {
        "age_out_of_range": 30,
        "lab_or_imaging_abnormality": 25,
        "prior_treatment": 20,
        "pregnancy": 15,
        "exclusion_criteria_met": 10,
    }
    pdf = build_consort_flow_pdf(funnel, deployment_name="Big", generated_at=_NOW)
    assert pdf[:5] == b"%PDF-"
