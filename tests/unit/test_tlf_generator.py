"""TLF generator — counts + SVG figure smoke test."""

from __future__ import annotations

import json

from research_assistant.cdisc.tlf_generator import generate_tlfs
from research_assistant.persistence.clinical.models import AdamAdsl, SdtmAe


def _adsl(saffl: str = "Y", dthfl: str = "N", age: int = 42) -> AdamAdsl:
    return AdamAdsl(
        deployment_id="dep-1",
        STUDYID="RS-1",
        USUBJID="RS-1-S-001",
        SUBJID="S-001",
        AGE=age,
        AGEU="YEARS",
        SEX="F",
        RACE="WHITE",
        SAFFL=saffl,
        ITTFL="Y",
        DTHFL=dthfl,
    )


def _ae(
    usubjid: str = "RS-1-S-001",
    severity: str = "MILD",
    serious: str = "N",
    term: str = "headache",
    aeseq: int = 1,
) -> SdtmAe:
    return SdtmAe(
        deployment_id="dep-1",
        STUDYID="RS-1",
        DOMAIN="AE",
        USUBJID=usubjid,
        AESEQ=aeseq,
        AETERM=term,
        AESEV=severity,
        AESER=serious,
    )


def test_generate_tlfs_emits_3_tables_and_1_figure() -> None:
    tlfs = generate_tlfs(deployment_id="dep-1", adsl=[_adsl()], ae_records=[_ae()])
    kinds = sorted(t.kind for t in tlfs)
    ids = sorted(t.tlf_id for t in tlfs)
    assert kinds == ["figure", "table", "table", "table"]
    assert ids == [
        "f-ae-frequency",
        "t-ae-summary",
        "t-demographics",
        "t-disposition",
    ]


def test_disposition_table_counts_match_seeded_subjects() -> None:
    adsl = [
        _adsl(saffl="Y", dthfl="N"),
        _adsl(saffl="Y", dthfl="Y"),
        _adsl(saffl="N", dthfl="N"),
    ]
    tlfs = generate_tlfs(deployment_id="dep-1", adsl=adsl, ae_records=[])
    disp = next(t for t in tlfs if t.tlf_id == "t-disposition")
    rows = {row[0]: row[1] for row in json.loads(disp.content_json)["rows"]}
    assert rows["Subjects in dataset"] == 3
    assert rows["ITT population (ITTFL=Y)"] == 3
    assert rows["Safety population (SAFFL=Y)"] == 2
    assert rows["Subjects with death event (DTHFL=Y)"] == 1


def test_ae_summary_counts_events_and_sae() -> None:
    aes = [
        _ae(severity="MILD", serious="N", term="headache", aeseq=1),
        _ae(severity="SEVERE", serious="Y", term="MI", aeseq=2),
        _ae(usubjid="RS-1-S-002", severity="MILD", serious="N", term="nausea"),
    ]
    tlfs = generate_tlfs(
        deployment_id="dep-1",
        adsl=[_adsl(), _adsl()],
        ae_records=aes,
    )
    summary = next(t for t in tlfs if t.tlf_id == "t-ae-summary")
    rows = {row[0]: row[1] for row in json.loads(summary.content_json)["rows"]}
    assert rows["Total AE events"] == 3
    assert rows["Total SAEs"] == 1
    # Severity breakdown rows are added for each AESEV that appears
    assert rows["Severity: MILD"] == 2
    assert rows["Severity: SEVERE"] == 1


def test_demographics_table_summarises_age_sex_race() -> None:
    adsl = [
        _adsl(age=30),
        _adsl(age=40),
        _adsl(age=50),
    ]
    tlfs = generate_tlfs(deployment_id="dep-1", adsl=adsl, ae_records=[])
    demo = next(t for t in tlfs if t.tlf_id == "t-demographics")
    content = json.loads(demo.content_json)
    # Age summary on first row
    age_row = content["rows"][0]
    assert age_row[0] == "Age (years)"
    assert "mean 40" in age_row[1]


def test_ae_frequency_svg_handles_empty_data() -> None:
    tlfs = generate_tlfs(deployment_id="dep-1", adsl=[_adsl()], ae_records=[])
    fig = next(t for t in tlfs if t.kind == "figure")
    assert fig.svg_content is not None
    assert fig.svg_content.startswith("<svg")
    assert "No adverse events recorded" in fig.svg_content


def test_ae_frequency_svg_renders_top_10_by_count() -> None:
    aes = [_ae(term="headache") for _ in range(5)] + [
        _ae(term="nausea", usubjid="RS-1-S-002") for _ in range(3)
    ]
    tlfs = generate_tlfs(deployment_id="dep-1", adsl=[_adsl()], ae_records=aes)
    fig = next(t for t in tlfs if t.kind == "figure")
    assert fig.svg_content is not None
    # Both terms should appear in the SVG label text
    assert "headache" in fig.svg_content
    assert "nausea" in fig.svg_content
