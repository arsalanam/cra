# CDISC submission readiness

**Question this doc answers:** if a common drug-intervention trial is run through
CRA, can someone generate the CDISC submission datasets and reports end-to-end?

**Bottom line — yes for the core, not a turnkey full submission.** From captured
eCRF data, one derive run produces a genuine, reviewable CDISC package: 10 SDTM
domains, 6 ADaM datasets (ADSL, ADAE, ADCM, ADLB, ADVS, ADTTE), a
Define-XML v2.1 metadata file, SAS Transport (XPT v5) datasets, a starter
table/figure set, and an ICH E3 clinical study report — assembled into one
submission-bundle ZIP. It is **not** yet a complete FDA/EMA eCTD Module 5
package: several standard SDTM & ADaM datasets, the reviewer guides
(SDRG / ADRG / annotated CRF), and — critically — **MedDRA / WHODrug medical
coding** are outstanding. Coding is license-gated, not an engineering gap.

> A rendered, shareable version of this matrix lives at
> [`cdisc-readiness.html`](cdisc-readiness.html). **This markdown file is the
> canonical, version-controlled source of truth**; keep the two in step when the
> `cdisc/` package changes.

Scope of the assessment: a **parallel-group randomized drug trial** (safety +
time-to-event), judged against the CDISC SDTM IG / ADaM IG expectation for a
typical submission. Grounded in the current `src/research_assistant/cdisc/`
package (branch `feat/backlog-quick-wins`). Status is *coverage-of-standard*,
not a regulatory clearance.

**Status key:** ✅ Produced (derived & in the bundle) · 🟡 Partial (produced with
a documented caveat) · ⬜ Not yet (buildable, on the backlog) · ⛔ Blocked
(external license or multi-quarter build).

**How it is generated:** `POST /api/edc/deployments/{id}/cdisc/derive` runs the
pipeline (SDTM → ADaM → TLF) in one transaction and persists every output; the
submission-bundle ZIP contains `define.xml` + `sdtm/*.xpt` + `adam/*.xpt` +
`tlf/*`. Survival TLFs render via `/cdisc/survival/render`.

---

## SDTM domains

Study Data Tabulation Model — the standardized raw-data tables.

| Status | Domain | Dataset | Notes |
|---|---|---|---|
| ✅ | `DM` | Demographics | Age, sex, race, arm, reference dates. Full. |
| 🟡 | `AE` | Adverse Events | Verbatim + seriousness + relationship. `AEDECOD` / `AEBODSYS` free-text — MedDRA coding blocked. |
| ✅ | `EX` | Exposure | Dosing / administration records with routes. |
| 🟡 | `CM` | Concomitant Meds | Captured & mapped. `CMDECOD` free-text — WHODrug coding blocked. |
| ✅ | `LB` | Laboratory | Results + normal-range indicator; standardised columns (`LBSTRESN`/`LBSTRESU` + `LBSTNRLO`/`HI`) unit-converted (US-conventional → SI) per analyte via `lab_units`. |
| ✅ | `VS` | Vital Signs | Height, weight, BP, pulse, temperature. |
| 🟡 | `MH` | Medical History | Captured & mapped. SOC placeholder until MedDRA. |
| ✅ | `DA` | Drug Accountability | Dispense/return amounts as `DISPAMT`/`RETURNED` findings, kit id as `DAREFID`; derived from the IP dispense/return records. In Define-XML + XPT + the bundle. |
| 🟡 | `DS` | Disposition | One disposition event per subject, `DSDECOD` mapped from `Subject.status` (ONGOING/COMPLETED). Minimal — a per-subject disposition lifecycle (withdrawals, exit dates, screen-failure reasons) isn't captured yet. In Define-XML + XPT + the bundle. |
| ✅ | `SV` | Subject Visits | One record per completed visit from the planned-visit calendar; VISIT/VISITNUM from the schedule, dated at the actual completion. In Define-XML + XPT + the bundle. |
| ⬜ | `SE` | Subject Elements | Study-element timing; needs the SE mapper. |
| ⬜ | `QS` | Questionnaires | ePRO subsystem could feed QS; mapper not built. |
| ⬜ | `EG` | ECG | Only if the trial collects ECG (capture + mapper). |
| ⬜ | `TA·TE·TV·TI·TS` | Trial Design | Small static domains required for submission; not yet emitted. |

## ADaM datasets

Analysis Data Model — SDTM plus analysis-ready derived variables.

| Status | Dataset | Name | Notes |
|---|---|---|---|
| ✅ | `ADSL` | Subject-Level | One row/subject; population flags (SAFFL / ITTFL / DTHFL), age groups, treatment, reference dates. |
| ✅ | `ADTTE` | Time-to-Event | Time to first AE / SAE / death; AVAL + CNSR with SRCDOM/SRCVAR traceability. |
| ✅ | `ADAE` | Adverse Events | One row per AE + ADSL treatment/population; `TRTEMFL` (treatment-emergent) + `AOCCFL` (first occurrence) flags. In Define-XML + XPT + bundle. |
| ✅ | `ADLB` / `ADVS` | Labs / Vitals (BDS) | One row per result with the BDS baseline/change value-add (`ABLFL` / `BASE` / `CHG`). |
| ✅ | `ADCM` | Concomitant Meds | Con-meds merged with ADSL treatment/population (OCCDS). |
| ⬜ | `ADQS` | Questionnaires (BDS) | Blocked on SDTM `QS` (no ePRO questionnaire-response model yet). |

