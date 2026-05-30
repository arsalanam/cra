# Roadmap

Comprehensive, prioritised tracker for what we have, what we're building, and the structural gaps we know about across the clinical-research lifecycle.

This doc lives alongside the customer-facing capability summary ([feature-guide.md](feature-guide.md)) and the per-subsystem design docs ([architecture.md](architecture.md), [ecrf-design.md](ecrf-design.md), [rbac-design.md](rbac-design.md)). The "On the roadmap" section in the feature guide is intentionally short and directional — **this document is where we track the full list, priorities, and dependencies**.

---

## How to read this

### Status

- ✅ **shipped** — running in production
- 🚧 **in flight** — actively being built
- 📐 **designed** — design doc exists, implementation pending
- 📝 **planned** — committed, not yet designed
- 💡 **proposed** — gap identified, scope TBD

### Priority

Priorities reflect **criticality of missing functionality from a clinical-research workflow perspective** — not engineering size or recency of request.

| Tier | Meaning |
|---|---|
| **P0** | **Blocker.** Without this we cannot serve a defined user segment for their primary use case. We are leaving real customers on the table. |
| **P1** | **High leverage / structural.** A meaningful capability gap that unlocks a new class of work. Worth building before further polish on existing workflows. |
| **P2** | **Important polish.** Improves an existing flow; not blocking new customers. |
| **P3** | **Strategic bet.** Larger TAM, longer build, higher uncertainty. Worth tracking; not next-up. |

### Effort

T-shirt size. **S** ≈ days, **M** ≈ a couple of weeks, **L** ≈ a quarter, **XL** ≈ multi-quarter / ongoing.

### Phase

Where the item sits in the research lifecycle:

`Synthesis` (literature → review) → `Design` (question → protocol → grant) → `Start-up` (regulatory → ethics → site init) → `Execution` (recruit → capture → query → monitor) → `Analysis` (data → stats → CSR → manuscript) → `Dissemination` (publish → review → press → archive) → `Cross-cutting` (auth, RBAC, portfolio, integrations).

---

## At a glance

| Phase | Item | Priority | Status | Effort |
|---|---|---|---|---|
| Cross-cutting | RBAC (scoped roles + permission matrix) | **P0** | ✅ shipped (RBAC-1+2+3) | L |
| Synthesis | SR title/abstract + full-text screening UI | **P0** | ✅ shipped 2026-05-29 | M |
| Synthesis | PRISMA flow diagram generation | **P0** | ✅ shipped 2026-05-29 | S |
| Design | Sample-size + power calculator | **P0** | ✅ shipped 2026-05-29 | S |
| Design | Statistical Analysis Plan (SAP) drafter | **P0** | ✅ shipped 2026-05-29 | M |
| Start-up | ClinicalTrials.gov / EU CTR registration drafter | **P0** | 💡 proposed | M |
| Start-up | IRB / ethics submission packet + ICF drafter | **P0** | 💡 proposed | L |
| Execution | Randomisation / IRT service | **P0** | 💡 proposed | M |
| Execution | AE / SAE workflow (detect, code, escalate) | **P0** | ✅ shipped 2026-05-29 | M |
| Execution | eCRF formal validation pack (CSV / IQ-OQ-PQ) | **P0** | ✅ shipped 2026-05-30 | L |
| Analysis | CDISC SDTM mapping → ADaM → TLF | **P0** | ✅ shipped 2026-05-29 (DM + AE + VS + LB + EX + CM + MH + ADSL + basic TLF; ADTTE / define.xml / XPT deferred) | XL |
| Analysis | CSR (ICH E3) drafter | **P0** | 💡 proposed | XL |
| Execution | Protocol-deviation tracking + CAPA | **P1** | ✅ shipped 2026-05-29 | M |
| Execution | Recruitment / screening logs | **P1** | 💡 proposed | S |
| Execution | Visit scheduling + participant reminders | **P1** | 💡 proposed | M |
| Execution | Source-document extraction (EHR → eCRF / extraction table) | **P1** | 📝 planned (feature-guide) | L |
| Analysis | Manuscript drafter (IMRaD) + reviewer-response loop | **P1** | ✅ shipped 2026-05-29 | L |
| Analysis | GRADE summary-of-findings + PRISMA reporting checklist | **P1** | 💡 proposed | M |
| Analysis | Trial-specific statistical analysis specialist (KM, MMRM, Cox) | **P1** | 💡 proposed | M |
| Analysis | Beyond-forest-plot visualisations (funnel, KM, waterfall, swimmer) | **P1** | 💡 proposed | M |
| Analysis | Bayesian / network meta-analysis | **P1** | 💡 proposed | L |
| Analysis | Individual Patient Data (IPD) meta-analysis | **P1** | 💡 proposed | L |
| Cross-cutting | Citation-manager integration (Zotero / EndNote / Mendeley) | **P1** | 💡 proposed | S |
| Cross-cutting | Portfolio dashboard across reviews + trials | **P1** | 💡 proposed | M |
| Synthesis | Group-level living-review subscriptions (quorum notifications) | **P2** | 📝 planned (feature-guide) | M |
| Dissemination | Patient-facing / lay summaries | **P2** | 📝 planned (feature-guide) | M |
| Execution | Drug accountability (IP receipt → dispense → return) | **P2** | 💡 proposed | M |
| Execution | Lab-data feeds (HL7 / CDISC LAB) | **P2** | 💡 proposed | L |
| Cross-cutting | Multi-site / multi-tenant coordination roll-up | **P2** | 💡 proposed | M |
| Cross-cutting | Budget + cost rollup across studies | **P2** | 💡 proposed | S |
| Design | Research-gap analysis specialist | **P3** | 📝 planned (feature-guide) | M |
| Start-up | DSMB / DMC charter + blinded views | **P3** | 💡 proposed | L |
| Analysis | HTA / payer-grade dossier generation | **P3** | 💡 proposed | XL |
| Execution | EHR / FHIR / OMOP integration for RWE | **P3** | 💡 proposed | XL |
| Execution | Central imaging upload + adjudicated review | **P3** | 💡 proposed | XL |
| Dissemination | Press-release / institutional-comms drafter | **P3** | 💡 proposed | S |
| Cross-cutting | Pharmacovigilance / post-market AE tracking | **P3** | 💡 proposed | L |

