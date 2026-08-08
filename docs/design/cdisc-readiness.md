# CDISC submission readiness

**Question this doc answers:** if a common drug-intervention trial is run through
CRA, can someone generate the CDISC submission datasets and reports end-to-end?

**Bottom line — yes for the core, not a turnkey full submission.** From captured
eCRF data, one derive run produces a genuine, reviewable CDISC package: 7 SDTM
domains, the subject-level (ADSL) and time-to-event (ADTTE) ADaM datasets, a
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
| 🟡 | `LB` | Laboratory | Results + normal-range indicator. `LBSTRESN = LBORRES` until a unit-conversion table is wired in. |
| ✅ | `VS` | Vital Signs | Height, weight, BP, pulse, temperature. |
| 🟡 | `MH` | Medical History | Captured & mapped. SOC placeholder until MedDRA. |
| ⬜ | `DA` | Drug Accountability | **Data already exists** (IP receipt / dispense / return subsystem) — no SDTM DA mapper yet. |
| ⬜ | `DS` | Disposition | Completion / discontinuation events. Derivable from screening + visit state. |
| ⬜ | `SV` / `SE` | Subject Visits / Elements | Visit-schedule data exists; needs the SV/SE mappers. |
| ⬜ | `QS` | Questionnaires | ePRO subsystem could feed QS; mapper not built. |
| ⬜ | `EG` | ECG | Only if the trial collects ECG (capture + mapper). |
| ⬜ | `TA·TE·TV·TI·TS` | Trial Design | Small static domains required for submission; not yet emitted. |

## ADaM datasets

Analysis Data Model — SDTM plus analysis-ready derived variables.

| Status | Dataset | Name | Notes |
|---|---|---|---|
| ✅ | `ADSL` | Subject-Level | One row/subject; population flags (SAFFL / ITTFL / DTHFL), age groups, treatment, reference dates. |
| ✅ | `ADTTE` | Time-to-Event | Time to first AE / SAE / death; AVAL + CNSR with SRCDOM/SRCVAR traceability. |
| ⬜ | `ADAE` | Adverse Events | Scoped & deferred — a 6-file slice (model + deriver + pipeline + Define-XML + XPT). TRTEMFL / AOCCFL flags. |
| ⬜ | `ADLB` / `ADVS` | Labs / Vitals (BDS) | Basic-Data-Structure analysis datasets from LB / VS. |
| ⬜ | `ADCM` | Concomitant Meds | Occurrence-data-structure dataset from CM. |

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

1. **SDTM `DA`** — drug-accountability subsystem already holds receipt / dispense /
   return rows; add the DA mapper. Also closes the roadmap's "CDISC EX from
   dispensations" follow-up.
2. **SDTM `DS`** — disposition from screening-log + visit state (both captured).
3. **SDTM `SV`** — subject visits from the visit-schedule subsystem.
4. **ADaM `ADAE`** — the deferred 6-file slice; highest analysis value.
5. **ADaM `ADLB` / `ADVS`** — BDS datasets from LB / VS.
6. **Reviewer guides (SDRG / ADRG)** — drafter shape parallel to the CSR/IRB
   drafters; templated from the Define-XML + derivation metadata.
7. **LB unit-conversion table** — makes `LBSTRESN` regulator-ready (removes an
   `LB` caveat).

**Deploy-gate, not backlog:** MedDRA (MSSO) + WHODrug (Uppsala) licenses. Until
these land, AE/MH/CM coded terms stay verbatim and any "regulator-acceptable
coding" claim is unsupportable — call this out in any CDISC capability statement.

See the [roadmap](../roadmap.md) "CDISC mapping" section for these items as
tracked follow-ups.
