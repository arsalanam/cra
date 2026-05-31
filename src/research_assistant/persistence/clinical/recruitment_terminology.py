"""Recruitment / screening codebooks.

Three small canonical sets:

  • CONSORT_EXCLUSION_REASONS — 8 standard screen-failure reasons from
    the CONSORT 2010 statement (Schulz KF et al., BMJ 2010). Operators
    pick one + optionally add free text in `exclusion_reason_text`.
  • AGE_BANDS — 6 canonical NIH-style age bands.
  • SEX_VALUES, RACE_VALUES, ETHNICITY_VALUES — OMB-1997 categorical
    sets (the same shape FDA Form 1572 and NIH diversity reports use).

The frontend dropdowns are populated from these; the repository's
funnel rollup keys per-reason counts by these codes.
"""

from __future__ import annotations

from typing import Final

# ── CONSORT 2010 screen-failure reasons ─────────────────────────────────


CONSORT_EXCLUSION_REASONS: Final[dict[str, str]] = {
    "age_out_of_range": "Outside the protocol's age window.",
    "lab_or_imaging_abnormality": "Abnormal lab or imaging value at screening.",
    "prior_treatment": "Prior or current disallowed treatment.",
    "pregnancy": "Pregnant or refusing acceptable contraception.",
    "declined_consent": "Declined to provide informed consent.",
    "inclusion_criteria_not_met": "Inclusion criteria not met (other).",
    "exclusion_criteria_met": "Exclusion criteria met (other).",
    "withdrawn": "Withdrew or did not return for screening.",
    "other": "Other (specify in `exclusion_reason_text`).",
}


# ── Age bands (NIH-style) ───────────────────────────────────────────────


AGE_BANDS: Final[tuple[str, ...]] = (
    "<18",
    "18-29",
    "30-44",
    "45-64",
    "65-74",
    "75+",
)


# ── OMB-1997 categoricals ───────────────────────────────────────────────


SEX_VALUES: Final[tuple[str, ...]] = ("M", "F", "other", "unknown")


RACE_VALUES: Final[tuple[str, ...]] = (
    "american_indian",
    "asian",
    "black",
    "native_hawaiian",
    "white",
    "multiracial",
    "other",
    "unknown",
)


ETHNICITY_VALUES: Final[tuple[str, ...]] = (
    "hispanic_or_latino",
    "not_hispanic_or_latino",
    "unknown",
)


# ── Status enums (also surfaced in OpenAPI / frontend dropdowns) ────────


ELIGIBILITY_STATUSES: Final[tuple[str, ...]] = (
    "pending",
    "eligible",
    "screen_failure",
)
CONSENT_STATUSES: Final[tuple[str, ...]] = (
    "pending",
    "consented",
    "declined",
    "withdrew",
)
ENROLMENT_STATUSES: Final[tuple[str, ...]] = (
    "pending",
    "enrolled",
    "not_enrolled",
)


__all__ = [
    "AGE_BANDS",
    "CONSENT_STATUSES",
    "CONSORT_EXCLUSION_REASONS",
    "ELIGIBILITY_STATUSES",
    "ENROLMENT_STATUSES",
    "ETHNICITY_VALUES",
    "RACE_VALUES",
    "SEX_VALUES",
]