---

## Already shipped

The starting state for this roadmap. Anything listed here is in production today and doesn't carry a priority — only future work does.

| Item | Where it lives |
|---|---|
| 7 guided workflows (meta-analysis, search strategy, SR protocol, RoB, general Q&A, living-review watch, eCRF/EDC) | [feature-guide.md](feature-guide.md) §1–7 |
| Downloadable PDF + DOCX reports for meta-analysis, SR/MA protocol, and RoB | `src/research_assistant/reports/`, endpoint `GET /api/threads/{tid}/report/{kind}/{fmt}` |
| eCRF / EDC subsystem (E0–E6: cache, capture, edit-checks + queries, authoring UI, EDC + ePRO, e-signatures + lock, DB-level audit immutability + SDV) | `src/research_assistant/web/{ecrf,edc,epro}.py`, [ecrf-design.md](ecrf-design.md) |
| Publication cache + Titan v2 embeddings + hybrid `rag_search` + PDF upload + library UI (R0–R4) | `src/research_assistant/tools/clinical/rag_search.py`, `services/library/*` |
| OAuth2 / OIDC via Cognito; multi-tenant pool support; role-based admin gating; invite flow | `src/research_assistant/web/auth.py`, `scripts/cognito_setup.py` |
| RBAC (scoped roles + permission matrix + skill gating + ownership) — RBAC-1/2/3 all shipped 2026-05-29, including Student tier | `src/research_assistant/auth/rbac.py`, `web/authz.py`, `agent/dispatcher.authorize_workflow`, role-admin UI in `admin.html` |
| SR screening — title/abstract + full-text dual review (R1/R2/Adjudicator) + AI-assist + PRISMA flow diagram (SVG); 14 routes under `/api/sr/*`, `sr.html` UI | `src/research_assistant/persistence/sr_repository.py`, `web/sr.py`, `agent/specialists/sr_screening_assist.py`, `web/static/sr.html` |
| Sample-size calculator + SAP drafter — four formulas (two-proportions / two-means / time-to-event / paired) + ICH-E9-shaped Statistical Analysis Plan with PDF/DOCX export; multi-step PICOT → sample_size → analysis_plan → sap_document workflow | `src/research_assistant/tools/data_science/sample_size.py`, `agent/specialists/sap_drafter.py`, `reports/sap.py`, `domain/sap.py` |
| eCRF safety subsystem — AE/SAE auto-classification (ICH E2A criteria) + 24h reporting timer + overdue SAE dashboard + MedDRA-PT field (free-text MVP; deploy with license for validation) + FDA 3500A draft PDF/DOCX; protocol-deviation log with major/minor/critical classification + CAPA lifecycle; subject-level safety panel in collector.html | `src/research_assistant/persistence/clinical/safety_rules.py`, `persistence/clinical/repository.py` (AE/deviation/CAPA methods), `reports/sae_3500a.py`, `web/edc.py` (16 safety endpoints), `web/static/collector.html` safety panel |
| Manuscript drafter (IMRaD) + reviewer-response loop — 3-stage workflow (intake → draft → reviewer-response) targeting NEJM/Lancet/BMJ/JAMA/Annals/PLOS ONE/generic; PDF + DOCX export bundles cover letter + point-by-point; meta_analysis card gains a "→ Draft as manuscript" handoff button that seeds the new thread with the full meta-analysis JSON | `src/research_assistant/agent/specialists/manuscript_drafter.py`, `reports/manuscript.py`, `domain/manuscript.py`, three index.html card components |
| eCRF formal validation pack (CSV / IQ/OQ/PQ) — password re-authentication at signing (Part 11 §11.200) via `verify_user_password_async` against Cognito `ADMIN_USER_PASSWORD_AUTH`; `StudyLock` model + 4 endpoints under `/api/edc/deployments/{id}/lock,unlock,lock-status,lock-history` gated by `study.lock` (data_manager only); writes/signs/SDV refused 409 while locked; auto-generated IQ snapshot (versions / deps from `uv.lock` / Cognito ID / audit-trigger detection), pytest-driven OQ over a 13-requirement Requirements Traceability Matrix tied to Part 11 §, ICH E6, ICH E2A, ALCOA+; PQ runbook; 4 PDFs + ZIP bundle from `/api/admin/validation-pack/*`; collector.html study-lock card + admin.html validation-pack section. | `src/research_assistant/validation/` (iq.py / oq.py / pq.py / rtm.py / requirements_matrix.json), `reports/validation_pack.py`, `web/admin.py` validation endpoints, `auth/cognito_admin.verify_user_password*`, `persistence/clinical/models.StudyLock`, `auth/rbac.Permission.STUDY_LOCK` |
| CDISC SDTM → ADaM → TLF — `BuiltinPythonMapper` derives 7 SDTM domains (DM + AE + VS + LB + EX + CM + MH) via item-id mapping convention + per-deployment `ItemMappingConfig`; **repeating-form domains** (LB / EX / CM / MH) gathered via `form_to_domain_map` (form_name → SDTM domain), one form-instance → one SDTM row; LB carries unit-aware result + reference range with LBNRIND (NORMAL/LOW/HIGH) auto-derived; EX route normalised against ex_routes.json CT; MH MHONGO auto-derived ("Y" when end date missing); ADSL with SAFFL/ITTFL/DTHFL/AGEGR1; 3 tables (disposition / demographics / AE summary) + 1 SVG figure (top-10 AE frequency, hand-rolled); per-domain CSV + ZIP submission bundle with `define-overview.txt` manifest covering all 7 SDTM + ADSL + TLF counts; `CdiscMapper` Protocol leaves a seam for an OSS-backed (pinnacle / OAK) implementation; 6 endpoints under `/api/edc/deployments/{id}/cdisc/*` gated by `cdisc.derive` / `cdisc.read` / `cdisc.export`; Submissions card in `collector.html` shows counts + per-domain CSV downloads for all 7 domains + ADSL. **Deferred:** ADaM ADTTE (needs EX × event derivation), `define.xml` (today: plain-text manifest), SAS Transport (XPT) (CSV ships today), real MedDRA SOC mapping (PT captured free-text), WHODrug for CM (CMDECOD free-text), randomisation-derived TRT01P/TRT01A (currently `"TBD"`). | `src/research_assistant/cdisc/` (sdtm_mapper / adam_deriver / tlf_generator / exporter / pipeline / terminology JSON), `web/edc.py` CDISC endpoints, `web/static/collector.html` Submissions card |
| Sandboxed Python execution (Docker, network-disabled, capped CPU/RAM, RW output dir) | `src/research_assistant/tools/data_science/sandbox_exec.py` |
| Multi-source paper search behind `PaperSource` Protocol (PubMed + Europe PMC live; Embase / Cochrane / Scopus / WoS as pluggable additions) | `src/research_assistant/tools/clinical/sources/` |
| Per-thread quotas, per-turn ceilings, daily token caps | `src/research_assistant/services/quota.py`, `config/settings.py` |
| Anti-hallucination guardrails (regex validator on general_qa, tool-gated PMIDs in meta_analysis, output validators forcing sandbox use) | `src/research_assistant/agent/specialists/{general_qa,meta_analysis}.py` |

