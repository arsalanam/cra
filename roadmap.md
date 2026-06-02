# Roadmap

Forward-looking tracker for what's left to build. This document is split into two parts:

1. **[Strategic initiatives](#strategic-initiatives)** — the next big rocks. Each one is a sponsor-ready opportunity in its own right: a clinical-research workflow that the platform doesn't yet serve and a customer segment we can't take today without it. Pick from here for major builds.
2. **[Pickable follow-ups by subsystem](#pickable-follow-ups-by-subsystem)** — every concrete item we deliberately deferred during the P0 / P1 / P2 sweep. Each line is small enough that one engineer can take it solo. Pick from here for steady polish on what we already ship.

Historical context lives at the bottom: [Already shipped](#already-shipped) (one-line summary per landed item with a memory + commit pointer), [Designed but not yet built](#designed-but-not-yet-built), and [Maintenance norms](#maintenance).

---

## How to read this

### Status

- ✅ **shipped** — running in production
- 🚧 **in flight** — actively being built
- 📐 **designed** — design doc exists, implementation pending
- 📝 **planned** — committed, not yet designed
- 💡 **proposed** — gap identified, scope TBD

### Effort

T-shirt size. **S** ≈ days, **M** ≈ a couple of weeks, **L** ≈ a quarter, **XL** ≈ multi-quarter / ongoing.

### Phase

Where the item sits in the research lifecycle:

`Synthesis` (literature → review) → `Design` (question → protocol → grant) → `Start-up` (regulatory → ethics → site init) → `Execution` (recruit → capture → query → monitor) → `Analysis` (data → stats → CSR → manuscript) → `Dissemination` (publish → review → press → archive) → `Cross-cutting` (auth, RBAC, portfolio, integrations).

### Where we are

As of 2026-05-31, every P0 / P1 / P2 item from the original roadmap is **✅ shipped**. The platform now spans synthesis → analysis → dissemination across both retrospective evidence reviews and prospective trials. The "what's next" question is two-fold:

- **Pick a strategic initiative** to chase a new customer segment (post-market PV teams, RWE researchers, HTA submitters, imaging-heavy trials, DSMBs, comms offices).
- **Pick a follow-up** to deepen an existing subsystem — these are deliberately-deferred items that each unlock a concrete next step inside a workflow we already own.

---

## Strategic initiatives

Each of these is a multi-week-to-multi-quarter effort that opens a new class of customer. Ordered roughly by **fit with what we already ship** — items higher up reuse more of the existing platform; items lower down are larger green-field builds.

### 1. EHR / FHIR / OMOP integration for real-world evidence

**Phase:** Execution / Synthesis · **Effort:** XL · **Status:** 💡 proposed

**What it is.** Direct integration with hospital EHR systems (Epic / Cerner / Allscripts via FHIR R4) and federated data networks (OHDSI OMOP CDM, Sentinel, TriNetX, N3C). Lets a researcher pull a cohort straight from a live clinical data store rather than asking the operator to manually paste a CSV.

**Why a researcher / sponsor would want it.** Real-world evidence studies are the fastest-growing trial design — payers, regulators, and academic groups all want post-approval safety + effectiveness analyses that the RCT data alone can't deliver. Without EHR integration the platform can synthesise published literature and capture eCRF data but can't open the larger RWE market. The eCRF subsystem's source-document extraction (P1 #5) is the wedge: it already understands a CSV-shaped row stream + audit-traced ExtractionFill chain. FHIR / OMOP are richer row streams with the same downstream shape.

**What it unlocks.** A whole new specialist tier (`rwe_specialist`) anchored on cohort definition + outcome ascertainment from a federated network. A whole new customer segment (academic medical centres, payer-research teams, pragmatic-trial sponsors). A whole new way to drive the SDTM pipeline (the same LB derivation that already accepts lab-feed rows can accept FHIR Observation rows).

**Scope sketch.**
- A new `RweCohort` model (federation key + concept-set id + index date + follow-up window + denominators).
- A FHIR R4 reader extending the parser in `services/lab_ingest/fhir.py` to cover Condition / Procedure / MedicationStatement / Encounter (today it covers Observation only).
- An OMOP-CDM SQL emitter for queries against an external Postgres / Databricks target (concept_id mappings + person_id pseudonymisation).
- A federated-cohort API endpoint that issues the query, captures the row count, and surfaces a CONSORT-style flow chart.

**Dependencies + sequencing.** Source-document extraction (P1 #5) ✅ landed; lab-data feeds (P2 #6) ✅ landed and proved the FHIR R4 parsing pattern. Next dependency = a deployed test instance of an OMOP-CDM database to test against — defer hardware spend until customer pull.

---

### 2. Pharmacovigilance / post-market AE tracking

**Phase:** Execution · **Effort:** L · **Status:** 💡 proposed

**What it is.** Continuous post-market safety surveillance after a product reaches market. Adjacent to the trial AE / SAE workflow we already ship (P0) but operates on a different lifecycle and different reporting cadence.

**Why a sponsor or PV team would want it.** Regulators (FDA, EMA, PMDA) require post-market periodic safety updates (PSUR / PBRER) and rapid-alert reporting for serious unexpected events. PV teams today maintain this in standalone safety databases (Argus, ArisGlobal). Folding it into the platform lets the same MedDRA-coded AE schema travel from trial → post-market without re-keying, and lets the same `manuscript_drafter` and `csr_drafter` surfaces compose a PSUR draft from the post-market signal stream.

**What it unlocks.** The PV team as a buying centre. A periodic-report drafter (PSUR / PBRER / DSUR). E2B(R3) gateway integration with FDA FAERS + EMA EudraVigilance. Signal-detection algorithms (disproportionality analysis, PRR / ROR).

**Scope sketch.**
- A new `PostMarketCase` model (case id + reporter type + receipt date + classification + serious-criteria flags) extending the existing AE schema rather than duplicating it.
- A signal-detection job that computes PRR / ROR per drug-event pair on a weekly cadence.
- An `pv_periodic_drafter` specialist that turns the case stream + signal table into a PSUR-shaped document.

**Dependencies + sequencing.** AE / SAE workflow (P0) ✅ landed and provides the core schema. MedDRA license-gated mapping (a deferred CDISC follow-up) becomes a hard dependency for regulator submission. E2B(R3) XML export (a deferred safety follow-up) is the gateway adapter.

---

### 3. HTA / payer-grade dossier generation

**Phase:** Analysis / Dissemination · **Effort:** XL · **Status:** 💡 proposed

**What it is.** Submission packages for Health Technology Assessment agencies: NICE (UK), IQWiG (Germany), CADTH (Canada), PBAC (Australia), HAS (France), AMCP (US payer template). Each agency has a fixed dossier structure with mandatory cost-effectiveness modelling, indirect comparison, and budget-impact sections.

**Why a sponsor would want it.** HTA submissions are the gate to reimbursement. Each costs a six-figure consultancy fee today; sponsors increasingly want to bring this in-house and the bottleneck is the writing + re-formatting per agency. Big effort, high revenue per deal, far-future.

**What it unlocks.** A pharmaceutical or biotech sponsor's market-access team as a buying centre. A cross-agency dossier drafter that composes from the same source artefacts the CSR drafter already uses. A separate evidence-synthesis surface for indirect comparisons (the NMA specialist we already ship is the technical engine; the dossier framing is new).

**Scope sketch.**
- A `dossier_drafter` specialist with per-agency variants (the way `lay_summary` has recruitment / evidence / results variants).
- A cost-effectiveness Markov / partitioned-survival model wrapper around the existing sandbox (PSA + DSA + scenario-analysis runs).
- A budget-impact-model section that pulls from the trial result tables + epidemiology pastes.
- Per-agency PDF templates (each is dozens of pages with mandatory section headings).

**Dependencies + sequencing.** Meta-analysis ✅, NMA ✅, GRADE ✅, CSR drafter ✅ all in place. Direct dependency = a deploy-time access to ISPOR good-practices templates + per-agency boilerplate (defer until customer pull).

---

### 4. Central imaging upload + adjudicated review

**Phase:** Execution · **Effort:** XL · **Status:** 💡 proposed

**What it is.** Subjects in many trials have imaging endpoints — CT / MRI / X-ray / ophthalmology fundus / histopathology. Trials run a centralised imaging review where 2-3 blinded radiologists adjudicate each scan against RECIST 1.1 (oncology), Lugano (lymphoma), iRANO (CNS), Adelaide (osteoporosis), etc.

**Why an imaging-heavy trial sponsor would want it.** Today imaging review lives in standalone PACS systems (Calyx, ICON, Bioclinica). Folding it in means the same dual-review pattern we already ship for SR screening (R1 / R2 / Adjudicator) can drive imaging adjudication, and the same SDTM RS (Disease Response) domain can be derived from the adjudicated assessments without re-keying.

**What it unlocks.** Imaging-heavy oncology / neurology trials as a customer segment. A DICOM viewer (web-based; OHIF Viewer is open-source and embeddable). A `RsAssessment` model that mirrors the SR-screening dual-review state machine. A new SDTM RS (Disease Response) deriver.

**Scope sketch.**
- DICOM ingest endpoint + S3-backed storage with PHI-stripping pipeline (DICOM tags carry patient name + DOB + study description).
- Embedded OHIF Viewer for thumbnails + window-leveling + measurement tooling.
- A `RsAssessment` model with the same yes/no/adjudicate tri-state as `SrCandidate`.
- An adjudication queue with the existing R1 / R2 / Adjudicator role grant.
- An SDTM RS deriver that emits one row per adjudicated lesion-level assessment.

**Dependencies + sequencing.** SR screening dual-review pattern (P0) ✅ landed and is the closest analogue. CDISC SDTM bundle ✅ has the row pattern. New: DICOM dependency, S3 storage, OHIF embed.

---

### 5. DSMB / DMC charter + blinded views

**Phase:** Start-up / Execution · **Effort:** L · **Status:** 💡 proposed

**What it is.** An independent Data Safety Monitoring Board (or Data Monitoring Committee) reviews unblinded interim safety + futility data on a pre-defined cadence. The DSMB needs:

- A **DSMB charter** drafted at study start-up (membership + cadence + stopping rules + statistical-monitoring plan).
- A **blinded view** for the sponsor team that excludes treatment-arm details and is enforced at the database layer, not just hidden in the UI.
- A **DSMB-only unblinded view** that surfaces interim efficacy + safety summaries with audited access.

**Why a sponsor would want it.** DSMB oversight is mandatory for any pivotal Phase 2 / Phase 3 RCT. Today the charter is drafted in Word; the blinded view is enforced by mailing different CSV exports to different recipients. Both are operationally fragile.

**What it unlocks.** Late-phase RCTs as a customer segment (we already serve early-phase via the IRB + randomisation + safety subsystems). A pre-built scope (`scope_type='dsmb'`) parallel to `sr_review` for membership grants.

**Scope sketch.**
- A `dsmb_drafter` specialist extending the IRB-packet workflow.
- A new `ScopeType.DSMB` value in RBAC + a `dsmb.view_unblinded` permission gated at the eCRF query layer (not the UI).
- A blinded-export endpoint that strips Allocation.arm + treatment-coded items before download.
- An interim-analysis cadence config (kick-off + per-meeting checklist).

**Dependencies + sequencing.** Randomisation / IRT (P0) ✅ landed with the blinding-mode infrastructure. RBAC scopes (P0) ✅ landed. Direct dependency = the existing PI code-break flow (which we use for emergency unblinding) needs to be split from the routine DSMB unblinding flow (which doesn't trigger a study-wide code-break).

---

### 6. Research-gap analysis specialist

**Phase:** Synthesis · **Effort:** M · **Status:** 📝 planned

**What it is.** Given a body of literature (a finalised SR or living-review evidence pile), surface where the evidence base is thin — by population, intervention, comparator, outcome, geography, or study design. The output is a structured "evidence gap map" — a 2D grid of populations × interventions with per-cell weight (n_studies × certainty) — plus a written narrative pointing to the cells that are practice-relevant but under-evidenced.

**Why a researcher or funding body would want it.** Funders (NIH, NIHR, Wellcome) and methods groups (Cochrane, JBI) increasingly require "evidence-gap maps" as a deliverable. Academic researchers use them to justify a new RCT application. Until now the platform synthesises evidence but doesn't tell you where it's missing.

**What it unlocks.** Funder-facing output. Closer integration with the SR screening output (an evidence gap is a different read of the same Inclusion / Exclusion table). A natural cross-handoff into trial design (a gap-map row reads as a PICOT for the SAP drafter).

**Scope sketch.**
- A new `research_gap` specialist with intake = a finished SR / meta-analysis JSON.
- A `GapMap` document with rows = populations, columns = interventions, cells = `{n_studies, certainty_band, has_practice_relevance}`.
- An evidence-gap PDF renderer (heatmap + ranked priority list).
- A handoff seed from `meta_analysis` and `sr_protocol` cards.

**Dependencies + sequencing.** Meta-analysis ✅, SR screening ✅, GRADE ✅ all in place. No blocker; promoted to "next P1" once one customer pulls.

---

### 7. Press-release / institutional-comms drafter

**Phase:** Dissemination · **Effort:** S · **Status:** 💡 proposed

**What it is.** Embargoed press releases for big-result publications + plain-language institutional-comms drafts for grant-funders / sponsor PR teams. Composes from the manuscript drafter's IMRaD output the same way the lay summary specialist composes from the evidence intake.

**Why a researcher or institution PR office would want it.** Every accepted manuscript at a top-tier journal triggers a parallel comms cycle: embargoed press release for journalists, plain-language institutional Q&A, sponsor-branded summary for the funder. Today the comms office writes each from scratch.

**What it unlocks.** Adjacent to the existing manuscript drafter, with minimal new infrastructure. Quick win when one team pulls.

**Scope sketch.**
- A new `press_release_drafter` specialist (variants for embargoed-academic / sponsor-branded / patient-facing).
- A handoff seed from the manuscript drafter's terminal card.
- A pre-canned templates per audience (academic / sponsor / patient).

**Dependencies + sequencing.** Manuscript drafter (P1) ✅, lay summary specialist (P2 #2) ✅ both in place. Trivial. Pick up when a comms office customer signals interest.

---

## Pickable follow-ups by subsystem

Concrete, well-scoped tasks we deliberately deferred during the P0 / P1 / P2 sweep. Each one is small enough to be a one-engineer slice. Items are grouped by subsystem so a team member can scan their area and pick the one that fits.

### Account layer (Sprints A1 + A2 + A2.5 + A3 ALL SHIPPED 2026-06-01)

The structural layer (Account → ClinicalTrial → AccountSite, with TrialSite + AccountMember joins) shipped 2026-06-01. A2 wired the new layer into existing flows; A2.5 closed the chat-handoff loop so threads auto-bind to Trial artefact slots end-to-end; A3 walked all 13 Phase-04 demos for resumability, found one dominant gap (deep-link navigation from `/accounts.html` to the Phase-04 surfaces), and closed it. ALL three sprints + audit shipped 2026-06-01.

- **Sprint A2 — wire Account / Trial context into existing flows.** ✅ Shipped 2026-06-01. What landed: cross-store resolver (`services/trial_context.py` walks Deployment → Study → Trial → Account); auto-status hooks on the 4 lifecycle events (`design → draft` on EcrfStudy form publish, `draft → deployed` on StudyDeployment create, `deployed → locked` on study lock, `locked → deployed` on unlock) — never regresses, never touches archived; `POST /api/ecrf/studies` accepts optional `trial_id` + falls back to a fresh Trial under the Default Account; `GET /api/deployments/{id}/trial` cross-store resolver endpoint; `POST /api/accounts/{id}/transfer-ownership` (promotes new owner + demotes previous to 'admin'); `POST /api/trials/{id}/artefacts` binds a Thread to one of 5 artefact slots; collector.html shows the current Trial badge above the deployment selector with status pill + sponsor + indication + deep-link to /accounts.html.
- **Sprint A2.5 — chat handoff seeds carry trial context.** ✅ Shipped 2026-06-01. What landed: `Thread.trial_id` additive nullable FK; `POST /api/threads` accepts optional `trial_id` (with account-membership check); `_runHandoff` in index.html reads parent thread's trial_id and forwards it so children inherit trial context for free; `/accounts.html` trial-detail gains 5 "Draft <kind>" CTAs (registration / IRB / SAP / CSR / manuscript) that spawn a trial-bound thread + redirect to `/?thread=<id>&seed=<msg>` for auto-send; index.html gains a URL-param handler that loads the thread + auto-sends the seed (also makes the existing accounts.html "Open thread" deep-links actually work); dispatch.py runs `_maybe_autobind_artefact` after every turn — when a Thread is trial-bound AND the assistant output is one of `{registration_document, irb_document, sap_document, csr_document, manuscript_draft}` AND the slot is empty, the binding is written automatically; idempotent (never overwrites a populated slot), best-effort (binding failures log + drop without blocking the response); `lay_summary` deliberately NOT in the mapping (no `lay_summary_thread_id` column on Trial yet — deferred). 14 new tests (1480 total).
- **Sprint A3 — persistence + resume audit across Phase-04 demos (X1 → X13).** ✅ Shipped 2026-06-01. Walked all 13 Phase-04 demos. **Finding:** every demo's STATE is correctly DB-persistent (reload-survives + restart-survives). The 24h SAE timer (X4) is wall-clock based on `reported_at`. The reminder scheduler (X7) is a stateless poller over DB rows; APScheduler restart-safe via `SentReminder` UNIQUE constraint. PRE-COMMIT browser state in X1 ("Draft forms" AI response) is by-design ephemeral — operator must accept before persistence. NO state-persistence gaps found. **Single resumability gap:** `/accounts.html` trial detail couldn't deep-link the operator into the Phase-04 surface they were working in; collector.html / ecrf.html / multisite.html had no `?deployment=<id>` / `?study=<id>` URL-param honouring, so even a deep-link wouldn't pre-select. **Patches:** `TrialDetailView` extended with `studies: list[StudyRefView]` carrying per-study deployments + lock state (cross-store read in one round-trip); `/accounts.html` trial-detail gains a "Resume work" section listing every Study with "Open in eCRF designer" + every Deployment with "Open collector" + "Multi-site rollup" buttons (each with a locked-pill when applicable); collector.html / ecrf.html / multisite.html each honour their respective URL params on mount, pre-selecting the picker + firing the load function so the operator lands on their working context in one click. 10 new tests (1490 total).
- **RoleAssignment widening across hierarchies.** Today an `account` grant doesn't satisfy a bare `study` check (and vice versa) — they live in separate scope hierarchies. A widening rule (`account` ⊃ `trial` ⊃ `study` ⊃ `site`) would let an account-admin role automatically grant `study.read` on every Trial's Deployment. **Benefit:** fewer per-deployment role grants for institutional admins.
- **Cross-store consistency check.** A health endpoint that walks `EcrfStudy.trial_id → ClinicalTrial.account_id` + `StudyDeployment.research_study_id → EcrfStudy.id` and reports any orphans (clinical-store Site rows whose `account_site_id` no longer exists, deployments whose study has been deleted, etc). **Benefit:** drift detection for the cross-store relations.

### User administration (Sprint U1 shipped 2026-06-01)

The platform's existing invite endpoint (`/api/admin/users`) is bare — global-scoped flat role list, no profile capture, no separation-of-duties enforcement, no delegation log. U1 introduces a regulatory-aware admin surface that wraps Cognito invite + enforces ICH E6 + Part 11 expectations at grant time.

- **Sprint U1 — backend foundations.** ✅ Shipped 2026-06-01. What landed: 4 new tables — `UserProfile` (title / name / credentials / medical license / GCP training / CV / financial disclosure / onboarding gate / suspension), `DelegationLogEntry` (Trial-scoped per ICH E6 §4.1.5 + 21 CFR §312.62), `TrainingRecord` (free-form training credentials with optional expiry); `PendingInvitation.assignments_json` additive column for scope-aware grants (`[{role, scope_type, scope_id}, …]`); `services/user_admin.py` with REQUIRED_FIELDS per role + SAME_STUDY_CONFLICTS matrix + ROLE_CATALOGUE descriptors (all anchored to Part 11 §11.10(d), ICH E6 §4.1–5.19, 21 CFR §54 / §312.62, EU CTR Art. 49); `detect_grant_conflicts` + `missing_required_fields` pure-fn validators; `UserAdminRepository` with profile upsert / suspend / reactivate / scoped invitation / training / delegation CRUD; `resend_cognito_invitation` via boto3 `AdminCreateUser MessageAction=RESEND`; 16 endpoints under `/api/user-admin/*` covering invitations (create/resend/revoke), users (list/detail/profile/suspend/reactivate), role-assignments (grant with SoD check + revoke), training-records, delegation-entries, roles-catalogue, separation-of-duties, check-grant pre-flight; `resolve_login` matcher consumes `assignments_json` (new) with fallback to `roles_json` (legacy) so existing PendingInvitation rows still work. SoD check fires at both invite-time + grant-time, returns the conflict pair + regulatory rationale in the 422 body, and is overridable via `override_rationale` string for audit. 38 new tests (1528 total).
- **Sprint U2 — admin UI (`/users-admin.html`).** ✅ Shipped 2026-06-01. Vanilla-JS page (same posture as accounts.html / admin.html) consuming the 16 U1 endpoints + 1 new convenience endpoint (`POST /invitations/by-email/resend`). Sections: scope+role+status filter strip; users table with role-pill badges + status pill + profile-completeness counter; user detail panel showing profile / assignments / missing-required-fields warnings / suspend+reactivate buttons / resend-invite for not-yet-logged-in users; invite modal with multi-row scoped-assignment editor + intra-invitation SoD check via the loaded matrix + override-rationale textarea; grant modal with `POST /check-grant` pre-flight + override; bulk CSV import (drag-drop, parse client-side, group by email, "Invite all" loops `POST /invitations` with per-row status pills); roles-catalogue drawer driven by `GET /roles-catalogue`; sidebar link in index.html gated on `user.manage`. 14 new tests (1542 total). Static-file content pins for key handlers + API path references catch accidental removal in future refactors.
- **Sprint U3 — onboarding flow (`/onboarding.html`).** Post-login wizard: required-fields capture per role, GCP certificate + CV upload to S3, e-signature agreement, delegation log entry capture. ENFORCEMENT gate: `/auth/me` returns `onboarding_required: true` when `UserProfile.onboarding_completed_at IS NULL` AND any held role's required fields are missing; frontend redirects until complete. **Benefit:** users can't operate the platform without their regulatory record on file (matches ICH E6 §4.2.4 + Part 11 §11.10(d)).
- **Sprint U4 — audit + reports.** Per-Trial delegation log PDF; per-site training matrix; user audit log (every grant / revoke / suspend / override_rationale); GCP expiry alerts. **Benefit:** auditor-ready exports for monitoring visits + inspections.

### Auth + RBAC

- **Per-user / per-tier daily token caps.** Today the cap is global (`config/settings.context_window_messages` + `summarize_after_messages`). Tier-based ceilings would let an institution give researchers a generous budget while capping students at a teaching tier. **Benefit:** institutions can buy a single deployment for a mixed researcher + student audience without one tier exhausting the other's budget.
- **Blinded views for non-DSMB roles.** The randomisation subsystem masks Allocation.arm in single / double / triple blind deployments via response-shape. A broader blinded-view contract that strips arm-coded items from form data (not just allocation) would let monitors and DM see counts without seeing arms. **Benefit:** stricter separation of duties for blinded trials.

### Library + RAG (R0–R4 shipped)

- **R5 — domain ontology integration.** MeSH / SNOMED-CT / UMLS expansion at query time so a search for "diabetes" picks up "type 2 diabetes mellitus" + ICD-10 codes. **Benefit:** higher recall on the library search without over-precision tuning.
- **R6 — passage rerank.** A cross-encoder rerank pass on the top-N hybrid hits. **Benefit:** the top-3 RAG hits become reliable enough for direct quoting in the meta-analysis intake card.
- **OCR for scanned PDFs.** Today PDF upload assumes a born-digital file. Scanned uploads land with empty body text. **Benefit:** opens the cabinet of pre-2000 references that only exist as scans.

### SR screening

- **Title-only screening tier** as a pre-pass before the full title+abstract review. Cuts reviewer load when the initial PubMed hit list is large. **Benefit:** halves reviewer time for the first-pass of a 10k-candidate review.
- **Reviewer chat / annotations on the adjudication queue.** Today the adjudicator sees the conflicting yes/no choices but can't see *why* each reviewer chose. Free-text rationale per vote would close that gap. **Benefit:** faster + more transparent conflict resolution.
- **Living-protocol re-screening when inclusion criteria change.** Today the criteria are pinned at project create. Editing them mid-screening doesn't re-evaluate already-screened candidates. **Benefit:** living-review projects can refine their criteria without manually re-screening everything.

### Sample-size + SAP drafter

- **Non-inferiority + equivalence formulas.** Today the calculator covers superiority (two-proportions / two-means / time-to-event / paired). Non-inferiority and equivalence are conceptually small extensions of the same closed forms. **Benefit:** lets the SAP drafter cover non-inferiority trials (a large fraction of biosimilar and device studies).
- **Adaptive design support** — group-sequential boundaries (O'Brien-Fleming, Pocock) + sample-size re-estimation. **Benefit:** modern adaptive trials reduce sample size 20-40% vs fixed-design; we should support drafting them.
- **Sample-size sensitivity table.** Vary α / β / dropout / effect together and emit a small grid showing N for each combination. **Benefit:** reviewers (IRB, sponsor) often ask for this — saves a re-run cycle.

### Trial-registration drafter

- **Additional registries** — WHO ICTRP (sponsor-of-last-resort), ANZCTR (Australia/NZ), ChiCTR (China), ISRCTN (UK). Same intake shape; different per-registry field names. **Benefit:** lets multi-region trials register everywhere from one workflow.
- **IND / IDE submission packet.** Pre-IND meeting briefing + IND / IDE form fields. Drafter shape parallel to the IRB drafter. **Benefit:** opens the FDA pre-trial workflow to the same customers using us for IRB.
- **Cross-registration deduplication.** When a trial registers in CT.gov AND EU CTR, the platform should warn about field-value divergence before the operator submits. **Benefit:** prevents the "two registry entries disagree" finding that audits flag.

### IRB / ethics drafter

- **Investigator CV insert / FDA Form 1572.** The 1572 is a mandatory IND attachment naming investigators, sub-investigators, facilities, IRB, and pertinent CVs. **Benefit:** completes the start-up packet today's lite version doesn't cover.
- **Site supplementary packets.** Per-site investigator commitment letters, facility audits, site personnel rosters. **Benefit:** reduces the per-site setup work that today is a copy-paste exercise.
- **HIPAA authorisation form.** Required alongside the ICF for US trials handling PHI. Same compliance-checked schema as the ICF. **Benefit:** completes the US-trial start-up packet.

### Randomisation / IRT

- **Schedule extension mid-study.** Today the schedule is generated once at study start. Adding entries when enrolment exceeds the original target requires a regenerate + audit-comment. **Benefit:** common request for over-enrolling trials.
- **Sponsor-notification webhooks on code-break.** When a PI breaks blinding, the sponsor's safety team should get an immediate ping. **Benefit:** closes the loop on the IND safety report timer.
- **Response-adaptive randomisation.** Allocate to the better-performing arm as evidence accumulates. Common in oncology dose-finding. **Benefit:** opens the platform to platform / basket / umbrella trial designs.
- **Block-size shuffle randomisation.** The current permuted-block algorithm uses a fixed block size; varying it randomly prevents site personnel from inferring the next allocation. **Benefit:** stricter blinding integrity.

### AE / SAE + protocol-deviation

- **SUSAR detection.** SAE with `unexpected=True` AND `relationship >= probable` is the SUSAR subset that triggers the 7 / 15-day expedited regulatory clock. We have the fields; we don't currently flag the subset. **Benefit:** automatic surfacing of the events that have the tightest regulatory deadline.
- **MedDRA license-gated dictionary.** Real MedDRA Preferred-Term validation requires a license. The field today is free-text. **Benefit:** regulator-acceptable PT coding without an external dictionary lookup.
- **Real-time FAERS / EudraVigilance submission gateway.** Today we draft the FDA 3500A as a PDF; submission is the operator's job. An E2B(R3) XML emitter + a gateway adapter would close the loop. **Benefit:** the platform becomes the end-to-end PV system for early-phase trials.

### Recruitment / screening log

- **CONSORT 2010 flow diagram PDF auto-export from the recruitment funnel.** We compute the counts; today the PDF is rendered from operator pastes in the trial-stats specialist. A direct flow-diagram export from the funnel data would close the loop. **Benefit:** automatic CONSORT compliance.
- **Real-time recruitment forecasting.** Given the per-week per-site trajectory, project the date at which target enrolment lands. **Benefit:** sponsors can spot under-recruiting sites before they become a deadline risk.
- **Country / region rollup** as an additional dimension on the funnel. Per-week × per-country × per-site stage counts. **Benefit:** multi-country trials get a cleaner sponsor-level view.

### Visit scheduling + reminders

- **SMS via Twilio.** Today SMS reminders are stubbed (`SentReminder.status='skipped'`). Wiring Twilio closes the channel. **Benefit:** participants in regions with poor email reliability get reminders.
- **ICS calendar attachment** on email reminders. Subjects can add the visit to their phone calendar with one tap. **Benefit:** measurably reduces missed-visit rate.
- **Visit-window violation auto-deviation.** When a planned visit ages past its window, auto-create a protocol-deviation row of category=`visit_window`. **Benefit:** closes the loop between the visit calendar and the deviation log; today the operator has to record the deviation manually.
- **Locale-aware reminders** (timezone of the participant, not the deployment). **Benefit:** cross-region trials don't send reminders at midnight local time.

### Source-document extraction

- **Direct EHR pull via FHIR.** Today the operator uploads a file. Pulling directly from a hospital FHIR endpoint is a natural extension (and partially proven by the lab-feed FHIR parser). **Benefit:** removes the manual export step at sites that have FHIR endpoints.
- **OCR for scanned source documents.** Same OCR engine as the library R6 follow-up. **Benefit:** lets older studies use the extraction pipeline on legacy paper records.
- **FHIR / JSON ingest** for source documents (CSV-only today). **Benefit:** symmetry with lab-data feeds; same audit chain covers both.

### Drug accountability

- **`kit_id` pattern enforcement.** Catalogue carries the regex but the repo doesn't validate dispense events against it. **Benefit:** catches typo'd kit IDs at point of capture.
- **Per-subject compliance metric.** `sum(quantity_used) / sum(quantity_dispensed)` per subject. **Benefit:** monitors spot non-compliant subjects without computing it by hand.
- **Temperature-excursion CAPA auto-link.** When `DrugReceipt.temp_excursion_flag=True`, auto-create a protocol-deviation + CAPA. **Benefit:** the cold-chain break surfaces in the deviation log instead of just the receipt notes.
- **IRT integration for dispensation.** Today the kit is operator-selected; randomisation could auto-pick the next kit from the allocated arm. **Benefit:** removes the blinded-site-personnel-could-infer-arm leak when the kit packaging differs by arm.
- **Destruction / recall workflows.** End-of-study destruction events + sponsor-recall workflows aren't modelled today. **Benefit:** completes the GCP-mandated IP lifecycle.
- **Parent-child dosing relationship.** Some trials dose two IPs together (active + booster, or drug + matched placebo). **Benefit:** lets the platform serve those trial designs cleanly.
- **CDISC EX domain integration.** Dispensations should auto-feed the SDTM EX (exposure) domain the same way LabResult feeds LB. **Benefit:** the CDISC bundle picks up dispensation events automatically.

### Lab-data feeds

- **Unit-conversion table for LBSTRESN.** Today the cascade emits `LBSTRESN = LBORRES` (no conversion). A deploy-time mg/dL ↔ mmol/L dictionary would normalise across glucose, creatinine, cholesterol, etc. **Benefit:** the SDTM LB bundle becomes regulator-ready without manual unit harmonisation.
- **LBTESTCD ↔ LOINC mapping table.** Source messages carry LOINC codes (HL7 / FHIR) that don't always align with the platform's `lb_test_codes.json` aliases. **Benefit:** sponsors with their own LOINC dictionaries map cleanly.
- **MLLP / TCP listener.** Hospitals that can only ship HL7 over TCP need a long-running listener; today only HTTP works. **Benefit:** opens the platform to hospital lab integrations that don't support REST.
- **Hospital → trial subject_code mapping table.** Source messages typically carry the hospital MRN; we have `subject_code_hint` but the operator must pre-map. **Benefit:** removes manual translation at every ingest.
- **Per-batch error report download.** Today warnings are returned in the API response. A downloadable PDF / CSV would close the audit loop. **Benefit:** sponsors retain a per-batch reconciliation artefact.
- **Streaming parse for > 20 MB payloads.** Multi-day central-lab dumps exceed the limit today. **Benefit:** supports the central-lab daily-dump pattern many sponsors actually run.
- **Auto-rederive CDISC LB after lab backfill.** Linking a Subject today doesn't auto-rerun the CDISC pipeline. **Benefit:** removes a manual operator step.

### Living-review subscriptions

- **Watch-runner auto-tally trigger.** Today a vote drives the tally; if no member votes, the run goes unflagged. A periodic check that surfaces "5 runs awaiting your vote" via the existing notification stream would close the loop. **Benefit:** group subscriptions don't go silent when members forget to vote.
- **Per-paper voting** as an alternative to run-level voting. **Benefit:** committees that want to drill into individual papers get that granularity.
- **Email digest for pending votes.** Reuses the SES integration the reminder subsystem already wires. **Benefit:** members get a weekly nudge.
- **Quorum-clear cross-handoff** into manuscript / GRADE / SR protocol drafters. **Benefit:** when a committee decides a run is practice-changing, one click composes the next-step artefact.
- **Org-level subscriptions** (anyone in the institution sees them). **Benefit:** an institution can run a single watch for a whole department without manually inviting every member.
- **Auto-pause on health signal** (e.g. N consecutive quorum-clears). **Benefit:** flags overactive watches whose triage threshold needs tuning.

### Multi-site rollup

- **Per-site IP allocation.** `DrugReceipt.site_id` already exists; aggregating dispensations + returns by site would let the low-stock signal migrate from deployment-totals down to per-site cards. **Benefit:** monitors see which sites are running low.
- **Monitor-visit scheduling table.** Today last-SDV is a proxy. A dedicated `MonitorVisit` table (or a free-text "last seen" log) would surface a more direct signal. **Benefit:** sponsors can prioritise their next site visit.
- **Time-series sparklines** on each KPI card. Per-week deltas instead of point-in-time counts. **Benefit:** trending issues surface earlier.
- **Per-site cost attribution** by linking thread spend to deployment + site. **Benefit:** sponsor finance teams see per-site burn.
- **Filters / sort / search** on the dashboard. **Benefit:** scales gracefully past a handful of sites.
- **Drilldown links** from each KPI count into the corresponding eCRF endpoint. **Benefit:** one-click navigation from "8 open AEs" to the AE list filtered to that site.

### Budget + cost rollup

- **Per-study / per-SR-project cost attribution.** Threads aren't linked to SR projects today; an explicit linkage would let the rollup attribute spend per project. **Benefit:** sponsors see cost per evidence artefact.
- **Dynamic pricing fetch from AWS.** Today pricing is constants-in-code. A scheduled scrape would auto-update. **Benefit:** prices reflect AWS changes the day they happen.
- **Per-day / per-week activity sparkline** on the dashboard. **Benefit:** spending trends are visible.
- **Budget alerts / quota gauges.** Already-shipped per-user quota lives in `services.quota`; surfacing it alongside the spend cards would close the loop. **Benefit:** users see "you've used 60% of your monthly budget" before they hit the limit.
- **Sponsor-attribution / cost-centre tagging** on threads. **Benefit:** clinical research organisations bill the right sponsor for the right thread.
- **Cost-per-artefact rollup.** "How much did this manuscript cost to draft?" Requires linking manuscript final-document turns back to their thread cost trail. **Benefit:** sponsors compare manuscript spend across journals.
- **Invoicing-grade pricing snapshots.** Snapshot prices at write time so historical costs are immutable. **Benefit:** required if the platform becomes a billing system, not just transparency.

### Lay summaries

- **Per-language Flesch-Kincaid formulas.** FK works for English; Spanish needs Fernández-Huerta, French needs Kandel-Moles, German needs LIX. **Benefit:** the multilingual claim becomes regulator-defensible for ES / FR / DE submissions.
- **PDF cover art / illustrations.** CISCRP-style patient materials usually carry an illustration band. **Benefit:** higher patient engagement.
- **EMA Reg (EU) No 536/2014 templated lay summary.** EMA has a specific 10-section structure. **Benefit:** EU trial closeout becomes a one-click export.
- **ICF supplement handoff** into the IRB drafter. **Benefit:** the recruitment-language lay summary composes into a consent supplement automatically.
- **Glossary auto-extraction.** Today the model picks glossary terms. An NLP pass that flags jargon over a target reading level would help. **Benefit:** more reliable plain-language output.
- **Frontend handoff buttons on CSR + IRB cards.** Today only the meta-analysis card carries the "Draft lay summary" button. **Benefit:** results and recruitment intakes get one-click handoffs too.
- **Plain-language style validator.** Reject phrases like "you should" / "you must" before persistence. **Benefit:** stricter guarantee against medical-decision language regressions.

### Citations

- **OAuth Zotero web-API integration.** Today round-trip is via file upload. Direct library sync would close the loop. **Benefit:** "import my Zotero library" becomes one click.
- **EndNote XML format.** Some institutions use EndNote XML rather than BibTeX or RIS. **Benefit:** completes universal coverage.
- **CSL JSON** (Citation Style Language). Same purpose, different ecosystem. **Benefit:** completes universal coverage.

### Portfolio dashboard

- **Time-series sparklines** on the hero cards. **Benefit:** activity trends surface.
- **Filters / sort / search.** **Benefit:** scales to large user counts.
- **Drilldown links** from each card into the source surface. **Benefit:** one-click navigation.

### CDISC mapping (SDTM → ADaM → TLF)

- **MedDRA license-gated mappers** for AE / MH preferred-term coding. **Benefit:** the regulator-acceptable submission unblocked.
- **WHODrug license-gated mappers** for CM medication coding. **Benefit:** same as above for concomitant meds.
- **Additional ADaM datasets** — ADAE, ADCM, ADLB, ADVS, ADQS. **Benefit:** each one is an FDA-recommended dataset for typical submissions.
- **More TLF templates** — subgroup forest plots, Kaplan-Meier curves overlayed by ADSL strata, swimmer + waterfall as standard outputs. **Benefit:** richer TLF library reduces operator paste-into-CSR work.
- **FDA ESG / EMA CESP submission gateway.** Today we export the bundle; submission is the operator's job. A direct gateway adapter is the next step. **Benefit:** end-to-end submission inside the platform.

### CSR drafter

- **Narrative sections** — Introduction, Discussion, Overall Conclusions. Today these ship as `[Operator to complete]` placeholders. **Benefit:** completes the ICH E3 deliverable so the operator doesn't write three chapters of prose.
- **Sponsor signature + approval block.** Auto-inserted at the end of the CSR for the sponsor medical-monitor and biostatistician. **Benefit:** the document becomes single-sign approval-ready.
- **Module 5 (Common Technical Document) integration.** The CSR is one of many Module 5 deliverables; cross-references to the others. **Benefit:** the platform composes a Module 5 bundle, not just the CSR.

### Manuscript drafter

- **Journal-specific style validators.** NEJM 250-word introduction, JAMA structured abstract, Lancet word limits per section. **Benefit:** the draft lands closer to the journal's requirements first time.
- **ORCID author lookup.** Auto-populate author affiliations + ORCIDs at draft time. **Benefit:** removes a manual lookup step for every co-author.
- **Conflict-of-interest statement auto-generation** from the author list + a sponsor disclosure table. **Benefit:** standard COI statement format on every manuscript.
- **Cross-journal preprint sync.** When the manuscript is finalised, push to medRxiv / SSRN via their API. **Benefit:** preprint becomes a one-click step inside the platform.

### Trial-statistics specialist

- **Restricted mean survival time** as an alternative to the Cox HR for non-proportional hazards. **Benefit:** RMST is the FDA-preferred summary when HR is invalid.
- **Repeated-measures Bland-Altman** for method-comparison studies. **Benefit:** opens the platform to diagnostics + device validation studies.
- **Bayesian posterior probability** of treatment effect. **Benefit:** modern Bayesian-decision trials need this; classical p-values aren't enough.

### Beyond-forest-plot visualisations

- **Bubble plot for meta-regression.** Effect size on x, moderator on y, study weight as bubble size. **Benefit:** the meta-regression specialist isn't shipped yet; this visualisation is part of a future meta-regression slice.
- **Doi plot for publication bias.** Alternative to the funnel plot — abandoning the funnel's assumption of symmetric reporting. **Benefit:** more robust publication-bias signal for asymmetric outcomes.
- **Tipping-point analysis viz.** Vary the imputed-missing assumptions to find the value at which the conclusion flips. **Benefit:** sensitivity analysis around missing-data handling.

### NMA

- **PyMC in the sandbox image.** The Bayesian backend is gracefully skipped today because PyMC isn't in the sandbox image. **Benefit:** the Bayesian NMA becomes available without sandbox rebuild.
- **Component network meta-analysis.** Decompose complex interventions (e.g. CBT + drug) into single components. **Benefit:** lets the NMA cover composite interventions cleanly.
- **IPD-NMA integration.** The IPD specialist and NMA specialist are independent today. An integrated IPD-NMA would pool subject-level data across a network. **Benefit:** state-of-the-art network synthesis.

### IPD meta-analysis

- **Binary / TTE interaction p.** Today subgroup interaction p is continuous-only. Extending to binary (interaction via logit OR) + TTE (interaction via Cox HR ratio) closes the gap. **Benefit:** subgroup analysis covers all three outcome types.
- **Multi-level model with random slopes.** Today MixedLM uses random intercepts. Random slopes per trial would model differential treatment effects more flexibly. **Benefit:** better fit for heterogeneous trial sets.
- **Aggregate-vs-IPD comparison plot.** Side-by-side the IPD pooled estimate with the aggregate-data pool. **Benefit:** quantifies the value of IPD over published summaries.

### GRADE + PRISMA

- **Automated cross-checks** against the meta-analysis source. If GRADE says "no serious heterogeneity" but the source meta-analysis I² > 50%, flag it. **Benefit:** catches operator inconsistencies before sign-off.
- **AMSTAR-2 critical-appraisal integration.** AMSTAR-2 is the standard quality-appraisal tool for systematic reviews. **Benefit:** the platform composes the quality appraisal alongside GRADE.

### Recruitment / screening logs

(See **Recruitment / screening log** subsection above — same items.)

### eCRF validation pack

- **Password-prompt UI at signing.** Today the password reauth is API-driven; collector.html doesn't yet have a signing surface. **Benefit:** completes the Part 11 §11.200 path for browser-based signing.
- **21 CFR Part 11 §11.50 displayed-signature manifestation.** The signed-name + datetime should display alongside the signed form. **Benefit:** regulator-required visible attribution.
- **Annual re-validation pipeline.** The IQ / OQ / PQ pack is generated on demand. A scheduled annual run + immutable archive would close the loop. **Benefit:** institutions don't have to remember to re-validate.

### Reports / specialists infrastructure

- **More report types** — every specialist that ships only a chat card today (sap_drafter, registration_drafter, irb_drafter, csr_drafter, grade_drafter, trial_stats, nma, ipd, lay_summary) has a PDF / DOCX renderer; refining their visual design is incremental polish. **Benefit:** sponsor-facing deliverables look more like the consultancy output they're replacing.
- **Report telemetry** — count downloads per kind + report build time. **Benefit:** tells us which reports are most loaded and need optimisation.

---

## Already shipped

The starting state for this roadmap. Anything listed here is in production today.

| Phase | Item | Shipped | Memory |
|---|---|---|---|
| Cross-cutting | OAuth2 / OIDC via Cognito + multi-tenant pool | 2026-05-23 | [project_auth_progress.md](../memory/project_auth_progress.md) |
| Cross-cutting | RAG v1 (publication cache + Titan v2 embeddings + hybrid `rag_search` + PDF upload + library UI; R0–R4 done) | 2026-05-23 | [project_rag_plan.md](../memory/project_rag_plan.md) |
| Cross-cutting | Multi-tenant Cognito (one AWS account hosts many CRA installs) | 2026-05-23 | [project_multi_tenant_cognito.md](../memory/project_multi_tenant_cognito.md) |
| Cross-cutting | RBAC (scoped roles + permission matrix + skill gating + Student tier + frontend gating) | 2026-05-29 | [project_rbac_design.md](../memory/project_rbac_design.md) |
| Synthesis | SR title/abstract + full-text screening UI (R1/R2/Adjudicator + AI-assist + PRISMA SVG) | 2026-05-29 | [project_sr_screening.md](../memory/project_sr_screening.md) |
| Synthesis | PRISMA flow diagram generation | 2026-05-29 | (with SR screening) |
| Design | Sample-size + power calculator | 2026-05-29 | [project_sap_drafter.md](../memory/project_sap_drafter.md) |
| Design | Statistical Analysis Plan (SAP) drafter | 2026-05-29 | [project_sap_drafter.md](../memory/project_sap_drafter.md) |
| Execution | AE / SAE workflow (ICH E2A auto-classification + 24h timer + FDA 3500A draft + CAPA lifecycle) | 2026-05-29 | [project_safety.md](../memory/project_safety.md) |
| Execution | Protocol-deviation tracking + CAPA | 2026-05-29 | (with safety subsystem) |
| Analysis | Manuscript drafter (IMRaD) + reviewer-response loop | 2026-05-29 | [project_manuscript_drafter.md](../memory/project_manuscript_drafter.md) |
| Analysis | CDISC SDTM mapping → ADaM → TLF (7 SDTM domains + ADSL + ADTTE + Define-XML v2.1 + SAS Transport v5) | 2026-05-29/30 | [project_cdisc_mapping.md](../memory/project_cdisc_mapping.md) |
| Execution | eCRF formal validation pack (CSV / IQ / OQ / PQ + study lock + password reauth) | 2026-05-30 | [project_validation_pack.md](../memory/project_validation_pack.md) |
| Start-up | Trial-registration drafter (CT.gov / EU CTR / CTIS) | 2026-05-30 | [project_startup_drafters.md](../memory/project_startup_drafters.md) |
| Start-up | IRB / ethics packet + ICF drafter (lite — synopsis + ICF; multilingual en/es/fr/de) | 2026-05-30 | [project_startup_drafters.md](../memory/project_startup_drafters.md) |
| Execution | Randomisation / IRT service (4 algorithms + blinding modes + code-break) | 2026-05-30 | [project_irt.md](../memory/project_irt.md) |
| Analysis | CSR (ICH E3) drafter (synopsis + 4 data sections) | 2026-05-30 | [project_csr_drafter.md](../memory/project_csr_drafter.md) |
| Analysis | GRADE Summary of Findings + PRISMA 2020 reporting checklist | 2026-05-30 | [project_grade_drafter.md](../memory/project_grade_drafter.md) |
| Analysis | Trial-specific statistical analysis specialist (KM / MMRM / Cox / binary / subgroup) | 2026-05-30 | [project_trial_stats.md](../memory/project_trial_stats.md) |
| Analysis | Beyond-forest-plot visualisations (funnel + waterfall + swimmer + GRADE chip) | 2026-05-30 | [project_beyond_forest_plots.md](../memory/project_beyond_forest_plots.md) |
| Execution | Recruitment / screening logs | 2026-05-30 | [project_recruitment_log.md](../memory/project_recruitment_log.md) |
| Execution | Visit scheduling + participant reminders (SES email; SMS deferred) | 2026-05-30 | [project_visit_schedule.md](../memory/project_visit_schedule.md) |
| Execution | Source-document extraction (EHR → eCRF / extraction table) | 2026-05-30 | [project_source_extraction.md](../memory/project_source_extraction.md) |
| Analysis | Network meta-analysis (frequentist + Bayesian opt-in) | 2026-05-30 | [project_nma.md](../memory/project_nma.md) |
| Analysis | Individual patient data (IPD) meta-analysis | 2026-05-31 | [project_ipd.md](../memory/project_ipd.md) |
| Cross-cutting | Citation-manager integration (BibTeX + RIS) | 2026-05-31 | [project_citations.md](../memory/project_citations.md) |
| Cross-cutting | Portfolio dashboard across reviews + trials | 2026-05-31 | [project_portfolio.md](../memory/project_portfolio.md) |
| Cross-cutting | Budget + cost rollup across studies | 2026-05-31 | [project_budget_rollup.md](../memory/project_budget_rollup.md) |
| Execution | Drug accountability (IP receipt → dispense → return + reconciliation) | 2026-05-31 | [project_drug_accountability.md](../memory/project_drug_accountability.md) |
| Dissemination | Patient-facing / lay summaries (3 source variants + host-side FK readability) | 2026-05-31 | [project_lay_summaries.md](../memory/project_lay_summaries.md) |
| Synthesis | Group-level living-review subscriptions (quorum-driven notifications) | 2026-05-31 | [project_living_review_subscriptions.md](../memory/project_living_review_subscriptions.md) |
| Cross-cutting | Multi-site / multi-tenant coordination rollup | 2026-05-31 | [project_multisite_rollup.md](../memory/project_multisite_rollup.md) |
| Execution | Lab-data feeds (HL7 v2 + CDISC LAB + FHIR; SDTM LB cascade) | 2026-05-31 | [project_lab_feeds.md](../memory/project_lab_feeds.md) |

Memory-index pointer: [memory/MEMORY.md](../memory/MEMORY.md) carries one-line summaries of each shipped item; commits land under `feat(<area>):` and `docs(<area>):` pairs.

---

## Designed but not yet built

Empty — every previously-designed item has landed. New design work for the strategic initiatives above will be linked here once docs exist.

---

## Maintenance

- Update on every shipped feature: move the row to "Already shipped" with its memory pointer.
- Add new follow-up items under "Pickable follow-ups by subsystem" as they're discovered during implementation. Keep them small enough to be one-engineer slices.
- Re-prioritise the strategic initiatives quarterly or when customer pull shifts. Promote a strategic initiative to a P0 / P1 / P2 build queue when a concrete customer commits.
- Link each strategic initiative to its design doc (`<area>-design.md`) once one exists.
- When a follow-up item lands, delete it from this doc and link it from the corresponding memory file's "What's deferred" section so the memory stays accurate.