## Submission artifacts

The metadata, transport, and coding layer a reviewer expects around the datasets.

| Status | Artifact | Notes |
|---|---|---|
| ✅ | Define-XML v2.1 | One ItemGroupDef per dataset + CodeLists + Methods — the machine-readable data dictionary regulators preflight. |
| ✅ | SAS Transport (XPT v5) | Hand-rolled writer — the CDISC IG default transport format, one per dataset. |
| ✅ | Per-dataset CSV | Human-readable companion export for every SDTM / ADaM dataset. |
| ✅ | Submission-bundle ZIP | `define.xml` + `sdtm/*.xpt` + `adam/*.xpt` + `tlf/*` in one download. |
| 🟡 | Controlled Terminology | SDTM CT applied (sex, severity, outcome, routes, …). **MedDRA + WHODrug coding not applied.** |
| ⛔ | MedDRA / WHODrug coding | Licensed dictionaries (MSSO / Uppsala). AE/MH/CM terms stay verbatim until the license drops in at deploy. |
| ⬜ | Reviewer guides (SDRG / ADRG) | The Study & Analysis Data Reviewer's Guides expected in an FDA package. |
| ⬜ | Annotated CRF (aCRF) | The blank CRF annotated with SDTM variable origins. |
| ⬜ | eCTD Module 5 packaging | Folder structure + gateway submission is the operator's step. |

## Tables, Listings & Figures

| Status | Output | Notes |
|---|---|---|
| ✅ | Table 1 — Subject Disposition | From ADSL population flags. |
| ✅ | Table 2 — Baseline Demographics | Age groups, sex, race by arm. |
| ✅ | Table 3 — Adverse Event Summary | AE / SAE counts and rates. |
| ✅ | Figure — AE Frequency | SVG, embedded in the bundle. |
| 🟡 | Efficacy / survival TLFs | The trial-stats specialist renders K-M, Cox, subgroup forest & waterfall in the sandbox — analysis-driven, not yet a fixed regulatory shell. |
| ⬜ | Full TLF shell | Lab shift tables, exposure listings, the complete efficacy/safety table set. |

## Regulatory reports & documents

The narrative deliverables the platform drafts alongside the datasets.

| Status | Document | Notes |
|---|---|---|
| ✅ | CSR — ICH E3 | Synopsis + data sections, every count carrying a derived-from trace. PDF / DOCX. |
| ✅ | SAP | Statistical analysis plan drafter (PICOT → SAP), with sample-size + adaptive-design tooling. |
| ✅ | FDA 3500A / IND safety | Per-event MedWatch draft from the safety subsystem. |
| ✅ | Protocol · IRB · Registration | IRB/ethics packet, ICF, and CT.gov / CTIS registration drafts. |
| ✅ | Validation pack | IQ / OQ / PQ + requirements-traceability matrix for the system itself. |
| ⬜ | CSR narrative chapters | Introduction / Discussion / Conclusions ship as operator-to-complete placeholders. |

---

## Path to a complete submission

Most "Not yet" rows are backlog-buildable, and the data usually **already exists**
in the platform — the gap is the mapper/deriver, not the capture. The two hard
limits are MedDRA/WHODrug (paid license) and the reviewer-guide / eCTD packaging
that is inherently an operator step.

Suggested build sequence (cheapest-given-existing-data first):

1. ~~**SDTM `DA`**~~ — ✅ **shipped.** Drug-accountability dispense/return records
   derive to the SDTM DA domain (Define-XML + XPT + bundle).
2. ~~**SDTM `SV`**~~ — ✅ **shipped.** Completed visits from the visit-schedule
   subsystem derive to SDTM SV.
3. **SDTM `DS`** — 🟡 *minimal shipped* (disposition from `Subject.status`). To
   fully clear: capture a per-subject disposition lifecycle (exit reason + date)
   and/or fold in screening-log screen-failures.
4. ~~**ADaM `ADAE` · `ADCM` · `ADLB` · `ADVS`**~~ — ✅ **shipped.** Takes ADaM
   from 2 → 6 (the FDA-recommended set); ADQS still needs SDTM `QS` first.
5. **Reviewer guides (SDRG / ADRG)** — drafter shape parallel to the CSR/IRB
   drafters; templated from the Define-XML + derivation metadata.
6. ~~**LB unit-conversion table**~~ — ✅ **shipped** (`cdisc/lab_units`):
   standardised LB columns are unit-converted US-conventional → SI per analyte.

**Deploy-gate, not backlog:** MedDRA (MSSO) + WHODrug (Uppsala) licenses. Until
these land, AE/MH/CM coded terms stay verbatim and any "regulator-acceptable
coding" claim is unsupportable — call this out in any CDISC capability statement.

See the [roadmap](../roadmap.md) "CDISC mapping" section for these items as
tracked follow-ups.