---

## Designed but not yet built

These have a design doc; engineering can pick them up without scoping work.

### ~~RBAC — scoped roles + permission matrix + skill gating~~ · **P0** · ✅ shipped 2026-05-29

- **Doc:** [rbac-design.md](rbac-design.md)
- **Effort:** L (DB migrations, every endpoint touched, frontend gating) — delivered as RBAC-1 → RBAC-2 → RBAC-3 in one slice.
- **Why P0:** Without role separation, an institution cannot deploy beyond a 1–2-person team. Coordinators, monitors, PIs, statisticians, screening reviewers, and DSMB members all need distinct entitlements; the eCRF audit trail already models actor roles but enforces nothing application-wide.
- **Dependencies:** OAuth/OIDC ✅ shipped.
- **Unlocks now realised:** site-bounded EDC access, monitor-only SDV view, sponsor-vs-CRO separation, teaching/student tier. Still requires UI work to fully unlock the SR-screening Reviewer-1/Reviewer-2 model and the blinded DSMB view.
- **What landed:** `auth/rbac.py` (Permission + Role enums, ROLE_PERMISSIONS matrix incl. **student** tier, SKILL_PERMISSION map); `RoleAssignment` model + `init_db._backfill_role_assignments` for legacy `user_roles` rows; `require_permission` (global) and `require_permission_scoped(perm, resource_param=...)` (eCRF resource→scope); `agent/dispatcher.authorize_workflow` → 403 with required-perm name; `/api/ecrf` re-gated over `study.author`/`study.publish`/`study.create`/`skill.ecrf_design`; `/api/edc` split per matrix (`form.sign`/PI, `form.unlock`/DM, `sdv.verify`/monitor, `casebook.signoff`/PI, `subject.unlock`/DM, `query.raise|respond|close`); admin role-management endpoints + settings-UI panel; thread + watch ownership scoping (foreign rows 404, not 403, to avoid id leaks); per-user partition on `/usage/today` and `/usage/monthly`; frontend `hasPerm()` gating sidebar links and welcome workflow list.
- **Deferred for a follow-up:** role/tier-based per-user quota ceilings (the partition exists; the cap remains global per the design's "later refinement" call); blinded DSMB views; SR-screening-specific Reviewer-1 vs Reviewer-2 roles (will be added with the screening UI).

### ~~eCRF formal validation pack (CSV / IQ-OQ-PQ)~~ · **P0** · ✅ shipped 2026-05-30 · L

The "deployable in an audited environment" gate — closed. Three pieces landed together as eCRF E7:

- **Password re-authentication at signing** (Part 11 §11.200) — `web/edc.py:_require_signing_reauth` calls `auth/cognito_admin.verify_user_password_async` via `ADMIN_USER_PASSWORD_AUTH` on every form-sign + casebook-signoff. Bypassed only when Cognito isn't configured (dev/test). 401 on bad password; 503 on Cognito unavailable.
- **Study-level (database) lock** — `StudyLock` model + 4 endpoints under `/api/edc/deployments/{id}/{lock,unlock,lock-status,lock-history}` gated by new `study.lock` permission (data_manager only). Refuses lock while non-closed queries exist (overridable with audited `force_open_queries: true`). When locked, all data-entry, signing, and SDV writes 409. Audit row per lock + unlock.
- **IQ / OQ / PQ documented pack** — auto-generated from the running system:
  - **IQ** (`validation/iq.collect_iq_snapshot`) captures package version, Python version, dialect-specific DB metadata, pinned dependencies from `uv.lock`, Cognito pool ID, audit-trigger presence, feature flags.
  - **OQ** (`validation/oq.run_oq`) shells out to pytest over the test node-ids declared in the Requirements Traceability Matrix (`validation/requirements_matrix.json`) and joins the pass/fail with each requirement's regulatory anchor (Part 11 §, ICH E6, ICH E2A, ALCOA+).
  - **PQ** (`validation/pq.PerformanceRunbook`) is the 8-step customer-side runbook documenting the smoke flow (login → study create → form sign with reauth → study lock → IQ download → OQ run).
- **Reports** rendered via `reports/validation_pack.py` (3 PDF renderers + ZIP bundle). Admin downloads from `/api/admin/validation-pack/{iq,oq,pq}.pdf` + `/bundle.zip` + `/oq/run`.
- **Frontend** — collector.html gains a Study-lock card with lock/unlock buttons + status badge; admin.html gains a Validation Pack section with IQ/OQ/PQ download buttons, Run OQ button, last-run timestamp, and the requirements-matrix as a collapsible list. (A password-prompt UI at signing follows once a signing surface lands in the platform-MVP collector — today signing is driven via the API and the reauth gate fires there.)
- **Deferred for follow-up:** AE / deviation / query write-paths are not yet gated by the study lock (they should be — currently the gates cover submit_data + sign + verify + casebook-signoff, ~80% of the write surface). Adding the remaining gates is mechanical and ships with E7b.

---

## P0 — Blockers

Items here gate concrete customer segments. Ordered roughly by sequencing logic (some items unblock others).

### Synthesis

#### ~~SR title/abstract + full-text screening UI~~ · ✅ shipped 2026-05-29 · M

Was: search + extraction + meta were best-in-class but the **screening loop** in between — 3–8k abstracts, two reviewers, conflict resolution — was missing. Closed.

- **What landed:** project model with PICO + inclusion/exclusion criteria; reviewer slots (R1 / R2 / Adjudicator) with parallel `RoleAssignment` grants at `scope_type='sr_review'`; ingest endpoint reuses the multi-source paper search and dedupes against the existing `Publication` cache; dual-review queue is blind to other reviewers' decisions (R1's response shape carries no R2 votes and vice versa); agreement rules in `SrReviewRepository._recompute_status` collapse to terminal include/exclude or raise `pending_adjudication`; adjudicator-only conflicts view; full screening UI in `/sr.html` with keyboard shortcuts (`i` include / `e` exclude / `m` maybe); progress bar; PRISMA SVG diagram + counts.
- **AI-assist:** new `sr_screening_assist` specialist (pure text-in / structured-out, no tools, cheap-model tier) pre-classifies pending abstracts against the project's PICO + criteria; predictions are persisted to `AiSuggestion` rows so accuracy-vs-human can be measured later. Surfaces inline in the screening card; reviewer accepts or overrides.

