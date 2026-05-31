# Clinical Research Assistant — Feature Guide

**An AI co-investigator that spans the full clinical-research lifecycle — from the first literature search to the patient-facing return-of-results letter, with regulatory-grade data capture in the middle.**

---

## Why this exists

A single clinical-research programme — even a routine one — passes through six distinct phases over months or years: literature synthesis, trial design, regulatory + ethics start-up, execution (data capture + randomisation + safety), analysis + reporting, and dissemination. Each phase has its own jargon, its own deliverables, its own audit posture, and historically its own software. Researchers spend more time switching tools and re-typing the same study into each one than they spend on the work itself.

The Clinical Research Assistant is one platform organised around **how clinical research actually flows**, not around what each piece of software does. The same PICO that scopes the meta-analysis becomes the seed for the SAP. The same protocol synopsis that goes to the IRB becomes the seed for the lay summary. The same Subject who's randomised in the eCRF appears in the SDTM bundle, in the CSR Disposition section, and in the return-of-results letter — all without re-typing.

What makes it different from a workflow-management tool: this one actually does the methodological work. It composes search queries, runs meta-analyses in a sandboxed compute environment, derives SDTM datasets, drafts ICH-E3 CSRs, computes Flesch-Kincaid grades host-side, parses HL7 v2 lab feeds. The model is the writer; the platform is the writer's editor + fact-checker + filing cabinet.

---

## How to read this guide

The platform is organised around the **six phases of clinical research**. The same six phases drive the welcome page (`index.html`) — every skill you'll read about here also has a tile there with a one-line starter command.

For each phase we describe:

- **What the phase is** in clinical-research vocabulary — who does this, what artefact comes out.
- **Each capability in the phase** — what it does, where it sits, how a researcher would actually use it, and the anti-hallucination posture where one applies.
- **The hand-offs** — every capability has one-click cross-references into the next-step capability so the same study doesn't get re-typed.

Cross-cutting infrastructure (auth, RBAC, library, budget tracking, portfolio dashboard, deployment) follows at the end, and is briefer because it's the same shape an institution would expect from any platform.

> **Template note (for future documentation).** The lifecycle structure used here — `phase → capability → workflow context → user benefit → guardrails` — is the canonical shape for all future product documentation. Roadmap items, design docs, demo guides, and customer-facing collateral should all anchor to the same six phases so a reader can pick up any document and locate themselves in the research lifecycle.

---

## 01 · Evidence synthesis

> **Phase context.** Before any new study runs, the team needs to know what the existing evidence base looks like — for the protocol's background section, for the IRB packet's "what's known", for the grant application's justification of need, and for guideline + HTA submissions whose entire artefact is a synthesis. This phase covers everything from the first search query through the published systematic review.

### 🔎 Search strategy

**Where it fits.** Information specialists build the search query that everything downstream depends on. A reproducible, multi-database query is the foundation of every systematic review and meta-analysis; it's also a defensible exhibit for a Cochrane / PROSPERO registration.

**What it does.** Builds a Boolean query with MeSH terms, field tags, and database-specific syntax across **PubMed, Europe PMC, Embase, Cochrane Library, Scopus, and Web of Science** — whichever your institution has licensed, in a single fan-out. Iterates with the user on broaden / tighten suggestions against live hit-count previews until the strategy is registration-ready.

**Try it.** `/search adults with treatment-resistant depression CBT vs SSRI`

**Outcome.** Information specialists get a defensible, reproducible search string in minutes instead of half a day, with the full iteration history preserved.

---

### 📝 Systematic-review protocol drafter

**Where it fits.** The protocol is the contract between the team and the methods. A registered protocol (PROSPERO) is increasingly mandatory for peer-reviewed publication.

**What it does.** Generates a **PRISMA-P–aligned** protocol skeleton — background, objectives, eligibility, search methods, screening plan, data items, risk-of-bias plan, synthesis approach — ready to deposit in PROSPERO. PDF + DOCX export.

**Try it.** `/protocol SGLT2 inhibitors for HF prevention in T2DM`

**Outcome.** A protocol-ready first draft your methodologist edits, not writes from scratch.

---

### 🗂️ SR title/abstract + full-text screening

**Where it fits.** Between the search (which returns thousands of hits) and the meta-analysis (which uses the dozen-or-so studies that meet criteria) is the **screening loop** — 3k-8k abstracts triaged by two reviewers, with an adjudicator breaking ties. This was historically the single biggest time-sink in any SR.

**What it does.** Title/abstract + full-text **dual review** with conflict adjudication; AI-assist pre-classifies every abstract against the project's PICO + inclusion criteria; reviewer accepts or overrides; PRISMA flow diagram + counts on tap. 14 routes under `/api/sr/*`; vanilla-JS UI at `/sr.html` with keyboard shortcuts (`i` = include, `e` = exclude, `m` = maybe).

