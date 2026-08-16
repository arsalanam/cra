"""Single source of truth for CDISC dataset + column metadata.

Both the XPT v5 writer and the Define-XML v2.1 generator consume this
registry. Keep the per-domain `_COLUMNS` lists in `exporter.py` aligned
with the column order here — the existing list-of-strings stays for
backward compatibility but the type / length / label metadata only
lives here.

The registry intentionally hand-codes lengths (sized to comfortably
fit captured values for the platform-MVP scope). For deployment with
unusually long subject codes or treatment labels, override at the
DatasetMeta level when generating the Define-XML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ── Codelist OIDs referenced by the Define-XML <CodeListRef> nodes ──────
#
# These are deliberately not tied to a specific registry version — the
# Define-XML generator declares each codelist explicitly, so the OIDs
# below are just internal anchors used for cross-references.

CL_SEX = "CL.SEX"
CL_RACE = "CL.RACE"
CL_NY = "CL.NY"  # Y/N flag (SAFFL/ITTFL/DTHFL/MHONGO/AESER)
CL_AESEV = "CL.AESEV"
CL_AEOUT = "CL.AEOUT"
CL_AEREL = "CL.AEREL"
CL_VSTESTCD = "CL.VSTESTCD"
CL_LBTESTCD = "CL.LBTESTCD"
CL_EXROUTE = "CL.EXROUTE"
CL_MHCAT = "CL.MHCAT"
CL_CNSR = "CL.CNSR"  # ADTTE CNSR (0/1)
CL_AVALU = "CL.AVALU"  # ADTTE AVALU
CL_PARAMCD_ADTTE = "CL.PARAMCD.ADTTE"


# ── Method OIDs (derivations described in <MethodDef>) ──────────────────

M_AGEGR1 = "M.AGEGR1"
M_LBNRIND = "M.LBNRIND"
M_MHONGO = "M.MHONGO"
M_SAFFL = "M.SAFFL"
M_DTHFL = "M.DTHFL"
M_ADTTE_AVAL = "M.ADTTE.AVAL"
M_ADTTE_CNSR = "M.ADTTE.CNSR"


XptType = Literal["CHAR", "NUM"]


@dataclass(frozen=True)
class ColumnMeta:
    name: str
    type: XptType
    length: int = 200  # CHAR length in bytes (NUM is always 8 in XPT v5)
    label: str = ""
    codelist_oid: str | None = None
    method_oid: str | None = None
    mandatory: bool = False


@dataclass(frozen=True)
class DatasetMeta:
    name: str  # 8 chars max per SDTM IG (DM, AE, VS, …, ADSL, ADTTE)
    label: str
    structure: str  # e.g. "One record per subject"
    purpose: Literal["Tabulation", "Analysis"]
    klass: str = ""  # e.g. "SPECIAL PURPOSE", "EVENTS", "FINDINGS", "INTERVENTIONS"
    key_vars: tuple[str, ...] = ()
    columns: tuple[ColumnMeta, ...] = field(default_factory=tuple)


# ── SDTM ────────────────────────────────────────────────────────────────


_DM = DatasetMeta(
    name="DM",
    label="Demographics",
    structure="One record per subject",
    purpose="Tabulation",
    klass="SPECIAL PURPOSE",
    key_vars=("STUDYID", "USUBJID"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("SUBJID", "CHAR", 20, "Subject Identifier for the Study"),
        ColumnMeta("SITEID", "CHAR", 40, "Study Site Identifier"),
        ColumnMeta("AGE", "NUM", 8, "Age in AGEU"),
        ColumnMeta("AGEU", "CHAR", 10, "Age Units"),
        ColumnMeta("SEX", "CHAR", 2, "Sex", codelist_oid=CL_SEX),
        ColumnMeta("RACE", "CHAR", 60, "Race", codelist_oid=CL_RACE),
        ColumnMeta("ETHNIC", "CHAR", 60, "Ethnicity"),
        ColumnMeta("RFSTDTC", "CHAR", 30, "Subject Reference Start Date/Time"),
        ColumnMeta("RFENDTC", "CHAR", 30, "Subject Reference End Date/Time"),
        ColumnMeta("ARM", "CHAR", 60, "Description of Planned Arm"),
        ColumnMeta("ARMCD", "CHAR", 20, "Planned Arm Code"),
        ColumnMeta("COUNTRY", "CHAR", 6, "Country"),
    ),
)

_AE = DatasetMeta(
    name="AE",
    label="Adverse Events",
    structure="One record per adverse event",
    purpose="Tabulation",
    klass="EVENTS",
    key_vars=("STUDYID", "USUBJID", "AESEQ"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("AESEQ", "NUM", 8, "Sequence Number", mandatory=True),
        ColumnMeta("AETERM", "CHAR", 200, "Reported Term for the Adverse Event"),
        ColumnMeta("AEDECOD", "CHAR", 200, "Dictionary-Derived Term"),
        ColumnMeta("AEBODSYS", "CHAR", 200, "Body System or Organ Class"),
        ColumnMeta("AESTDTC", "CHAR", 30, "Start Date/Time of Adverse Event"),
        ColumnMeta("AEENDTC", "CHAR", 30, "End Date/Time of Adverse Event"),
        ColumnMeta("AESEV", "CHAR", 20, "Severity/Intensity", codelist_oid=CL_AESEV),
        ColumnMeta("AESER", "CHAR", 1, "Serious Event", codelist_oid=CL_NY),
        ColumnMeta("AEREL", "CHAR", 40, "Causality", codelist_oid=CL_AEREL),
        ColumnMeta("AEOUT", "CHAR", 40, "Outcome of Adverse Event", codelist_oid=CL_AEOUT),
    ),
)

_VS = DatasetMeta(
    name="VS",
    label="Vital Signs",
    structure="One record per vital-sign measurement",
    purpose="Tabulation",
    klass="FINDINGS",
    key_vars=("STUDYID", "USUBJID", "VSSEQ"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("VSSEQ", "NUM", 8, "Sequence Number", mandatory=True),
        ColumnMeta("VSTESTCD", "CHAR", 8, "Vital Signs Test Short Name", codelist_oid=CL_VSTESTCD),
        ColumnMeta("VSTEST", "CHAR", 40, "Vital Signs Test Name"),
        ColumnMeta("VSORRES", "CHAR", 60, "Result or Finding in Original Units"),
        ColumnMeta("VSORRESU", "CHAR", 20, "Original Units"),
        ColumnMeta("VSDTC", "CHAR", 30, "Date/Time of Measurement"),
    ),
)

_LB = DatasetMeta(
    name="LB",
    label="Laboratory Test Results",
    structure="One record per lab measurement",
    purpose="Tabulation",
    klass="FINDINGS",
    key_vars=("STUDYID", "USUBJID", "LBSEQ"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("LBSEQ", "NUM", 8, "Sequence Number", mandatory=True),
        ColumnMeta("LBTESTCD", "CHAR", 8, "Lab Test Short Name", codelist_oid=CL_LBTESTCD),
        ColumnMeta("LBTEST", "CHAR", 40, "Lab Test Name"),
        ColumnMeta("LBORRES", "CHAR", 60, "Result or Finding in Original Units"),
        ColumnMeta("LBORRESU", "CHAR", 20, "Original Units"),
        ColumnMeta("LBSTRESC", "CHAR", 60, "Standardised Result in Character Form"),
        ColumnMeta("LBSTRESN", "NUM", 8, "Standardised Result in Numeric Form"),
        ColumnMeta("LBSTRESU", "CHAR", 20, "Standardised Units"),
        ColumnMeta("LBORNRLO", "CHAR", 60, "Reference Range Lower Limit in Original Units"),
        ColumnMeta("LBORNRHI", "CHAR", 60, "Reference Range Upper Limit in Original Units"),
        ColumnMeta("LBSTNRLO", "NUM", 8, "Reference Range Lower Limit, Std Units"),
        ColumnMeta("LBSTNRHI", "NUM", 8, "Reference Range Upper Limit, Std Units"),
        ColumnMeta(
            "LBNRIND",
            "CHAR",
            20,
            "Reference Range Indicator (NORMAL/LOW/HIGH)",
            method_oid=M_LBNRIND,
        ),
        ColumnMeta("LBDTC", "CHAR", 30, "Date/Time of Specimen Collection"),
    ),
)

_EX = DatasetMeta(
    name="EX",
    label="Exposure",
    structure="One record per dose administration",
    purpose="Tabulation",
    klass="INTERVENTIONS",
    key_vars=("STUDYID", "USUBJID", "EXSEQ"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("EXSEQ", "NUM", 8, "Sequence Number", mandatory=True),
        ColumnMeta("EXTRT", "CHAR", 200, "Name of Treatment"),
        ColumnMeta("EXDOSE", "NUM", 8, "Dose per Administration"),
        ColumnMeta("EXDOSU", "CHAR", 20, "Dose Units"),
        ColumnMeta("EXROUTE", "CHAR", 40, "Route of Administration", codelist_oid=CL_EXROUTE),
        ColumnMeta("EXSTDTC", "CHAR", 30, "Start Date/Time of Treatment"),
        ColumnMeta("EXENDTC", "CHAR", 30, "End Date/Time of Treatment"),
    ),
)

_CM = DatasetMeta(
    name="CM",
    label="Concomitant Medications",
    structure="One record per concomitant medication",
    purpose="Tabulation",
    klass="INTERVENTIONS",
    key_vars=("STUDYID", "USUBJID", "CMSEQ"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("CMSEQ", "NUM", 8, "Sequence Number", mandatory=True),
        ColumnMeta("CMTRT", "CHAR", 200, "Reported Name of Drug, Med, or Therapy"),
        ColumnMeta("CMDECOD", "CHAR", 200, "Standardised Medication Name"),
        ColumnMeta("CMINDC", "CHAR", 200, "Indication"),
        ColumnMeta("CMDOSE", "NUM", 8, "Dose per Administration"),
        ColumnMeta("CMDOSU", "CHAR", 20, "Dose Units"),
        ColumnMeta("CMSTDTC", "CHAR", 30, "Start Date/Time of Medication"),
        ColumnMeta("CMENDTC", "CHAR", 30, "End Date/Time of Medication"),
    ),
)

_MH = DatasetMeta(
    name="MH",
    label="Medical History",
    structure="One record per condition or event",
    purpose="Tabulation",
    klass="EVENTS",
    key_vars=("STUDYID", "USUBJID", "MHSEQ"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("MHSEQ", "NUM", 8, "Sequence Number", mandatory=True),
        ColumnMeta("MHTERM", "CHAR", 200, "Reported Term for the Medical History"),
        ColumnMeta("MHDECOD", "CHAR", 200, "Dictionary-Derived Term"),
        ColumnMeta("MHCAT", "CHAR", 100, "Category", codelist_oid=CL_MHCAT),
        ColumnMeta("MHSTDTC", "CHAR", 30, "Start Date/Time of Medical History"),
        ColumnMeta("MHENDTC", "CHAR", 30, "End Date/Time of Medical History"),
        ColumnMeta(
            "MHONGO",
            "CHAR",
            1,
            "Ongoing Event",
            codelist_oid=CL_NY,
            method_oid=M_MHONGO,
        ),
    ),
)

_DA = DatasetMeta(
    name="DA",
    label="Drug Accountability",
    structure="One record per accountability finding per subject",
    purpose="Tabulation",
    klass="FINDINGS",
    key_vars=("STUDYID", "USUBJID", "DASEQ"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("DASEQ", "NUM", 8, "Sequence Number", mandatory=True),
        ColumnMeta("DAREFID", "CHAR", 40, "Reference ID"),
        ColumnMeta("DATESTCD", "CHAR", 8, "Drug Accountability Test Short Name"),
        ColumnMeta("DATEST", "CHAR", 40, "Drug Accountability Test Name"),
        ColumnMeta("DAORRES", "CHAR", 20, "Result or Finding as Collected"),
        ColumnMeta("DAORRESU", "CHAR", 20, "Original Units"),
        ColumnMeta("DASTRESN", "NUM", 8, "Numeric Result/Finding in Standard Units"),
        ColumnMeta("DASTRESU", "CHAR", 20, "Standard Units"),
        ColumnMeta("DADTC", "CHAR", 30, "Date/Time of Collection"),
    ),
)

_SV = DatasetMeta(
    name="SV",
    label="Subject Visits",
    structure="One record per subject per visit",
    purpose="Tabulation",
    klass="SPECIAL PURPOSE",
    key_vars=("STUDYID", "USUBJID", "SVSEQ"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("SVSEQ", "NUM", 8, "Sequence Number", mandatory=True),
        ColumnMeta("VISITNUM", "NUM", 8, "Visit Number"),
        ColumnMeta("VISIT", "CHAR", 60, "Visit Name"),
        ColumnMeta("SVSTDTC", "CHAR", 30, "Start Date/Time of Visit"),
        ColumnMeta("SVENDTC", "CHAR", 30, "End Date/Time of Visit"),
    ),
)

_DS = DatasetMeta(
    name="DS",
    label="Disposition",
    structure="One record per subject per disposition event",
    purpose="Tabulation",
    klass="EVENTS",
    key_vars=("STUDYID", "USUBJID", "DSSEQ"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("DOMAIN", "CHAR", 2, "Domain Abbreviation", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("DSSEQ", "NUM", 8, "Sequence Number", mandatory=True),
        ColumnMeta("DSTERM", "CHAR", 200, "Reported Term for the Disposition Event"),
        ColumnMeta("DSDECOD", "CHAR", 60, "Standardized Disposition Term"),
        ColumnMeta("DSCAT", "CHAR", 40, "Category for Disposition Event"),
        ColumnMeta("DSSTDTC", "CHAR", 30, "Start Date/Time of Disposition Event"),
    ),
)

_ADSL = DatasetMeta(
    name="ADSL",
    label="Subject-Level Analysis Dataset",
    structure="One record per subject",
    purpose="Analysis",
    klass="ADaM",
    key_vars=("STUDYID", "USUBJID"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta("SUBJID", "CHAR", 20, "Subject Identifier for the Study"),
        ColumnMeta("SITEID", "CHAR", 40, "Study Site Identifier"),
        ColumnMeta("AGE", "NUM", 8, "Age"),
        ColumnMeta("AGEU", "CHAR", 10, "Age Units"),
        ColumnMeta("AGEGR1", "CHAR", 10, "Age Group 1", method_oid=M_AGEGR1),
        ColumnMeta("SEX", "CHAR", 2, "Sex", codelist_oid=CL_SEX),
        ColumnMeta("RACE", "CHAR", 60, "Race", codelist_oid=CL_RACE),
        ColumnMeta("ETHNIC", "CHAR", 60, "Ethnicity"),
        ColumnMeta(
            "SAFFL",
            "CHAR",
            1,
            "Safety Population Flag",
            codelist_oid=CL_NY,
            method_oid=M_SAFFL,
        ),
        ColumnMeta("ITTFL", "CHAR", 1, "Intent-to-Treat Population Flag", codelist_oid=CL_NY),
        ColumnMeta("DTHFL", "CHAR", 1, "Death Flag", codelist_oid=CL_NY, method_oid=M_DTHFL),
        ColumnMeta("RFSTDTC", "CHAR", 30, "Subject Reference Start Date/Time"),
        ColumnMeta("RFENDTC", "CHAR", 30, "Subject Reference End Date/Time"),
        ColumnMeta("TRT01P", "CHAR", 60, "Planned Treatment for Period 1"),
        ColumnMeta("TRT01A", "CHAR", 60, "Actual Treatment for Period 1"),
        ColumnMeta("COUNTRY", "CHAR", 6, "Country"),
    ),
)

_ADTTE = DatasetMeta(
    name="ADTTE",
    label="Time-to-Event Analysis Dataset",
    structure="One record per subject per parameter",
    purpose="Analysis",
    klass="ADaM",
    key_vars=("STUDYID", "USUBJID", "PARAMCD"),
    columns=(
        ColumnMeta("STUDYID", "CHAR", 20, "Study Identifier", mandatory=True),
        ColumnMeta("USUBJID", "CHAR", 40, "Unique Subject Identifier", mandatory=True),
        ColumnMeta(
            "PARAMCD",
            "CHAR",
            8,
            "Parameter Code",
            codelist_oid=CL_PARAMCD_ADTTE,
            mandatory=True,
        ),
        ColumnMeta("PARAM", "CHAR", 60, "Parameter Description"),
        ColumnMeta("AVAL", "NUM", 8, "Analysis Value", method_oid=M_ADTTE_AVAL),
        ColumnMeta("AVALU", "CHAR", 10, "Analysis Value Unit", codelist_oid=CL_AVALU),
        ColumnMeta(
            "CNSR",
            "NUM",
            8,
            "Censor (0=event, 1=censored)",
            codelist_oid=CL_CNSR,
            method_oid=M_ADTTE_CNSR,
        ),
        ColumnMeta("STARTDT", "CHAR", 30, "Analysis Start Date"),
        ColumnMeta("ADT", "CHAR", 30, "Analysis Date"),
        ColumnMeta("EVNTDESC", "CHAR", 200, "Event Description"),
        ColumnMeta("SRCDOM", "CHAR", 8, "Source Domain"),
        ColumnMeta("SRCVAR", "CHAR", 20, "Source Variable"),
        ColumnMeta("TRT01P", "CHAR", 60, "Planned Treatment for Period 1"),
        ColumnMeta("TRT01A", "CHAR", 60, "Actual Treatment for Period 1"),
    ),
)


DATASETS: tuple[DatasetMeta, ...] = (
    _DM,
    _AE,
    _VS,
    _LB,
    _EX,
    _CM,
    _MH,
    _DA,
    _SV,
    _DS,
    _ADSL,
    _ADTTE,
)
DOMAIN_METADATA: dict[str, DatasetMeta] = {d.name: d for d in DATASETS}


__all__ = [
    "CL_AEOUT",
    "CL_AEREL",
    "CL_AESEV",
    "CL_AVALU",
    "CL_CNSR",
    "CL_EXROUTE",
    "CL_LBTESTCD",
    "CL_MHCAT",
    "CL_NY",
    "CL_PARAMCD_ADTTE",
    "CL_RACE",
    "CL_SEX",
    "CL_VSTESTCD",
    "DATASETS",
    "DOMAIN_METADATA",
    "M_ADTTE_AVAL",
    "M_ADTTE_CNSR",
    "M_AGEGR1",
    "M_DTHFL",
    "M_LBNRIND",
    "M_MHONGO",
    "M_SAFFL",
    "ColumnMeta",
    "DatasetMeta",
]