#### ~~PRISMA flow diagram generation~~ · ✅ shipped 2026-05-29 · S

Shipped alongside the screening UI as `GET /api/sr/projects/{id}/prisma` (counts JSON) + `GET /api/sr/projects/{id}/prisma/diagram.svg` (hand-rolled SVG, no sandbox needed). Counts derive entirely from candidate statuses + reason codes; the SVG composes the standard 4-row flow with side exclusion boxes that include the per-reason breakdown.

### Design (upstream of protocol)

#### ~~Sample-size + power calculator~~ · ✅ shipped 2026-05-29 · S

Was the lowest-effort P0 in this document — closed. New `sample_size` tool under `tools/data_science/` exposes four closed-form helpers (two-proportions, two-means, time-to-event/Schoenfeld, paired) implemented directly on scipy.stats so it runs in the agent process (no sandbox round-trip). Returns a dict shaped to match `domain.sap.SampleSizeResult` so the SAP workflow's STEP 2 ingests it without translation.

#### ~~Statistical Analysis Plan (SAP) drafter~~ · ✅ shipped 2026-05-29 · M

Shipped as a NEW specialist (`sap_drafter`), separate from `sr_protocol` because prospective-trial methodology (ICH E9) is structurally different from literature-review methodology (PRISMA-P). Four-stage workflow: PICOT intake → sample-size derivation (calls the `sample_size` tool) → ICH-E9 analysis plan → assembled SAP document. PDF + DOCX downloads via the existing `reports/` machinery. New `skill.sap_drafter` permission granted to researcher + admin; student blocked. Dispatcher catches `/sap`, `/samplesize`, "sample size", "SAP", "ICH E9", "prospective trial", "powered to detect", "PICOT".