**Roles.** `reviewer_1`, `reviewer_2`, `adjudicator` at `sr_review` scope. Each reviewer is blind to the others' decisions until adjudication.

**Outcome.** What was a six-week reviewer engagement is a single sprint.

---

### 📊 Meta-analysis

**Where it fits.** The classic deliverable of a systematic review — a pooled effect estimate with heterogeneity diagnostics, a forest plot, an interpretation paragraph.

**What it does.** End-to-end: PICO confirmation → multi-database search → study selection card → extraction table → pooled effect + heterogeneity + forest plot, all inside a Docker-sandboxed compute environment. Every PMID in the output traces back to a real database hit; the schema prevents the model from inventing citations.

**Try it.** `/meta SGLT2 inhibitors mortality in heart failure`

**Hand-offs.** Terminal card carries "→ Draft as manuscript" + "→ Draft lay summary" + "→ Draft GRADE" buttons that seed the next-step workflow with the full meta-analysis JSON.

---

### ⚖️ Risk of bias

**Where it fits.** Every systematic review and meta-analysis has to report risk-of-bias judgements per included study, per the chosen instrument. Mandatory for Cochrane review acceptance.

**What it does.** Supports **RoB 2** (RCTs), **ROBINS-I** (non-randomised), **Newcastle-Ottawa** (observational), **QUADAS-2** (diagnostic accuracy). Walks domain-by-domain, asks the operator the judgement questions, and produces the structured assessment table with a traffic-light summary plot.

**Try it.** `/rob assess RoB 2.0 for the studies in my last meta-analysis`

**Outcome.** Consistency across reviewers and a clean audit trail of *why* each judgement landed where it did.

---

### 🕸️ Network meta-analysis (NMA)

**Where it fits.** When the clinical question involves comparing **three or more interventions** (e.g. "of the five direct oral anticoagulants, which has the best safety-efficacy balance?"), pairwise meta-analysis can't answer it. NMA pools direct + indirect evidence across the network.

**What it does.** 5-stage workflow (intake → PicoNetwork with ≥3 interventions + transitivity rationale → search → extraction → results). **Frequentist backend** (mvmeta + electrical-network analogy on numpy + scipy; 1000 MVN posterior draws for SUCRA). **Bayesian backend (opt-in)** via PyMC NUTS. Hand-rolled network-geometry PNG. League table as colour-coded matrix + SUCRA ranking + landscape PDF.

**Try it.** `/nma compare five DOACs for stroke prevention in atrial fibrillation`

**Anti-hallucination.** Transitivity rationale required in the schema; PMIDs from `search_papers` only; pooled effects + SUCRA from sandbox runs only.

---

### 🧪 Individual patient data (IPD) meta-analysis

**Where it fits.** When you can obtain subject-level data from the included trials (rather than just published summaries), IPD meta-analysis is the gold standard. One-stage and two-stage pooling reveal heterogeneity sources the aggregate analysis can't.

**What it does.** 5-stage workflow (intake → bundle with per-trial CSV pastes + column mapping → main results = one-stage + two-stage **side-by-side** → subgroup × treatment interaction → assembled document). Methodological diagnostic: when one-stage and two-stage diverge by > 0.2 on the log scale, the model is misspecified.

**Try it.** `/ipd individual patient data meta-analysis on statins for primary prevention`

---

### 🏆 GRADE Summary of Findings + PRISMA 2020 checklist

**Where it fits.** Every journal-acceptable systematic review now requires a **GRADE Summary of Findings** table (certainty per outcome) and a **PRISMA 2020 reporting checklist**. Most teams produce these as the very last step before manuscript submission.

**What it does.** 5-stage workflow producing colour-coded SoF tables + the 42-item PRISMA 2020 checklist. Certainty is COMPUTED via Pydantic `computed_field` from per-domain ratings — the agent can't inline-assert it. Every downgrade rationale must cite a source number (I² value, CI bounds, n_studies, Egger's p).

**Hand-off.** Cross-handoff seed `Draft GRADE from meta-analysis` carries the meta-analysis JSON in verbatim.

---

### 🔔 Living-review watches + group subscriptions

**Where it fits.** A meta-analysis isn't a snapshot — guideline committees and HTA bodies need to know when new evidence lands that materially shifts the evidence base.

**What it does.**

- **Individual watch.** Pin a PICO + search strategy + cadence (daily / weekly / monthly). The platform re-runs the search on schedule, diffs against the baseline corpus, triages new papers, and notifies you when one clears the materiality threshold.
- **Group subscription.** Wrap a watch with an invited panel that votes yes / no / abstain on whether each new run is practice-changing. Quorum rule: `yes ≥ max(min_votes, ceil(min_fraction × n_voters))`. Members are notified only when quorum clears.

