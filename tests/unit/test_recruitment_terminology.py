"""Recruitment terminology codebook integrity tests (P1 #3)."""

from __future__ import annotations

from research_assistant.persistence.clinical.recruitment_terminology import (
    AGE_BANDS,
    CONSENT_STATUSES,
    CONSORT_EXCLUSION_REASONS,
    ELIGIBILITY_STATUSES,
    ENROLMENT_STATUSES,
    ETHNICITY_VALUES,
    RACE_VALUES,
    SEX_VALUES,
)


def test_consort_codebook_carries_eight_canonical_reasons_plus_other() -> None:
    """8 canonical CONSORT reasons + 'other' free-text bucket = 9 entries."""
    assert "other" in CONSORT_EXCLUSION_REASONS
    assert len(CONSORT_EXCLUSION_REASONS) == 9
    # Spot-check the four most common ones.
    for code in (
        "age_out_of_range",
        "inclusion_criteria_not_met",
        "exclusion_criteria_met",
        "declined_consent",
    ):
        assert code in CONSORT_EXCLUSION_REASONS


def test_status_enums_share_pending_default() -> None:
    for enum in (ELIGIBILITY_STATUSES, CONSENT_STATUSES, ENROLMENT_STATUSES):
        assert "pending" in enum
        assert enum[0] == "pending"  # 'pending' is the documented default


def test_eligibility_status_has_screen_failure_terminal() -> None:
    assert "eligible" in ELIGIBILITY_STATUSES
    assert "screen_failure" in ELIGIBILITY_STATUSES


def test_consent_status_separates_declined_from_withdrew() -> None:
    """A declined consent (refused) is operationally distinct from a
    withdrew (consented then changed mind) — both must be representable."""
    assert "declined" in CONSENT_STATUSES
    assert "withdrew" in CONSENT_STATUSES


def test_age_bands_cover_zero_to_75plus_with_pediatric_marker() -> None:
    assert AGE_BANDS[0] == "<18"
    assert AGE_BANDS[-1] == "75+"
    # Each band should be a single non-overlapping range (no '15-25' overlap).
    assert len(set(AGE_BANDS)) == len(AGE_BANDS)


def test_sex_values_include_other_and_unknown() -> None:
    """OMB-1997 + clinical-data norm: M/F + 'other' + 'unknown' for
    intersex / unspecified / data-missing cases."""
    assert set(SEX_VALUES) >= {"M", "F", "other", "unknown"}


def test_race_values_are_omb_1997_compatible() -> None:
    """The 5 OMB categories + multiracial / other / unknown."""
    for needed in (
        "american_indian",
        "asian",
        "black",
        "native_hawaiian",
        "white",
        "unknown",
    ):
        assert needed in RACE_VALUES


def test_ethnicity_values_match_omb_two_category_split() -> None:
    assert set(ETHNICITY_VALUES) == {
        "hispanic_or_latino",
        "not_hispanic_or_latino",
        "unknown",
    }