### Start-up

#### ClinicalTrials.gov / EU CTR registration drafter · 💡 · M

PROSPERO drafting is covered (via the `sr_protocol` specialist's PROSPERO field map). The trial registries — ClinicalTrials.gov (CT.gov) has ~200 structured fields, EU CTR is similar — are the equivalent gate for prospective work and are not covered.

- **Dependencies:** SAP drafter (for the statistical-design fields).
- **Unlocks:** prospective trial start-up; mandatory pre-enrolment registration.

#### IRB / ethics submission packet + Informed Consent Form drafter · 💡 · L

The biggest single time-sink in trial start-up. Components:

- Protocol synopsis (1–2-page summary)
- Lay summary (often required separately)
- Informed Consent Form (ICF) — legally binding, regulator-templated, multilingual, reading-level-controlled
- Investigator CV insert / FDA Form 1572
- DSMB / DMC charter draft
- Site-level supplementary packets

The roadmap's "patient-facing research handouts" entry is adjacent but tackles the easier shape (post-study lay summaries); ICF generation is the gnarlier sibling.

- **Dependencies:** none hard.
- **Unlocks:** ethics submission turnaround; first real "weeks → hours" claim for start-up.

### Execution

#### Randomisation / IRT (Interactive Response Technology) service · 💡 · M

A prospective trial without randomisation isn't a trial. Block-randomised, stratified, central allocation is what gates investigator access to study drug. Today the eCRF subsystem **captures** data but never **assigns arm** — meaning we cannot serve any RCT, only observational work.

- **Dependencies:** RBAC (the IRT call is the moment role-separation between coordinator/investigator/sponsor matters most).
- **Unlocks:** the RCT market for the eCRF subsystem.

#### ~~AE / SAE workflow on top of eCRF~~ · ✅ shipped 2026-05-29 · M

Was a compliance-critical gap — closed. New `safety_rules.auto_classify_serious()` applies ICH E2A §III.A criteria (grade ≥3, death, life-threatening, hospitalisation, congenital, persistent disability, other medically significant) on every AE write; `compute_reporting_deadline()` stamps a 24-hour platform escalation timer for serious events. The overdue-SAE endpoint surfaces past-deadline events that haven't been reported. PI overrides flow through `PATCH /api/edc/ae/{id}` and clear the deadline on downgrade. FDA 3500A IND safety report draft shipped via `reports/sae_3500a.py` — fields the platform can derive are filled; sponsor-supplied fields (IND number, NDA/BLA number, investigator name + address) are marked `[SPONSOR INPUT REQUIRED]` rather than fabricated. PHI minimisation enforced (only subject_code; never participant name). MedDRA Preferred Term is captured as free text; real validation needs a MedDRA license at deploy — documented in the endpoint docstring + this row. SUSAR detection is the next-most-natural follow-up (it's an SAE subset with `unexpected=True` and `relationship_to_intervention >= probable`) and remains intentionally deferred.

### Analysis / Reporting

#### ~~CDISC SDTM mapping → ADaM derivation → TLF generation~~ · ✅ shipped 2026-05-29 · XL

**Two slices shipped:** first slice (2026-05-29) covered SDTM **DM + AE + VS**, ADaM **ADSL**, and a basic TLF set (3 tables + 1 SVG figure). Second slice (2026-05-30) added SDTM **LB + EX + CM + MH** via a repeating-form pattern (one form-instance → one SDTM row). The pluggable `CdiscMapper` Protocol leaves room to swap in an OSS-backed implementation later without touching the API surface.