**Outcome.** Living reviews stop drifting out of date. Guideline committees get a consensus signal, not a per-individual alert.

---

## 02 · Trial design

> **Phase context.** Once the evidence base supports a new trial, the team specifies the question (PICOT), powers it (sample size), and writes the analysis plan. The deliverables are the protocol body + Statistical Analysis Plan, often submitted to peer review before any subject enrols.

### 🎯 Sample-size calculator + SAP drafter

**Where it fits.** The Statistical Analysis Plan is the contract that locks the analysis approach before unblinding. ICH E9 mandates an SAP before database lock; reviewers + regulators read it.

**What it does.** Four sample-size formulas (two-proportions / two-means / time-to-event Schoenfeld / paired) implemented directly on scipy.stats — no sandbox round-trip needed. Four-stage workflow: PICOT intake → sample-size derivation → ICH-E9 analysis plan → assembled SAP document. PDF + DOCX export.

**Try it.** `/sap two-arm RCT of CBT vs SSRI for depression, primary outcome HAM-D at 12 weeks`

**Hand-offs.** SAP document feeds the IRB packet + the CSR drafter.

---

## 03 · Start-up

> **Phase context.** The trial gets registered with regulators (ClinicalTrials.gov, EU CTR / CTIS, country registries) and the ethics committee (IRB / IRC) approves the protocol + ICF. Without these the trial cannot enrol its first subject. Both deliverables have strict structured templates and unforgiving compliance requirements.

### 📋 Trial-registration drafter

**Where it fits.** Pre-enrolment registration is mandatory under ICMJE policy for any trial whose results will be considered for top-tier publication.

**What it does.** New `registration_drafter` specialist + 4-stage workflow (intake → core fields → CT.gov + EU CTR drafts → assembled document). Output is paste-able into the CT.gov PRS portal and the EU CTIS portal. NCT IDs / CTIS trial IDs are **NEVER fabricated** — assigned by the registries on submission. Sponsor PHI marked `[SPONSOR INPUT]`.

**Try it.** `/register Phase 2 SGLT2i in HFpEF, double-blind randomised parallel`

---

### 🛡️ IRB / ethics packet + Informed Consent Form drafter

**Where it fits.** The IRB packet is the second of the two mandatory pre-enrolment artefacts. The two highest-value pieces — the protocol synopsis (1-2 page IRB-triage summary) and the ICF — take the most operator time today.

**What it does.** 3-stage workflow (intake → protocol synopsis → ICF). **Protocol synopsis** with 8 ICH E6(R2)-aligned sections (design / objectives / endpoints / methods / statistics / eligibility / schedule / risks). **ICF** schema-enforced against the 21 CFR §50.25(a) required-element set — the drafter can't omit any of the 9 required sections. Configurable Flesch-Kincaid reading-level target + computed actual grade; PDFs flag above-target overshoots. Multilingual en/es/fr/de.

**Try it.** `/irb Phase 2 trial of Drug X in disease Y, US IRB, language English, reading grade 8`

**Hand-offs.** Cross-handoff `Draft IRB packet from registration intake` carries the registration_drafter's fields into the IRB workflow without re-typing.

---

## 04 · Execution

> **Phase context.** Once the trial is approved and registered, real subjects enrol and real data accumulates. This is the largest, longest, and most operationally complex phase. It's also where the platform's regulatory-grade infrastructure (Part 11 / ALCOA+ / GCP / ICH E6) does the most work.

### 🧾 eCRF design (form authoring)

**Where it fits.** Every trial needs Case Report Forms — the structured surfaces site personnel and participants enter data into. CDISC CDASH is the conventional naming standard; ODM-XML is the export format for interoperability with EDC vendors.

**What it does.** AI-drafted CRFs from a pasted protocol (demographics / vitals / adverse events / outcomes / visit schedule). Versioned + immutable form definitions with `draft → publish → supersede` lifecycle. CDISC ODM-XML export. Edit-check engine (hard checks block invalid saves; soft checks raise queries).

**Try it.** `/ecrf design CRFs for a Phase 2 cardiometabolic outcomes trial`

---

### 🏥 EDC collector (live data capture)

**Where it fits.** The day-to-day data-entry surface for site coordinators + investigators, plus the participant magic-link ePRO surface for patient-reported outcomes.

**What it does.** Two capture surfaces (site EDC + participant ePRO). PHI lives in a separate clinical-data store with its own credentials — never mixed with the research database, never sent to the model. **Electronic signatures + lock hierarchy** (form-instance sign → subject-casebook sign-off → study-level lock); unlocking voids the signature, audited. **Source-data verification (SDV)** for monitors. **Tamper-proof audit trail** — append-only, enforced by the database itself, capturing every create/change with who, when, old→new value, and reason.

