"""ADaM ADSL derivation — flags + age binning + death tracing."""

from __future__ import annotations

from research_assistant.cdisc.adam_deriver import _age_group, derive_adsl
from research_assistant.persistence.clinical.models import (
    SdtmAe,
    SdtmDm,
)


def _dm(**overrides: object) -> SdtmDm:
    return SdtmDm(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="DM",
        USUBJID="RS-1-S-001",
        SUBJID="S-001",
        AGE=42,
        AGEU="YEARS",
        SEX="F",
        RACE="WHITE",
        **overrides,  # type: ignore[arg-type]
    )


def _ae(**overrides: object) -> SdtmAe:
    base = dict(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="AE",
        USUBJID="RS-1-S-001",
        AESEQ=1,
        AETERM="headache",
        AESEV="MILD",
        AESER="N",
        AEOUT="RECOVERING/RESOLVING",
    )
    base.update(overrides)
    return SdtmAe(**base)  # type: ignore[arg-type]


# ── _age_group bins ─────────────────────────────────────────────────────


def test_age_group_bins_pediatric() -> None:
    assert _age_group(8) == "<18"


def test_age_group_bins_adult() -> None:
    assert _age_group(40) == "18-64"
    assert _age_group(18) == "18-64"
    assert _age_group(64) == "18-64"


def test_age_group_bins_elderly() -> None:
    assert _age_group(65) == "65-74"
    assert _age_group(74) == "65-74"
    assert _age_group(75) == ">=75"
    assert _age_group(90) == ">=75"


def test_age_group_none_passes_through() -> None:
    assert _age_group(None) is None


# ── ADSL derivation ─────────────────────────────────────────────────────


def test_adsl_default_safety_flag_n_when_no_item_data() -> None:
    """No captured items → SAFFL='N' (subject didn't take the intervention
    in practice). ITTFL is always Y for randomised subjects."""
    [adsl] = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm()],
        ae_records=[],
        subject_item_data={},
    )
    assert adsl.SAFFL == "N"
    assert adsl.ITTFL == "Y"
    assert adsl.DTHFL == "N"
    assert adsl.AGEGR1 == "18-64"


def test_adsl_safety_flag_y_when_subject_has_item_data() -> None:
    """Any non-empty captured item → SAFFL='Y' (took at least one dose)."""
    from types import SimpleNamespace

    items = [SimpleNamespace(value="42")]
    [adsl] = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm()],
        ae_records=[],
        subject_item_data={"S-001": items},
    )
    assert adsl.SAFFL == "Y"


def test_adsl_death_flag_traces_to_fatal_ae() -> None:
    [adsl] = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm()],
        ae_records=[_ae(AEOUT="FATAL")],
    )
    assert adsl.DTHFL == "Y"


def test_adsl_treatment_placeholder_when_arm_missing() -> None:
    """Until randomisation/IRT lands, ARM stays None and we surface 'TBD'
    rather than fabricating a treatment label."""
    [adsl] = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm()],
        ae_records=[],
    )
    assert adsl.TRT01P == "TBD"
    assert adsl.TRT01A == "TBD"


def test_adsl_treatment_propagates_when_arm_set() -> None:
    [adsl] = derive_adsl(
        deployment_id="dep-1",
        study_id="RS-1",
        dm_records=[_dm(ARM="PLACEBO")],
        ae_records=[],
    )
    assert adsl.TRT01P == "PLACEBO"
    assert adsl.TRT01A == "PLACEBO"