- **What landed (slice 1 — DM/AE/VS/ADSL/TLF):** `cdisc/` package — `BuiltinPythonMapper` derives DM/AE/VS via an item-id mapping convention (`age`→AGE, `sex`→SEX, `sbp`→SYSBP …) overridable per deployment via `ItemMappingConfig`; controlled-terminology JSON for AE severity / outcome / relationship + DM sex / race + VS test codes; ADSL builds SAFFL / ITTFL / DTHFL / AGEGR1 (ICH E1 bins) with TRT01P/TRT01A defaulting to `"TBD"` until randomisation lands; TLF generator emits Disposition / Demographics / AE-summary tables (`content_json`) plus a hand-rolled top-10 AE-frequency SVG; per-domain CSV with SDTM column ordering + ZIP submission bundle (`sdtm/*.csv` + `adam/adsl.csv` + `tlf/*` + `define-overview.txt` manifest); `CdiscDerivation` audit row per run. Six endpoints under `/api/edc/deployments/{id}/cdisc/*` gated by `cdisc.derive` (data_manager only) / `cdisc.read` (data_manager, PI, monitor, auditor) / `cdisc.export` (data_manager + PI). Submissions card in `collector.html` with run-derivation, per-domain CSV download links, bundle download, and collapsible TLF previews.
- **What landed (slice 2 — LB/EX/CM/MH):** four new SDTM domains follow a **repeating-form** pattern — each form-instance of a designated form (`form_to_domain_map` in `ItemMappingConfig`) becomes one SDTM row. **LB**: unit-aware result + reference range; LBNRIND auto-derived (NORMAL / LOW / HIGH) using either form-captured ranges or the CT default for the test code (HGB, GLUC, ALT, AST, ALP, BILI, CREAT, BUN, NA, K, CL, CO2, CA, ALB, LDL, HDL, TG, CHOL, HBA1C and others). **EX**: route normalised against `ex_routes.json` (oral → ORAL, iv → INTRAVENOUS, etc); unknown routes pass through verbatim. **CM**: free-text CMTRT + optional ATC/WHODrug placeholder (`CMDECOD`); indication + dose. **MH**: MHTERM + MHCAT canonicalised via `mh_categories.json`; MHONGO auto-derived ("Y" when end date missing, "N" otherwise). Per-subject sequencing (LBSEQ / EXSEQ / CMSEQ / MHSEQ) stable across reruns. Submission bundle now ships 7 SDTM CSVs + ADSL + TLF; manifest counts all 7 domains.
- **Resolved open question:** built internally in Python with a `CdiscMapper` Protocol seam — no R/OSS dependency at deploy time; an OSS-backed `pinnacle`/`OAK` subprocess implementation can be registered without changing endpoints when the platform is deployed in environments where that's preferred.
- **Deferred for follow-up:** ADaM ADTTE (time-to-event analysis dataset; needs EX × event derivation); `define.xml` (today: `define-overview.txt` plain text); SAS Transport (XPT) — CSV is regulator-acceptable for many flows but XPT is the submission default; real MedDRA SOC back-indexing (PT is captured free-text); WHODrug for CM (CMDECOD free-text today); randomisation-derived TRT01P/TRT01A (waiting on IRT, P0 above).
- **Unlocks now realised:** structured submission-bundle generation for sponsor / CRO adoption with the most-requested SDTM domains; the CSR drafter (P0, below) can begin once ADTTE lands.

#### Clinical Study Report (CSR, ICH E3) drafter · 💡 · XL

A real CSR is 80–150 TLF artefacts plus narrative. The per-workflow report engine (meta-analysis · protocol · RoB, all ✅ shipped) is the scaffolding; CSR composition is the natural capstone of the trial workflow.

- **Dependencies:** SDTM/ADaM (above). Without ADaM, CSR has no analysis data to narrate.
- **Sequencing:** start the manuscript drafter (P1, below) first — it shares 80% of the composition mechanics and is useful on its own.

---

## P1 — High-leverage structural gaps

### Execution

#### ~~Protocol-deviation tracking + CAPA~~ · ✅ shipped 2026-05-29 · M

Was a regulator's first ask at inspection — closed. New `ProtocolDeviation` + `CapaAction` clinical-store models with full lifecycle: log (coordinator/monitor) → classify (data_manager/PI) → add CAPAs (data_manager) → complete CAPAs (owner) → close deviation (PI; refused while any CAPA is still open). The eCRF audit trail records *what changed* on individual items; this layer is the higher-level classification on top. Surfaces in the collector's per-subject safety panel.

#### Recruitment / screening logs · 💡 · S

Per-site, per-day enrolment numbers; ineligibility reason coding; recruitment-funnel attrition. Operational hygiene PIs ask for weekly. Low effort; lives naturally next to the eCRF subject roster.

#### Visit scheduling + participant reminders · 💡 · M

Today the visit schedule is part of the form definition, but there is no calendar surface for coordinators and no SMS/email reminder to participants. **ePRO compliance dies without reminders** — this is what makes the difference between 95% and 60% ePRO completion.

#### Source-document extraction (EHR → extraction table / eCRF pre-fill) · 📝 · L