**Open at:** `/collector.html`

**Outcome.** The weeks-long investigator↔data-manager round-trip at study start-up collapses to a guided session, and the resulting capture system is **audit-ready by construction**.

---

### 🎲 Randomisation / IRT (Interactive Response Technology)

**Where it fits.** Every randomised trial needs an audited allocation mechanism. Modern IRT vendors charge $100k+ per trial; we bring it in-house.

**What it does.** Four deterministic algorithms: **simple / permuted_block / stratified_permuted_block / Pocock-Simon minimisation**. The data_manager generates the schedule at study-start (seeded for audit reproducibility); the coordinator + PI consume entries via per-subject allocation at enrolment. Blinding modes (`open_label` / `single_blind` / `double_blind` / `triple_blind`) mask the arm in API responses until a PI-triggered code-break unblinds it. CDISC integration: `derive_adsl` pulls `TRT01P` / `TRT01A` from `Allocation.arm` — no more `"TBD"` placeholder.

---

### 🚨 AE / SAE workflow

**Where it fits.** Every interventional trial needs an audited safety surface. SAEs trigger a 24-hour reporting clock; SUSARs trigger a 7/15-day expedited regulatory clock. ICH E2A is the framework.

**What it does.** Coordinator captures AEs at the point of care. **Auto-classification** applies ICH E2A §III.A criteria (grade ≥3, death, life-threatening, hospitalisation, congenital, persistent disability, other medically significant) on every write. **24h reporting deadline** stamped automatically. **PI override** flows through `PATCH /api/edc/ae/{id}` and clears the deadline on downgrade. **FDA 3500A IND safety report** drafted as PDF (fields the platform can derive are filled; sponsor-supplied fields marked `[SPONSOR INPUT]`). MedDRA PT captured as free text (real validation requires a MedDRA license at deploy).

---

### 📋 Protocol-deviation + CAPA

**Where it fits.** Every GCP-compliant trial logs protocol deviations + their Corrective And Preventive Actions. Auditors check this surface.

**What it does.** Deviation log with major / minor / critical classification. CAPA lifecycle (open → completed). The PI closes the deviation once every CAPA is `completed`. Audit trail per state transition.

---

### 👥 Recruitment / screening logs

**Where it fits.** CONSORT 2010 reporting mandates a screened → eligible → consented → enrolled → randomised funnel diagram. Most trials maintain this in a side spreadsheet.

**What it does.** Three independent state machines (eligibility / consent / enrolment) with the CONSORT 2010 codebook (8 canonical screen-failure reasons + 'other'). 6 endpoints + a funnel rollup (per-week × per-site, computed in Python). PHI-minimised (age band / sex / race / ethnicity / dob_year only — no full DOB / MRN). Collector.html Recruitment panel surfaces the funnel + recent screenings + a record form.

---

### 📅 Visit scheduling + participant reminders

**Where it fits.** Every visit-driven trial schedules per-subject visits relative to a baseline date + sends reminders. Missed visits are a leading cause of protocol deviations.

**What it does.** Five new tables (VisitSchedule / ScheduledVisit / PlannedVisit / ParticipantContact / SentReminder). Reminder offsets are non-positive ints (days before due_date). SES email send when AWS_SES_FROM_EMAIL env is set; else dry-run mode logs `SentReminder` rows with `provider='dry_run'`. APScheduler interval job fires due reminders every 15 minutes. Collector.html Visit-calendar panel.

---

### 💊 Drug accountability

**Where it fits.** Every investigational-product trial has to track every kit from sponsor → site → subject → return. Regulators inspect this trail.

**What it does.** Four ClinicalBase tables (InvestigationalProduct catalogue / DrugReceipt shipments / DrugDispensation kit-to-subject / DrugReturn). State invariants enforced at the repository layer: dispense > inventory → reject; return without prior dispensation → reject; quantity_used + quantity_lost ≤ quantity_returned; cross-deployment subject vs IP → reject. Per-lot reconciliation rollup (`current_inventory = received + (returned − used − lost) − dispensed`).

---

### 📤 Source-document extraction (EHR → eCRF)

**Where it fits.** Retrospective studies and patient-level meta-analyses often start from structured exports — your EHR's lab dump, a registry's flat file, a trial-management system's CSV. Today the operator re-keys each row into the eCRF.

**What it does.** 4 new tables (SourceDocument / SourceRow / ExtractionMapping / ExtractionFill). CSV ingest with SHA-256 content-hash dedupe. Per-deployment per-form versioned mappings. **Dual apply modes**: `subjects` (pre-fill eCRF instances) OR `table` (flat IPD-meta projection). Per-cell ExtractionFill carries denormalised mapping_version so audit chain survives mapping mutations.

---

### 🧫 Lab-data feeds (HL7 / CDISC LAB / FHIR)

**Where it fits.** Central labs ship results in standard formats. The eCRF was forcing operators to re-key every lab value by hand.

**What it does.** Three stdlib-only parsers: **HL7 v2 ORU^R01** (pipe-delimited; most labs still emit this), **CDISC LAB tab-delimited** (trial-specific central-lab format), **HL7 FHIR R4** (modern JSON). Two ingest endpoints: file upload via collector.html for operators; system-to-system listener for central-lab POST. SHA-256 idempotent dedupe. **SDTM LB cascade** — parsed labs feed the existing CDISC LB derivation additively without disturbing the form-based path.

---

### 🗺️ Multi-site / multi-tenant coordination rollup

**Where it fits.** Central coordinators, monitors, and DMs need to see all sites at once — who's behind on enrolment, who has the largest query backlog, who hasn't been SDV'd recently.

**What it does.** Per-deployment and cross-deployment rollups, composing **4 KPI groups per site**: enrolment funnel (from ScreeningLog), query backlog (open / answered / closed), safety (open AEs / serious / deviations / CAPAs), operational (overdue visits / low-IP lots / last SDV). Pure read-side aggregation — no new persistence. New `/multisite.html` page with deployment picker + per-site cards + admin-only org section.

**Open at:** `/multisite.html`

---

### ✅ eCRF formal validation pack (Part 11 / IQ-OQ-PQ)

**Where it fits.** "Deployable in an audited environment" gate. FDA / EMA / MHRA inspectors expect documented Installation / Operational / Performance Qualification.

**What it does.**

- **Password re-authentication at signing** (Part 11 §11.200) — verifies user password against Cognito on every form-sign + casebook-signoff.
- **Study-level (database) lock** — 4 endpoints under `/api/edc/deployments/{id}/{lock,unlock,lock-status,lock-history}`. While locked, all data-entry, signing, and SDV writes 409. Audit row per lock + unlock.
- **IQ / OQ / PQ documented pack** — auto-generated from the running system: IQ captures package version + pinned dependencies; OQ shells out to pytest over the Requirements Traceability Matrix (each requirement tied to Part 11 §, ICH E6, ICH E2A, ALCOA+); PQ is the 8-step customer-side runbook.
- **4 PDFs + a ZIP bundle** from `/api/admin/validation-pack/*`.

---

## 05 · Analysis & reporting

> **Phase context.** After database lock, the team runs the planned statistical analyses, assembles the CDISC submission bundle, writes the CSR, drafts the manuscript, and produces patient-facing materials. Most of this phase is writing — but writing that has to cite specific result tables and survive sponsor + regulator review.

### 📈 Trial-specific statistical analysis specialist