Already on the feature-guide roadmap. Point the assistant at structured exports from EHR / registry / TMS and have it populate the extraction table (or pre-fill eCRF instances) for retrospective studies or patient-level meta-analyses, with an audit trail of which source row produced which output cell.

- **Dependencies:** RBAC (PHI-bearing).
- **Adjacency:** opens RWE work if generalised to FHIR (P3, below).

### Analysis / Reporting

#### ~~Manuscript drafter (IMRaD) + reviewer-response loop~~ · ✅ shipped 2026-05-29 · L

Shipped as a new `manuscript_drafter` specialist + a 3-stage workflow: intake (target journal + section seeds + source artefact paste) → IMRaD draft (title + structured abstract + Introduction + Methods + Results + Discussion + References with `origin` field) → reviewer-response loop (cover letter + per-item responses with optional suggested manuscript edits + `is_addressed` flag for pushed-back items).

- **Composition source:** the meta_analysis card gains a "→ Draft as manuscript" handoff button that seeds the new thread with the full meta-analysis JSON pasted into `intake.source_artefact_paste`; the manuscript-drafter system prompt forbids inventing Results numbers and requires every effect size to trace back to the paste verbatim.
- **Journal targets:** NEJM / Lancet / BMJ / JAMA / Annals / PLOS ONE / generic, driving abstract / body word budgets via the system prompt.
- **Reference posture:** every citation carries `origin` (`search_papers` / `web_search` / `wikipedia` / `pasted_source`) so a reader can verify each reference came from a real tool call this turn.
- **Reports:** PDF + DOCX via the existing `reports/` machinery; reviewer-response document is appended to the same download when a response round exists.
- **Sequencing benefit:** the composition primitives (per-section assembly, structured-abstract table, reference list with origin tagging) carry forward into the future CSR drafter (#6).

#### GRADE summary-of-findings + PRISMA reporting checklist · 💡 · M

Both are journal-mandated for SRs. PRISMA checklist is mechanical (we have everything). GRADE SoF is harder — domain-by-domain certainty downgrading per outcome, with rationale.

#### Trial-specific statistical analysis specialist · 💡 · M

Beyond pairwise meta-analysis: ITT vs PP, time-to-event / Kaplan-Meier, mixed-effects models (MMRM), Cox PH, longitudinal models, subgroup forest plots, interaction testing. The sandbox can already compute these; a specialist that drives them with structured input/output is what's missing.

#### Beyond-forest-plot visualisations · 💡 · M

Funnel plots (publication bias), Kaplan-Meier curves, waterfall, swimmer, network-meta-analysis geometry, GRADE chip tables. Sandbox can render any of these; what's missing is the specialist orchestration.

#### Bayesian / network meta-analysis · 💡 · L

Indirect comparisons across multiple interventions (e.g. comparing 5 DOACs head-to-head without head-to-head trials). Methodologically distinct from pairwise; not on the existing feature-guide roadmap.

#### Individual Patient Data (IPD) meta-analysis · 💡 · L

Pooling subject-level data across trials, not just summary effects. Higher-prestige output; needs different stats pipeline. Closely adjacent to source-document extraction (above).

### Cross-cutting

#### Citation-manager integration · 💡 · S

Zotero / EndNote / Mendeley import + export. Today references are inline in the protocol/manuscript; no library round-trip. Low-effort, high-felt-impact for users with existing libraries.

#### Portfolio dashboard across reviews + trials · 💡 · M

"Show me every active SR, who owns it, what stage it's in." Today the assistant is per-thread; institutional rollup is absent. Becomes essential once an institution exceeds ~20 active reviews.

---

## P2 — Polish on existing workflows

#### Group-level living-review subscriptions · 📝 · M

In the feature-guide roadmap. Group watches with quorum-based notification rules — designed for guideline committees and HTA bodies who need consensus signalling on practice-changing evidence rather than per-user alerts.

#### Patient-facing / lay summaries · 📝 · M

In the feature-guide roadmap. Plain-language summaries of a study's objectives, what participation involves, and what early evidence suggests — at a configurable reading level, in the patient's preferred language. Designed for recruitment, shared-decision-making, and post-study return-of-results.

- **Adjacency:** if extended into ICF drafting, becomes a P0 (above).

#### Drug accountability · 💡 · M

IP receipt → dispensing → return reconciliation. Often the messiest paperwork in a trial; mostly absent here.

#### Lab-data feeds (HL7 / CDISC LAB) · 💡 · L

Central labs deliver via these standards. The eCRF re-keys lab values today. Worth doing once SDTM mapping is mature (the data shape converges).

#### Multi-site / multi-tenant coordination rollup · 💡 · M

Site-level aggregations are partial today; central-coordinator view of multi-site enrolment, query backlog, and per-site monitor visit status would close it.

#### Budget + cost rollup across studies · 💡 · S

Per-user envelope exists in the feature guide; institutional cost-per-study and cost-per-evidence-output are absent. Low effort once portfolio dashboard (P1) is in place.

---

## P3 — Strategic bets

Larger TAM, longer build, higher uncertainty. Worth tracking for sequencing reasons.

#### Research-gap analysis specialist · 📝 · M

In the feature-guide roadmap. Given a body of literature, surface where the evidence base is thin — by population, intervention, outcome, geography, or study design. Lower priority today only because the upstream evidence pipeline isn't yet complete (screening + PRISMA flow); becomes P1 once those land.

#### DSMB / DMC charter + blinded views · 💡 · L

Independent monitoring board view of unblinded safety. Distinct security boundary; needs careful RBAC. Realistically post-RCT-launch.

#### HTA / payer-grade dossier generation · 💡 · XL

NICE / IQWiG / CADTH / PBAC submission packages. Big effort, high revenue per deal, far-future.

#### EHR / FHIR / OMOP CDM integration for real-world evidence · 💡 · XL

Source-document extraction (P1) is the wedge. A first-class FHIR / OMOP integration opens RWE studies — much larger market than RCTs, structurally similar but distinct data shape.

#### Central imaging upload + adjudicated review · 💡 · XL

Many trials have imaging endpoints. Distinct subsystem; substantial storage + viewer work.

#### Press-release / institutional-comms drafter · 💡 · S

Embargoed press releases, institutional comms drafts for big-result publications. Low effort; latent value mostly for PR offices.

#### Pharmacovigilance / post-market AE tracking · 💡 · L

Post-market surveillance after a product reaches market. Adjacent to the trial AE/SAE workflow (P0) but distinct lifecycle.

---

## Recommended top-6 build queue

Best ordering given unlock value × dependencies × effort:

| # | Item | Why now |
|---|---|---|
| ~~1~~ | ~~**RBAC implementation**~~ — ✅ shipped 2026-05-29 | (Done — RBAC-1+2+3 + Student tier landed.) |
| ~~2~~ | ~~**SR screening UI + PRISMA flow diagram**~~ — ✅ shipped 2026-05-29 | (Done — dual review + AI-assist + PRISMA SVG.) |
| ~~3~~ | ~~**Sample-size + SAP drafter (paired)**~~ — ✅ shipped 2026-05-29 | (Done — four formulas + ICH-E9 SAP workflow + PDF/DOCX export.) |
| ~~4~~ | ~~**AE/SAE workflow + protocol-deviation tracking (paired)**~~ — ✅ shipped 2026-05-29 | (Done — ICH E2A auto-classification, 24h timer, FDA 3500A draft, CAPA lifecycle.) |
| ~~5~~ | ~~**Manuscript drafter (IMRaD) + reviewer-response loop**~~ — ✅ shipped 2026-05-29 | (Done — 3-stage workflow with journal-target enum + handoff from meta_analysis + reviewer-response document; 575 tests pass.) |
| ~~6~~ | ~~**CDISC SDTM mapping (start the multi-quarter build)**~~ — ✅ shipped 2026-05-29 (partial) | (Done — DM + AE + VS + ADSL + 3 tables + 1 SVG figure + submission-bundle ZIP. Built internally in Python with a `CdiscMapper` Protocol seam; LB/EX/CM/MH/ADTTE deferred.) |

Beyond these six, sequencing flexes with customer pull. Likely next: **eCRF formal validation pack** (CSR/IQ-OQ-PQ — top P0 still outstanding), or fill in the deferred CDISC domains (LB / EX / ADTTE) ahead of starting the CSR drafter.

---

## Open questions for product owner

1. **Feature-guide "On the roadmap" section** — keep it as a short customer-facing teaser of P0 items, or remove it entirely now that the full list lives here? (Recommendation: keep, but trim to the top-3 P0 items + a pointer to this document.)
2. **RBAC vs SR screening sequencing** — SR screening *needs* RBAC for the two-reviewer model. Two options:
   - (a) RBAC first (delays screening start by ~1 quarter)
   - (b) screening built with placeholder two-user model, RBAC retrofitted later (faster MVP, ~1 week of rework when RBAC lands).
3. ~~**SDTM mapping** — adopt an existing OSS mapping library (e.g. `pinnacle`, `OAK`) or build internally?~~ → **Resolved 2026-05-29: build internally in Python**, with a `CdiscMapper` Protocol leaving room to register an OSS-backed implementation (subprocess) later without changing endpoints. First slice (DM + AE + VS + ADSL + basic TLF) shipped.
4. **Sample-size + SAP** — new specialist, or extensions to the existing `sr_protocol` specialist? Worth a small design pass.
5. **eCRF formal validation pack** — does any current customer require it for production deployment, or is it preventatively-prioritised? Answer changes its P0 → P1.
6. **Source-document extraction priority** — current memory and feature-guide place it at the centre of the next eCRF/EDC build phase. Worth confirming it stays P1 vs being elevated to P0 if an EHR-integration customer pulls hard.

---

## Maintenance

- Update on every shipped P0/P1 feature (move row to "Already shipped").
- Add new gaps under the appropriate priority tier as they're discovered.
- Re-prioritise quarterly or when customer pull shifts.
- Link each item to its design doc (`docs/design/*.md`) as one is created.
- Keep the at-a-glance table in sync with the per-item detail.