**Where it fits.** The trial-specific analyses that ICH E9 calls for: time-to-event (Kaplan-Meier + log-rank + Cox PH), continuous endpoints (MMRM), binary endpoints (Fisher's exact + log-binomial GLM), subgroup analyses with interaction tests.

**What it does.** 7-stage post-lock workflow. **Sandbox-backed** via the new `run_trial_analysis` tool wrapping 4 canonical scripts: `kaplan_meier.py` (K-M + log-rank + Cox PH via statsmodels.duration.PHReg), `mmrm.py` (MMRM via statsmodels.MixedLM with visit fixed effects + TRT × visit interactions + optional BASE covariate), `binary.py` (Fisher's exact + log-binomial GLM with auto-fallback), `subgroup_forest.py` (per-subgroup Cox HR + interaction p + landscape forest PNG).

**Anti-hallucination.** Every result row carries a required `derived_from` of the form `sandbox:<kind>:<paramcd>` — HRs / LSMean diffs / CIs / p-values come from sandbox runs only, NEVER inline.

---

### 📉 Beyond-forest-plot visualisations

**Where it fits.** Modern submissions go beyond the forest plot — funnel plots for publication bias, waterfall plots for per-subject best response, swimmer plots for treatment timelines, GRADE chip tables as visual SoF.

**What it does.** Four plots across three specialists: **funnel + Egger's test** (in meta_analysis STEP 5), **waterfall + swimmer** (in trial_stats STEP 6.5), **GRADE chip table** auto-derived host-side from assessments.

---

### 📦 CDISC SDTM → ADaM → TLF submission pipeline

**Where it fits.** FDA + EMA + PMDA require CDISC-shaped submissions. The full pipeline (DM / AE / VS / LB / EX / CM / MH SDTM → ADSL + ADTTE ADaM → TLF tables + figures) is the most operationally complex single artefact in clinical research.

**What it does.** `BuiltinPythonMapper` derives **7 SDTM domains** (DM + AE + VS + LB + EX + CM + MH) via item-id mapping convention + per-deployment `ItemMappingConfig`. Repeating-form domains gathered via `form_to_domain_map`. **ADaM ADSL** with SAFFL/ITTFL/DTHFL/AGEGR1. **ADaM ADTTE** with PARAMCD/PARAM/AVAL/CNSR/STARTDT/ADT/EVNTDESC/SRCDOM/SRCVAR audit anchors. Sandbox-backed K-M / Cox PH survival analyses via `cdisc/sandbox_scripts/survival_analysis.py`. **Define-XML v2.1** generator covering all 9 datasets with ItemGroupDefs + ItemDefs + CodeLists + MethodDefs. **Hand-rolled SAS Transport v5 writer** — IBM-360 8-byte float conversion + 80-byte record alignment + 140-byte v5 namestr records. Submission bundle ships `define.xml` + `define-overview.txt` + `sdtm/*.{csv,xpt}` + `adam/{adsl,adtte}.{csv,xpt}` + TLF.

**No new dependency** — `xport` / `pyreadstat` deliberately avoided.

---

### 📄 Clinical Study Report (ICH E3) drafter

**Where it fits.** The CSR is the final regulator-submission artefact summarising the entire trial. Skeleton with 16 ICH E3 section headers; data-driven sections populated from ADSL + ADTTE + TLF.

**What it does.** 4-stage workflow (intake → synopsis → 4 data sections → assembled). Data sections (Disposition / Demographics / Efficacy / Safety) populate from operator pastes. Narrative sections (Introduction / Discussion / Overall Conclusions) ship as `[Operator to complete]` placeholders this slice. **Strictest anti-hallucination posture in the codebase**: every count carries a required `derived_from` source-artefact id; system prompt forbids inline effect-size / CI / p-value writes without a TLF / ADTTE source.

**Hand-offs.** Cross-handoff seeds from meta-analysis, ADTTE, SAP, and trial-stats.

---

### ✍️ Manuscript drafter (IMRaD) + reviewer-response loop

**Where it fits.** Every accepted manuscript at a top-tier journal involves a peer-review round. Drafting the manuscript + drafting the reviewer-response letter + the cover letter is a full editorial cycle.

**What it does.** 3-stage workflow (intake → IMRaD draft → reviewer-response). Journal-target enum: NEJM / Lancet / BMJ / JAMA / Annals / PLOS ONE / generic. PDF + DOCX export bundles cover letter + point-by-point response. Meta_analysis card carries a "→ Draft as manuscript" handoff button.

**Try it.** `/manuscript compose an IMRaD manuscript from my last meta-analysis, target NEJM`

---

### 🗣️ Lay summary (patient-facing PLS)

**Where it fits.** Patient-facing materials sit alongside the regulator + sponsor artefacts — recruitment posters before the trial, shared-decision-making aids during, return-of-results letters after. EMA Reg (EU) No 536/2014 mandates a lay summary for closed trials.

**What it does.** Three intake variants: **recruitment** (from protocol synopsis), **evidence** (from meta-analysis), **results** (from CSR / trial_stats). Host-side **Flesch-Kincaid grade** computation (stdlib only — no `textstat` dep); compute-and-iterate readability loop with retry cap=3. AudienceProfile (target_grade 4-12 default 6 + language en/es/fr/de + free-text region + population_descriptor). 5 CISCRP / NIH plain-language sections schema-enforced. **Anti-hallucination**: PMIDs traced for evidence variant; `derived_from` traced for results variant; no medical-decision language.

**Try it.** `/lay-summary plain-language summary at grade 6 English for parents of children with asthma`

---

### 📚 Citation-manager integration

**Where it fits.** Every manuscript is rooted in a bibliography. Researchers maintain libraries in Zotero / EndNote / Mendeley.

**What it does.** Pure file-format round-trip (BibTeX + RIS) covering Zotero / EndNote / Mendeley universally. Stdlib-only parsers + exporters. 2 endpoints under `/api/citations` (parse multipart with auto-detect, export to downloadable file). `import_citations(format, content)` tool registered on `manuscript_drafter` + `sr_protocol`. Drop-zone in `<ManuscriptIntakeCard>` + `<ProtocolMethodsCard>`.

---

## 06 · Cross-cutting infrastructure

> **Phase context.** Behind every phase is the same underlying platform — auth, RBAC, library + retrieval, sandboxing, portfolio + budget tracking, and the configuration surface. These aren't research workflows but they're what makes the workflows safe to run at scale.

### 📊 Portfolio dashboard

**Where it fits.** A researcher running multiple SR projects and clinical trials simultaneously needs a single-pane-of-glass view.

**What it does.** 5 endpoints under `/api/portfolio` (threads + sr-projects + deployments + summary + admin-only org). Per-user routes inherit top-level auth; admin-only `/org` gated by `portfolio.read_org`. Each thread row carries its cumulative cost. Vanilla-JS `/portfolio.html` page.

**Open at:** `/portfolio.html`

---

### 💰 Budget + cost rollup across studies

**Where it fits.** Institutional admins need cost-per-evidence-output transparency — sponsors ask during contracting, finance asks during budget reviews, PIs compare platform spend to outside-vendor quotes.

**What it does.** Bedrock pricing table for Claude 4.x (Haiku 4.5 / Sonnet 4.6 / Opus 4.7) + legacy 3.x. Pure read-side aggregation over the existing done-event token stream — no new persistence. Per-workflow + per-model-family + monthly + cumulative rollups. Admin-only org rollup sorted by spend descending.

**Open at:** `/portfolio.html` → "Spend (USD)" section

---

### 🔐 RBAC (scoped roles + permission matrix)

**Where it fits.** Without role separation, an institution cannot deploy beyond a 1–2-person team. Coordinators, monitors, PIs, statisticians, screening reviewers, and DSMB members all need distinct entitlements.

**What it does.** Three-tier hierarchy: `global ⊃ study ⊃ site`, plus parallel `sr_review` scope for screening project membership. 14 distinct roles (admin / researcher / student / auditor / study_designer / principal_investigator / coordinator / data_manager / monitor / reviewer_1 / reviewer_2 / adjudicator / + sub-roles). Skill-permission map governs which specialists each role can drive. RBAC-1 (scopes) + RBAC-2 (resource→scope resolvers) + RBAC-3 (per-user partition on /usage) all in production.

---

### 📖 Library + RAG (retrieval-augmented)

**Where it fits.** Every abstract, full-text article, MeSH lookup, and extraction table you pull lands in the local research store. The same paper reused across reviews is paid for once.

**What it does.** Publication cache + Titan v2 embeddings + hybrid `rag_search` (BM25 + dense vector). PDF upload + parsing. Library UI surfaces the cache + per-search hit counts + re-import lineage.

---

### 🛡️ Sandboxed statistical compute

**Where it fits.** Every analytical workflow that produces an effect estimate or a figure runs inside an isolated compute environment — never on the host.

**What it does.** Docker container with **networking disabled**, the script + input files mounted **read-only**, only a designated output directory writable, capped CPU + memory. The model can run a random-effects meta-analysis without ever touching the host or the wider internet.

---

### 🔌 Multi-source paper search

**Where it fits.** Every clinical search across the platform fans out across enabled bibliographic sources behind one `PaperSource` Protocol.

**What it does.** PubMed + Europe PMC live; Embase / Cochrane / Scopus / Web of Science as pluggable additions. Admin panel toggles enable / disable + per-source rate limits + credentials (read from your secrets manager). Cross-source deduplication by PMID → DOI → source-id.

---

### 🔐 Authentication + multi-tenant deployment

**Where it fits.** Drop-in OAuth2 / OpenID Connect integration with your institutional IdP. One AWS account can host many CRA installs via separate Cognito user pools — set env vars, no code change.

**What it does.** Cognito-backed OIDC. Per-installation user pool. Invite flow for first-time logins. Admin role assignment via `admin.html`. Multi-tenant setup script is idempotent by pool name.

---

### 💬 General clinical Q&A

**Where it fits.** For the questions that aren't a full systematic review: background reading, definition of methods, navigation of guidelines.

**What it does.** Uses web search + Wikipedia + library RAG. **Hard-prevented from quoting effect sizes, PMIDs, or guideline citations from training data** — a regex-level validator blocks unsupported clinical claims before they reach the user.

**Try it.** `/ask what does a forest plot show in a meta-analysis?`

---

## What makes it different

| Differentiator | Why it matters for a research org |
|---|---|
| **Organised around the clinical-research lifecycle, not around features** | The same six phases drive the welcome page, this guide, the roadmap, and every design doc. A researcher reads any of those documents and immediately locates themselves. New documents follow the same template. |
| **Anti-hallucination enforced in code, not just prompt** | A regex validator on general Q&A, a tool-gated PMID rule on meta-analysis, a `derived_from` schema requirement on every CSR / trial-stats / IPD result row, and a host-side Flesch-Kincaid grade on every lay summary. Your investigators can trust the bibliography and the result tables. |
| **Cross-handoffs between phases** | The same meta-analysis JSON seeds the manuscript drafter, the GRADE drafter, AND the lay summary specialist. The same protocol synopsis from registration_drafter seeds the IRB packet. The same Allocation from IRT drives the SDTM ADSL. No re-typing. |
| **Source-agnostic search across paid + open databases** | One admin panel for credentials, rate-limits, enable/disable. Cross-source de-duplication. |
| **Local research cache + RAG over pulled content** | Pull paid content once; reuse it across the entire research programme. |
| **Sandboxed statistical compute** | Network disabled, capped CPU/RAM, RW output dir only. |
| **Workflow-gated tools** | The assistant cannot skip ahead. Methodology *is* the guardrail. |
| **Regulatory-grade data capture built in** | CDISC ODM-XML + Part 11 + ALCOA+ + e-signatures + DB-enforced audit trail + Validation pack (IQ/OQ/PQ + study lock). |
| **Full conversation persistence** | Every turn — PICO, included studies, extraction table, generated code, plot, AE record, lab batch — is stored. Reproducing an analysis a year later means re-opening the thread. |

---

## What it costs to run

Two variable costs to plan around: LLM tokens on AWS Bedrock, and web-search calls on Tavily. Everything else — paper database access, paper storage, the FastAPI app, the sandbox — sits on infrastructure you already own.

| Metric | Envelope | Notes |
|---|---|---|
| **Active research day, per user** | **$200 – $300** | A productive day across multiple phases: SR screening, meta-analysis, manuscript drafting, CSR section writing. |
| **Re-open / inspect an existing thread** | **≈ $0** | Past tool results, papers, extractions, generated code, AE records, lab batches are already in the local store. |
| **Re-using a paper across reviews** | **≈ $0** | The RAG pipeline hits the local research store first. |

**Built-in budget visibility.** The portfolio dashboard surfaces a real-time spend rollup per user, per workflow, and per model tier. Admins see the org-wide breakdown with top spenders. Re-built every page-load — no separate billing pipeline.

**Cost-control levers:**

- Local cache + RAG (no repeat API spend on content already paid for)
- Workflow-gated tools (expensive tools unreachable until the workflow needs them)
- Per-turn ceilings (`max_model_requests`, `agent_timeout_seconds`)
- Configurable model tier per workflow (Sonnet / Haiku / Opus)
- Tavily consumed only by general Q&A + protocol drafting — clinical search runs against bibliographic databases and does not consume Tavily credits

---

## Deployment & governance

- **Flexible deployment.** Runs on-premises or in your cloud (AWS, GCP, Azure). Single-tenant by default; same codebase, your choice of trust boundary.
- **Multi-tenant Cognito.** One AWS account hosts many CRA installs via separate user pools — set env vars, no code change. The setup script is idempotent by pool name.
- **Authentication.** OAuth2 / OpenID Connect via Cognito; drops into Okta / Azure AD / Auth0 / Keycloak / your homegrown IdP.
- **Authorisation.** Role-based access control with three-tier scope hierarchy (`global ⊃ study ⊃ site`) plus parallel `sr_review` scope for screening projects.
- **Sensitive data.** API keys + Bedrock credentials + PHI live in a secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.) — never in plaintext config or SQLite.
- **PHI isolation.** Subject data lives in a separate clinical-data store with its own credentials — never mixed with the research database, never sent to the model.
- **Data egress.** Outbound traffic limited to your configured bibliographic APIs and your Bedrock endpoint. No telemetry. No shared multi-tenant cloud — LLM calls go to your own AWS account.
- **Audit.** Every turn persists with full message + tool-call history. eCRF subsystem has a tamper-proof DB-enforced audit trail. Validation pack (IQ/OQ/PQ) on demand.

---

## What's next

The platform's strategic + tactical pickable backlog lives in **[roadmap.md](roadmap.md)**, organised in the same two-part shape:

1. **Strategic initiatives** — large next steps that each open a new customer segment (EHR / FHIR / OMOP integration for real-world evidence; Pharmacovigilance / post-market AE tracking; HTA / payer-grade dossier generation; Central imaging upload + adjudicated review; DSMB / DMC charter + blinded views; Research-gap analysis specialist; Press-release / institutional-comms drafter).
2. **Pickable follow-ups by subsystem** — concrete one-engineer slices we deliberately deferred during the P0 / P1 / P2 sweep, organised so a team member can scan their area and pick.

Both are described in the same lifecycle-anchored vocabulary used here — pick up the roadmap, locate the phase you care about, and read forward.

---

## In short

If your researchers spend more time **finding, formatting, and re-typing evidence** than **interpreting it**, this is what you point them at. They keep the clinical judgement. The platform absorbs the methodological choreography — across every phase from the first literature search to the patient-facing return-of-results letter — and writes it down as it goes.
