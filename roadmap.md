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
| Start-up | ClinicalTrials.gov / EU CTR registration drafter | **P0** | ✅ shipped 2026-05-30 | M |
| Start-up | IRB / ethics submission packet + ICF drafter | **P0** | ✅ shipped 2026-05-30 (lite — synopsis + ICF; investigator CV + DSMB charter deferred) | L |
| Execution | Randomisation / IRT service | **P0** | ✅ shipped 2026-05-30 | M |
| Execution | AE / SAE workflow (detect, code, escalate) | **P0** | ✅ shipped 2026-05-29 | M |
| Execution | eCRF formal validation pack (CSV / IQ-OQ-PQ) | **P0** | ✅ shipped 2026-05-30 | L |
| Analysis | CDISC SDTM mapping → ADaM → TLF | **P0** | ✅ shipped 2026-05-30 (7 SDTM domains + ADSL + ADTTE + K-M / Cox PH + Define-XML v2.1 + SAS Transport v5; MedDRA/WHODrug/IRT-driven TRT remain deploy-time gates) | XL |
| Analysis | CSR (ICH E3) drafter | **P0** | ✅ shipped 2026-05-30 (synopsis + 4 data sections; narrative deferred) | XL |
| Execution | Protocol-deviation tracking + CAPA | **P1** | ✅ shipped 2026-05-29 | M |
| Execution | Recruitment / screening logs | **P1** | ✅ shipped 2026-05-30 | S |
| Execution | Visit scheduling + participant reminders | **P1** | ✅ shipped 2026-05-30 | M |
| Execution | Source-document extraction (EHR → eCRF / extraction table) | **P1** | ✅ shipped 2026-05-30 | L |
| Analysis | Manuscript drafter (IMRaD) + reviewer-response loop | **P1** | ✅ shipped 2026-05-29 | L |
| Analysis | GRADE summary-of-findings + PRISMA reporting checklist | **P1** | ✅ shipped 2026-05-30 | M |
| Analysis | Trial-specific statistical analysis specialist (KM, MMRM, Cox) | **P1** | ✅ shipped 2026-05-30 | M |
| Analysis | Beyond-forest-plot visualisations (funnel, KM, waterfall, swimmer) | **P1** | ✅ shipped 2026-05-30 | M |
| Analysis | Bayesian / network meta-analysis | **P1** | ✅ shipped 2026-05-30 | L |
| Analysis | Individual Patient Data (IPD) meta-analysis | **P1** | ✅ shipped 2026-05-31 | L |
| Cross-cutting | Citation-manager integration (Zotero / EndNote / Mendeley) | **P1** | ✅ shipped 2026-05-31 | S |
| Cross-cutting | Portfolio dashboard across reviews + trials | **P1** | ✅ shipped 2026-05-31 | M |
| Synthesis | Group-level living-review subscriptions (quorum notifications) | **P2** | 📝 planned (feature-guide) | M |
| Dissemination | Patient-facing / lay summaries | **P2** | ✅ shipped 2026-05-31 | M |
| Execution | Drug accountability (IP receipt → dispense → return) | **P2** | ✅ shipped 2026-05-31 | M |
| Execution | Lab-data feeds (HL7 / CDISC LAB) | **P2** | 💡 proposed | L |
| Cross-cutting | Multi-site / multi-tenant coordination roll-up | **P2** | 💡 proposed | M |
| Cross-cutting | Budget + cost rollup across studies | **P2** | ✅ shipped 2026-05-31 | S |
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
| ADaM ADTTE + sandbox K-M / Cox PH survival analyses — `AdamAdtte` model with PARAMCD/PARAM/AVAL/AVALU/CNSR/STARTDT/ADT/EVNTDESC/SRCDOM/SRCVAR regulator-required audit anchors; `derive_adtte` produces 3 parameters per subject (TTAE, TTSAE, DEATH) with proper censoring at last follow-up; t-tte-summary TLF table uses a pure-Python K-M estimator for the median; first CDISC output to round-trip the sandbox via `cdisc/sandbox_scripts/survival_analysis.py` (statsmodels.duration.PHReg + matplotlib K-M plots stratified by TRT01A); POST `/api/edc/deployments/{id}/cdisc/survival/render` writes K-M PNGs back as data-URI `TlfArtefact.svg_content` + a Cox PH summary table; idempotent re-run wipes prior K-M + Cox rows. Submissions card gains ADTTE count + adtte.csv download + "📈 Render survival" button. | `src/research_assistant/cdisc/adtte_deriver.py` + `sandbox_scripts/survival_analysis.py`, ADTTE columns in `exporter.py`, `tlf_generator._tte_summary_table` + `_km_median`, survival render endpoint in `web/edc.py` |
| CDISC submission-format slice — **Define-XML v2.1** generator (`cdisc/define_xml.py`) emits a valid CDISC ODM/Define-XML 2.1 document covering all 9 datasets (DM/AE/VS/LB/EX/CM/MH/ADSL/ADTTE) with ItemGroupDefs (key variables + class), ItemDefs (type/length/label), CodeLists (sex/race/AESEV/AEOUT/AEREL/VSTESTCD/LBTESTCD/EXROUTE/MHCAT/NY/PARAMCD.ADTTE/CNSR/AVALU built from the existing terminology JSON), and MethodDefs documenting every derived column (AGEGR1/LBNRIND/MHONGO/SAFFL/DTHFL/ADTTE AVAL+CNSR). **Hand-rolled SAS Transport v5 writer** (`cdisc/xpt_writer.py`) — IBM-360 8-byte float conversion + 80-byte record alignment + 140-byte v5 namestr records; ASCII-only CHAR + NUM; explicit validation of the 8-char column-name and 200-char string-length v5 limits. Shared `cdisc/_metadata.py` registry is the single source of truth for column types/lengths/labels (both XPT writer and Define-XML consume it). Submission bundle now ships side-by-side: `define.xml` at root, `define-overview.txt` (human summary), `sdtm/*.{csv,xpt}` × 7, `adam/{adsl,adtte}.{csv,xpt}`, plus existing TLF. No new dependency — `xport`/`pyreadstat` deliberately avoided. | `src/research_assistant/cdisc/_metadata.py`, `cdisc/define_xml.py`, `cdisc/xpt_writer.py`, bundle wiring in `cdisc/exporter.py` |
| Trial-registration drafter — new `registration_drafter` specialist + 4-stage workflow (intake → core fields → CT.gov + EU CTR drafts → assembled document with background paragraph). Output is paste-able into the CT.gov PRS form and the EU CTIS portal. NCT IDs / CTIS trial IDs NEVER fabricated — assigned by the registries on submission; sponsor PHI marked `[SPONSOR INPUT]`. PDF + DOCX export. New `skill.registration_drafter` permission (researcher + admin). Dispatcher routes `/register` / `/ctgov` / `/ctis` + CT.gov / EU CTR / EudraCT / "trial registration" / "register a trial" keywords. | `src/research_assistant/agent/specialists/registration_drafter.py`, `reports/registration.py`, `domain/registration.py` |
| IRB-packet drafter (lite — synopsis + ICF) — new `irb_drafter` specialist + 3-stage workflow (intake → protocol synopsis → ICF). **Protocol synopsis** has 8 ICH E6(R2)-aligned sections; **ICF** is schema-enforced against the 21 CFR §50.25(a) required-element set (≥9 sections covering purpose / procedures / risks / benefits / alternatives / confidentiality / injury / contacts / voluntariness). Configurable reading-level target (Flesch-Kincaid grade 4-12) + computed actual grade; PDF flags above-target overshoots. Multilingual en/es/fr/de only (other languages → clarification asking for a human translator). PDF + DOCX export with signature block. New `skill.irb_drafter` permission (researcher + admin). Dispatcher routes `/irb` / `/icf` / `/consent` + IRB / ICF / "informed consent" / "21 CFR 50.25" / "ICH E6" / "ethics committee" / "protocol synopsis" keywords. Cross-handoff seed "Draft IRB packet from registration intake" carries registration_drafter's intake into the IRB workflow. *Deferred:* investigator CV / FDA Form 1572, DSMB charter, site supplementary packets. | `src/research_assistant/agent/specialists/irb_drafter.py`, `reports/irb.py`, `domain/irb.py` |
| Randomisation / IRT (eCRF E8 — RCT enabler) — new `RandomizationSchedule` + `Allocation` + `CodeBreakEvent` models; pure-Python `randomization/` package with 4 deterministic algorithms (simple / permuted_block / stratified_permuted_block / Pocock-Simon minimisation); schedule generation produces a pre-randomised sequence (or, for minimisation, initialises the running per-arm-per-factor counts state). Endpoints: POST `/api/edc/deployments/{id}/randomization/schedule` (DM only — generate), GET (read), POST `/schedule/close`; POST `/api/edc/subjects/{id}/randomize` (coordinator + PI — consume next entry / run minimisation), GET `/allocation`, POST `/code-break` (PI only — emergency unblinding, audited reason ≥8 chars), GET `/deployments/{id}/code-break-events` (audit-trail). Blinding modes: `open_label` reveals arm at allocation; `single_blind`/`double_blind`/`triple_blind` mask the arm in API responses until the code-break flips `Allocation.unblinded=True`. CDISC integration: `derive_adsl` pulls `TRT01P` / `TRT01A` from `Allocation.arm` when present (no more `"TBD"` placeholder); ADTTE inherits via ADSL. 4 new permissions (`randomization.generate` / `.allocate` / `.codebreak` / `.read`). Minimal collector.html panel showing schedule + per-arm allocation counts. | `src/research_assistant/randomization/` (algorithms + schedule), `persistence/clinical/models.{RandomizationSchedule,Allocation,CodeBreakEvent}`, `persistence/clinical/repository` IRT methods, IRT endpoints in `web/edc.py`, ADSL integration in `cdisc/adam_deriver.py`, Randomisation panel in `collector.html` |
| CSR (ICH E3) drafter — synopsis + 4 data-driven sections — new `csr_drafter` specialist + 4-stage workflow (intake → synopsis → data sections → assembled document). Skeleton CSR carries all 16 ICH E3 section headers; data sections (Disposition / Demographics / Efficacy / Safety) populate from operator pastes of ADSL + ADTTE + TLF; narrative sections (Introduction / Discussion / Overall Conclusions) ship as `[Operator to complete]` placeholders this slice. Strictest anti-hallucination posture in the codebase: every count carries a required `derived_from` source-artefact id (e.g. `TLF t-disposition`, `ADTTE PARAMCD=TTAE`); system prompt forbids inline effect-size / CI / p-value writes without a TLF/ADTTE source. PDF + DOCX bundling via `reports/csr.py`. New `skill.csr_drafter` permission (researcher + admin; student blocked). Dispatcher: `/csr` / `/e3` / `/study-report` slash commands + "Clinical Study Report", "ICH E3", "CSR drafter" keywords. Disambiguated continuation prefixes (`CSR intake confirmed` etc) avoid collision with the generic registration prefix. Cross-handoff seeds from meta-analysis, ADTTE, SAP. | `src/research_assistant/agent/specialists/csr_drafter.py`, `reports/csr.py`, `domain/csr.py` |
| GRADE Summary of Findings + PRISMA 2020 reporting checklist — new `grade_drafter` specialist + 5-stage workflow (intake → per-outcome assessment → SoF assembly → PRISMA 2020 checklist → assembled document). Certainty COMPUTED via Pydantic `computed_field` from per-domain ratings (RCT start=high / observational=low; serious=−1, very_serious=−2; observational upgrades for large effect / dose-response / residual confounding); agent cannot inline-assert it. Every downgrade rating has a required `rationale` field; system prompt mandates citing a source number (I² value, CI bounds, n_studies, Egger's p) rather than handwaving. PRISMA 2020 item registry carries the 42 canonical sub-items from Page et al. (BMJ 2021); operator records reported/location/notes per item; report renders missing as "Not reported". PDF (landscape) + DOCX with colour-coded SoF (green/yellow/orange/red), per-outcome detail spelling out each downgrade rationale, and the PRISMA checklist (5-column table). New `skill.grade_drafter` permission (researcher + admin; student blocked). Dispatcher: `/grade` / `/sof` / `/prisma-checklist` slash + "Summary of Findings" / "certainty of evidence" / "PRISMA checklist" keywords. Cross-handoff seed `Draft GRADE from meta-analysis`. | `src/research_assistant/agent/specialists/grade_drafter.py`, `reports/grade.py`, `domain/grade.py` |
| Portfolio dashboard across reviews + trials — 5 endpoints under `/api/portfolio` (threads, sr-projects, deployments, summary, org). Per-user routes inherit the top-level auth dependency — researcher sees their own data, no new permission needed. **`/org` endpoint admin-only via new `portfolio.read_org` permission** — returns per-user totals + org-wide aggregates, no row-level data leaked. SR projects rollup includes both owned and `sr_review`-scoped membership (reviewer_1/2/adjudicator). eCRF deployment visibility resolves through `RoleAssignment` set (global-admin, study-scoped, site-scoped via `Site → Deployment`). Researcher-only callers see empty deployment list (eCRF is clinical-operations, not researcher artefact). No new persistence — aggregations over existing `ThreadRepository`, `SrReview`, `StudyDeployment`, `RoleAssignment` rows. New `/portfolio.html` page (vanilla JS + fetch) with hero cards + per-section tables; org rollup section auto-hides when the endpoint returns 403. Sidebar link added to `index.html`. `last_turn_kind` peeks at the most recent assistant `Message.final_answer` JSON for the `kind` discriminator so the dashboard can show workflow progress without re-rendering turns. | `src/research_assistant/web/portfolio.py`, `web/app.py`, `web/static/portfolio.html`, `web/static/index.html` (sidebar link), `auth/rbac.py` (`PORTFOLIO_READ_ORG`) |
| Citation-manager integration — pure file-format round-trip covering Zotero / EndNote / Mendeley via the universal `.bib` (BibTeX) and `.ris` formats every manager imports/exports. New `services/citations.py` with stdlib-only parsers + exporters (no `bibtexparser` / `pyzotero` / `rispy` dep). 2 endpoints under `/api/citations` (parse multipart upload with auto-detect; export JSON → downloadable file). `import_citations(format, content)` tool registered on `manuscript_drafter` and `sr_protocol` specialists. Frontend `<CitationImporter>` drop-zone component embedded in the `ManuscriptIntakeCard` and `ProtocolMethodsCard` — on drop, POSTs to `/api/citations/parse` and inlines the parsed list into the next chat message. Canonical `Citation` dataclass round-trips both formats; entry types article/book/incollection/inproceedings/techreport/phdthesis/misc map across via `_BIBTEX_TYPE_BY_RIS_TY` / `_RIS_TY_BY_BIBTEX_TYPE`. Unknown BibTeX fields preserved through export via `raw_fields`. RIS `SP`+`EP` collapse to BibTeX `pages`. PMID detection from RIS `AN`/`ID`/`PM` tags. No new RBAC permission (specialists already researcher-tier). OAuth Zotero web-API integration deferred. | `src/research_assistant/services/citations.py`, `web/citations.py`, `web/app.py`, `tools/general/import_citations.py`, manuscript_drafter + sr_protocol specialist tool registration, `web/static/index.html` `<CitationImporter>` |
| Individual patient data (IPD) meta-analysis — new `ipd` specialist + 5-stage workflow (intake → bundle with per-trial CSV pastes + column mapping → main results = one-stage + two-stage side-by-side → subgroup × treatment interaction → assembled document). Three sandbox scripts in `ipd/sandbox_scripts/`: `one_stage.py` runs statsmodels MixedLM (continuous) / GLM Binomial logit with trial dummies (binary fixed-effects approximation) / stratified PHReg (TTE); `two_stage.py` does per-trial estimate + DerSimonian-Laird random-effects pool with I² + τ²; `subgroup.py` adds treatment × subgroup interaction + joint Wald p (continuous only this slice; binary/TTE interaction p deferred). **One-stage vs two-stage as the diagnostic** — both stages run; discrepancy >0.2 in log-scale signals model misspecification. Anti-hallucination: per-trial estimates + I² + τ² + interaction p come from sandbox runs only; trial ids in `studies_included` must appear in the bundle; system prompt mandates dropping (not fabricating) trials the sandbox skips. New `run_ipd_analysis(stage, payload)` tool. Domain enforces ≥2 trials in bundle, ≥2 per_trial rows in results, I² clamped to [0,100], τ² >= 0. Landscape PDF + DOCX with side-by-side one-stage/two-stage table + per-trial estimates + subgroup interactions + interpretation + caveats. `skill.ipd` permission (researcher + admin; student blocked). Dispatcher routes IPD before meta_analysis ("IPD MA on statins" → IPD, not aggregate); NMA's multi-arm `compare N` regex still catches 3+ arm comparisons first. Inline CSV ingest (not SourceDocument linkage this slice). | `src/research_assistant/agent/specialists/ipd.py`, `tools/data_science/ipd_analysis.py`, `ipd/sandbox_scripts/{one_stage,two_stage,subgroup}.py`, `reports/ipd.py`, `domain/ipd.py` |
| Network meta-analysis (Bayesian + frequentist) — new `nma` specialist + 5-stage workflow (intake → PicoNetwork with ≥3 interventions + transitivity rationale → search → per-arm extraction → NMA results). Sandbox-backed via the new `run_nma_analysis(backend, payload)` tool wrapping three scripts in `nma/sandbox_scripts/`. **Frequentist backend (default)** implements mvmeta-style + electrical-network analogy on numpy.linalg + scipy.stats.multivariate_normal (1000 MVN posterior draws for SUCRA); no netmeta R dependency. **Bayesian backend (opt-in)** uses PyMC NUTS (random-effects τ, 2 chains × 500 tune + 500 draws); the script gracefully returns a structured `skip_reason` when PyMC is missing from the sandbox image (currently the case — documented as a deploy-time gate; rebuild with `pymc>=5` to activate). **Network-geometry PNG** via matplotlib hand-rolled circular layout (nodes size ∝ √n_studies; edges width ∝ n_head_to_head_trials) — closes the NMA-geometry deferral from P1 #2. League table renders as a square colour-coded matrix (green=protective, red=risk-increasing, white=null-crossing); SUCRA ranking table; landscape PDF + DOCX. New `skill.nma` permission (researcher + admin; student blocked). Dispatcher routes `/nma`, `/network-ma`, `/indirect-comparison`, `/league-table` slash + keywords ("network meta-analysis", "NMA", "indirect comparison", "league table", "SUCRA", "mixed-treatment comparison", "ranking of treatments") + "compare N <noun>" with N≥3. Anti-hallucination posture matches meta_analysis: PMIDs from `search_papers` only; pooled effects + CIs + SUCRA from sandbox runs only; transitivity rationale required in `pico.rationale`. | `src/research_assistant/agent/specialists/nma.py`, `tools/data_science/nma_analysis.py`, `nma/sandbox_scripts/{frequentist,bayesian,geometry}.py`, `reports/nma.py`, `domain/nma.py` |
| Source-document extraction (EHR → eCRF / extraction table) — 4 new tables (`SourceDocument`, `SourceRow`, `ExtractionMapping`, `ExtractionFill`) + CSV ingest with SHA-256 content-hash dedupe + per-deployment per-form versioned mappings + dual apply modes (subjects=eCRF pre-fill OR table=flat IPD-meta projection). Per-cell `ExtractionFill` audit row carries `mapping_version` denormalised so the audit chain survives mapping mutations. `RESTRICT` on mapping FK from ExtractionFill prevents deletion while audit history exists. 9 new endpoints under `/api/edc/` (upload + list + read rows + create / list mappings + dry-run + apply + list fills). 5 new RBAC permissions wired into 5 clinical roles (coordinator uploads + applies at point-of-care; data_manager is primary owner; study_designer authors mappings; PI/monitor/auditor read). 2 new scope resolvers (`doc_id`, `mapping_id`). collector.html Source-extraction panel with file upload + subject-code field hint + recent docs / active mappings lists. 20MB upload cap; CSV-only this slice (FHIR/JSON bundles deferred to RWE P3). PHI minimisation: operator pre-de-ids; platform enforces nothing (documented). | `src/research_assistant/persistence/clinical/{models,repository}.py`, `src/research_assistant/web/{edc,authz}.py`, `src/research_assistant/auth/rbac.py`, `src/research_assistant/web/static/collector.html` |
| Visit scheduling + participant reminders — five new clinical-store tables (`VisitSchedule`, `ScheduledVisit`, `PlannedVisit`, `ParticipantContact`, `SentReminder`) + a new `baseline_date` column on `Subject`. Repository methods enforce state invariants: reschedule requires non-empty `override_reason`; reminder offsets must be days BEFORE due_date (non-positive ints); planned-visit generation is idempotent. New `services/reminders.py` fire pipeline with AWS SES email send when `AWS_SES_FROM_EMAIL` env is set, else dry-run (writes `SentReminder` rows with `provider='dry_run'`). SMS skipped this slice (Twilio not wired); rows logged with `status='skipped'`. APScheduler interval job `reminders:fire-due` registered alongside the existing watch-runner cron jobs (interval configurable via `REMINDER_INTERVAL_MINUTES`, default 15). 11 new endpoints under `/api/edc/` (visit-schedule CRUD + activate; scheduled-visit CRUD; planned-visit generate/list/update; participant-contact upsert; sent-reminder list; manual run-due trigger). 6 new RBAC permissions (`visit_schedule.author`, `visit_schedule.read`, `visit.update`, `participant_contact.manage`, `reminder.send`, `reminder.read`) wired into 5 clinical roles. New scope resolvers for `schedule_id` / `planned_visit_id` / `access_id`. collector.html Visit-calendar panel with overdue badges, schedule activator, generate-planned-visits trigger, mark-complete inline buttons. PHI minimisation: ParticipantContact stores email/phone only on opt-in; opt-out flips the row to skipped without deleting (preserves audit trail). | `src/research_assistant/persistence/clinical/{models,repository}.py`, `src/research_assistant/services/{reminders,scheduler}.py`, `src/research_assistant/web/{edc,authz}.py`, `src/research_assistant/auth/rbac.py`, `src/research_assistant/web/static/collector.html` |
| Recruitment / screening logs — new `ScreeningLog` model in the clinical store with three independent state machines (eligibility / consent / enrolment), CONSORT 2010 codebook for the 8 canonical screen-failure reasons, and 6 endpoints under `/api/edc/`. State invariants enforced at the repo layer (consent only after eligible; enrolment only after consented + with a Subject in the same deployment). Recruitment-funnel rollup endpoint returns per-week × per-site stage counts + per-reason exclusion histograms. PHI minimisation: identity-light demographics (age_band / sex / race / ethnicity / dob_year only — no full DOB / MRN). 3 new permissions (`screening.record`, `screening.update`, `screening.read`) wired into the RBAC matrix: coordinator captures at point-of-care, PI/DM update, monitor/auditor read. Site-aware `log_id` scope resolver leaves room to tighten to site-scoped grants later without endpoint changes. Collector.html Recruitment panel surfaces the funnel + recent screenings + a record form. Study-lock aware (no new screenings while the deployment-wide lock is active, mirroring the E7b posture). | `src/research_assistant/persistence/clinical/{models,repository,recruitment_terminology}.py`, `src/research_assistant/web/{edc,authz}.py`, `src/research_assistant/auth/rbac.py`, `src/research_assistant/web/static/collector.html` |
| Beyond-forest-plot visualisations — three sandbox-rendered diagnostics + one host-rendered visual SoF. **Funnel plot + Egger's test** (publication bias): new `funnel.py` script under `visualizations/sandbox_scripts/` uses `statsmodels.api.OLS` to fit Egger's regression (standardised effect on precision); meta_analysis specialist's STEP 5 now calls `run_visualisation(viz_kind="funnel", ...)` per outcome with ≥3 studies; new optional fields `funnel_plot_image`, `eggers_p_value`, `funnel_interpretation` on `MetaAnalysisOutcomeResult`. **Waterfall** (per-subject best response): `waterfall.py` script renders RECIST 1.1 categorised bars (CR/PR/SD/PD per ≤−30% / ≥+20% thresholds); trial_stats specialist gains an optional STEP 6.5 `subject_visualisations` turn variant carrying `WaterfallResult`s with `derived_from = "sandbox:waterfall:<outcome>"`. **Swimmer** (treatment timeline): `swimmer.py` script renders per-subject horizontal bars with event markers (response_onset / cr / pr / progression / death / off_treatment) + ongoing-treatment arrows; same `subject_visualisations` carrier with `SwimmerResult.derived_from = "sandbox:swimmer:<outcome>"`. **GRADE chip table** (visual SoF): hand-rolled SVG renderer in `visualizations/chip_table.py` — rows = outcomes, columns = 5 downgrade domains + 3 upgrade domains + computed certainty, each cell colour-coded green/amber/red per the GRADE-pro / Cochrane convention; auto-derived host-side from existing assessments (no agent involvement); embedded as a native reportlab Table in the GRADE PDF + python-docx Table in DOCX; SVG byte-string also stored on `GradeDocument.chip_table_svg` for the web frontend. New shared `run_visualisation(viz_kind, data_payload)` tool wraps `sandbox_exec` with all three scripts via `importlib.resources` (mirrors `run_trial_analysis` pattern). NMA geometry deferred (depends on the NMA specialist — P1 Bayesian/NMA). No new dependency (statsmodels/matplotlib pinned in sandbox; chip SVG hand-rolled with `html.escape` for XML safety). | `src/research_assistant/visualizations/` (script_loader + sandbox_scripts/{funnel,waterfall,swimmer}.py + chip_table.py), `tools/data_science/visualisations.py`, extensions in `domain/{meta_analysis,trial_stats,grade}.py`, specialist + dispatcher extensions, `reports/{meta_analysis,trial_stats,grade}.py` |
| Trial-specific statistical analysis specialist — new `trial_stats` specialist + 7-stage post-lock workflow (intake → analysis populations → time-to-event → continuous (MMRM) → binary → subgroup → assembled document). New `run_trial_analysis` tool wraps `sandbox_exec` with 4 canonical scripts in `src/research_assistant/trial_stats/sandbox_scripts/`: `kaplan_meier.py` (K-M + log-rank + Cox PH HR via statsmodels.duration.PHReg + censor ticks), `mmrm.py` (MMRM via statsmodels.MixedLM with visit fixed effects + TRT × visit interactions + optional BASE covariate), `binary.py` (Fisher's exact + log-binomial GLM with auto-fallback when log-binomial fails to converge), `subgroup_forest.py` (per-subgroup Cox HR + interaction p via TRT×SUBGROUP terms + landscape forest PNG). Every result row (TimeToEventResult / ContinuousResult / BinaryResult / SubgroupAnalysis) carries a required `derived_from` of the form `sandbox:<kind>:<paramcd>` — schema enforces HRs / LSMean diffs / CIs / p-values come from the sandbox, NEVER inline. `TrialStatsDocument.csr_artefact_ids` exposes a `TrialStats <kind>-<paramcd>` shape the CSR drafter can cite as a valid Efficacy `derived_from` source (cross-handoff seed `Draft CSR from trial-stats`); the CSR system prompt now explicitly lists those ids alongside `TLF` and `ADTTE`. PDF (landscape) + DOCX with per-endpoint tables, inline K-M curves, subgroup forest plots. Tool gating: `run_trial_analysis` available from STEP 2 onward; `web_search` / `wikipedia` gated to the assembled-document stage. New `skill.trial_stats` permission (researcher + admin; student blocked, all clinical roles blocked). Dispatcher: `/trial-stats` / `/efficacy` / `/km` / `/mmrm` slash + "Kaplan-Meier" / "MMRM" / "Cox PH" / "log-rank" / "subgroup forest" / "ITT vs PP" / "interaction p-value" keywords. | `src/research_assistant/agent/specialists/trial_stats.py`, `tools/data_science/trial_analysis.py`, `trial_stats/sandbox_scripts/*.py`, `reports/trial_stats.py`, `domain/trial_stats.py` |
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
- **E7b shipped 2026-05-30:** AE / deviation / CAPA / query write-paths now also gated by the study lock — 13 new gate calls across record/classify/code/mark-reported AE, raise/respond/close query, record/classify/close deviation (both subject- and deployment-scoped), and add/complete CAPA. Repository gains 4 new `deployment_id_for_*` resolvers + 4 matching `_require_deployment_unlocked_for_*` helpers in `web/edc.py`. RTM gains LOCK-004 ("Study lock also blocks AE / deviation / CAPA / query writes"). The lock now covers **every** mutation path through the EDC API — CDISC derivation is deliberately allowed post-lock (lock-then-derive is the regulator-default flow).

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

#### ~~ClinicalTrials.gov / EU CTR registration drafter~~ · ✅ shipped 2026-05-30 · M

New `registration_drafter` specialist + 4-stage workflow (intake → core fields → CT.gov + EU CTR drafts → assembled registration document with background paragraph). Output is paste-able into the registry portals. NCT IDs / CTIS trial IDs are NEVER fabricated — assigned by the registries on submission. PDF + DOCX export via `reports/registration.py`. New `skill.registration_drafter` permission (researcher + admin; student blocked). Dispatcher routes `/register`, `/ctgov`, `/euctr`, `/ctis`, `ClinicalTrials.gov`, `EudraCT`, `trial registration`, etc.

- **Dependencies satisfied:** SAP drafter shipped; provides statistical-design fields.
- **Unlocks now realised:** prospective trial start-up; mandatory pre-enrolment registration workflow.

#### ~~IRB / ethics submission packet + Informed Consent Form drafter~~ · ✅ shipped 2026-05-30 (lite) · L

New `irb_drafter` specialist + 3-stage workflow (intake → protocol synopsis → ICF) shipping the two highest-value pieces of the IRB packet:

- **Protocol synopsis** — 1-2 page IRB-triage summary with 8 ICH E6(R2)-aligned sections (design / objectives / endpoints / methods / statistics / eligibility / schedule / risks).
- **Informed Consent Form** — aligned to 21 CFR §50.25(a) required elements A-I (purpose / procedures / risks / benefits / alternatives / confidentiality / injury / contacts / voluntariness) with a schema-enforced 9-section minimum so the drafter can't omit one. ICF carries a configurable Flesch-Kincaid reading-level target + the computed actual grade (above-target flagged in the PDF). Multilingual: en/es/fr/de (other languages → clarification asking for a human translator).

PDF + DOCX export via `reports/irb.py` with the ICF acknowledgement + signature block. New `skill.irb_drafter` permission (researcher + admin; student blocked). Dispatcher routes `/irb`, `/icf`, `/consent`, "IRB", "ICF", "informed consent", "protocol synopsis", "21 CFR 50.25", "ICH E6", "ethics committee", etc.

- **Deferred for follow-up:** investigator CV insert / FDA Form 1572; DSMB / DMC charter draft; site-level supplementary packets. Those don't share the ICF compliance shape — easier to add as additional drafters later than to over-scope this slice.
- **Unlocks now realised:** ethics-submission turnaround for the two artefacts that take the most time today; first real "weeks → hours" claim for start-up. Cross-handoff "Draft IRB packet from registration intake" lets the operator carry registration_drafter's intake fields into the IRB workflow without re-typing.

### Execution

#### ~~Randomisation / IRT (Interactive Response Technology) service~~ · ✅ shipped 2026-05-30 · M

New `RandomizationSchedule` + `Allocation` + `CodeBreakEvent` models + the `randomization/` package with 4 algorithms — simple / permuted_block / stratified_permuted_block / Pocock-Simon minimisation. The data_manager generates the schedule at study-start (seeded for audit reproducibility); the coordinator + PI consume entries via the per-subject allocation call at enrolment. Open-label deployments reveal the arm at allocation; single_blind / double_blind / triple_blind deployments mask the arm in API responses until a PI-triggered code-break unblinds it. CDISC integration: `derive_adsl` pulls `TRT01P` / `TRT01A` from `Allocation.arm` when present (no more "TBD" placeholder); ADTTE inherits via ADSL. 6 endpoints under `/api/edc/{deployments,subjects}/...` gated by 4 new permissions (`randomization.generate` / `.allocate` / `.codebreak` / `.read`). Minimal `Randomisation` panel in collector.html showing the schedule + per-arm allocation counts (blinded counts surface as a separate cell).

- **Dependencies satisfied:** RBAC scoped roles + scope-bound permission checks already shipped; the role split (data_manager generates, coordinator allocates, PI breaks) is enforced directly via `Permission.RANDOMIZATION_*`.
- **Unlocks now realised:** the RCT market for the eCRF subsystem; ADSL.TRT01P/A flows through real allocations rather than placeholders.
- **Deferred for follow-up:** schedule extension (adding more entries to an active schedule mid-study), drug-supply tracking, sponsor-notification webhooks on code-break events.

#### ~~AE / SAE workflow on top of eCRF~~ · ✅ shipped 2026-05-29 · M

Was a compliance-critical gap — closed. New `safety_rules.auto_classify_serious()` applies ICH E2A §III.A criteria (grade ≥3, death, life-threatening, hospitalisation, congenital, persistent disability, other medically significant) on every AE write; `compute_reporting_deadline()` stamps a 24-hour platform escalation timer for serious events. The overdue-SAE endpoint surfaces past-deadline events that haven't been reported. PI overrides flow through `PATCH /api/edc/ae/{id}` and clear the deadline on downgrade. FDA 3500A IND safety report draft shipped via `reports/sae_3500a.py` — fields the platform can derive are filled; sponsor-supplied fields (IND number, NDA/BLA number, investigator name + address) are marked `[SPONSOR INPUT REQUIRED]` rather than fabricated. PHI minimisation enforced (only subject_code; never participant name). MedDRA Preferred Term is captured as free text; real validation needs a MedDRA license at deploy — documented in the endpoint docstring + this row. SUSAR detection is the next-most-natural follow-up (it's an SAE subset with `unexpected=True` and `relationship_to_intervention >= probable`) and remains intentionally deferred.

### Analysis / Reporting

#### ~~CDISC SDTM mapping → ADaM derivation → TLF generation~~ · ✅ shipped 2026-05-29 · XL

**Two slices shipped:** first slice (2026-05-29) covered SDTM **DM + AE + VS**, ADaM **ADSL**, and a basic TLF set (3 tables + 1 SVG figure). Second slice (2026-05-30) added SDTM **LB + EX + CM + MH** via a repeating-form pattern (one form-instance → one SDTM row). The pluggable `CdiscMapper` Protocol leaves room to swap in an OSS-backed implementation later without touching the API surface.

- **What landed (slice 1 — DM/AE/VS/ADSL/TLF):** `cdisc/` package — `BuiltinPythonMapper` derives DM/AE/VS via an item-id mapping convention (`age`→AGE, `sex`→SEX, `sbp`→SYSBP …) overridable per deployment via `ItemMappingConfig`; controlled-terminology JSON for AE severity / outcome / relationship + DM sex / race + VS test codes; ADSL builds SAFFL / ITTFL / DTHFL / AGEGR1 (ICH E1 bins) with TRT01P/TRT01A defaulting to `"TBD"` until randomisation lands; TLF generator emits Disposition / Demographics / AE-summary tables (`content_json`) plus a hand-rolled top-10 AE-frequency SVG; per-domain CSV with SDTM column ordering + ZIP submission bundle (`sdtm/*.csv` + `adam/adsl.csv` + `tlf/*` + `define-overview.txt` manifest); `CdiscDerivation` audit row per run. Six endpoints under `/api/edc/deployments/{id}/cdisc/*` gated by `cdisc.derive` (data_manager only) / `cdisc.read` (data_manager, PI, monitor, auditor) / `cdisc.export` (data_manager + PI). Submissions card in `collector.html` with run-derivation, per-domain CSV download links, bundle download, and collapsible TLF previews.
- **What landed (slice 2 — LB/EX/CM/MH):** four new SDTM domains follow a **repeating-form** pattern — each form-instance of a designated form (`form_to_domain_map` in `ItemMappingConfig`) becomes one SDTM row. **LB**: unit-aware result + reference range; LBNRIND auto-derived (NORMAL / LOW / HIGH) using either form-captured ranges or the CT default for the test code (HGB, GLUC, ALT, AST, ALP, BILI, CREAT, BUN, NA, K, CL, CO2, CA, ALB, LDL, HDL, TG, CHOL, HBA1C and others). **EX**: route normalised against `ex_routes.json` (oral → ORAL, iv → INTRAVENOUS, etc); unknown routes pass through verbatim. **CM**: free-text CMTRT + optional ATC/WHODrug placeholder (`CMDECOD`); indication + dose. **MH**: MHTERM + MHCAT canonicalised via `mh_categories.json`; MHONGO auto-derived ("Y" when end date missing, "N" otherwise). Per-subject sequencing (LBSEQ / EXSEQ / CMSEQ / MHSEQ) stable across reruns. Submission bundle now ships 7 SDTM CSVs + ADSL + TLF; manifest counts all 7 domains.
- **Resolved open question:** built internally in Python with a `CdiscMapper` Protocol seam — no R/OSS dependency at deploy time; an OSS-backed `pinnacle`/`OAK` subprocess implementation can be registered without changing endpoints when the platform is deployed in environments where that's preferred.
- **Slice 3 (2026-05-30) — ADaM ADTTE + sandbox K-M / Cox PH:** `AdamAdtte` SQLAlchemy model + `derive_adtte` emit one row per subject × PARAMCD for the three standard MVP parameters (TTAE / TTSAE / DEATH). Censoring follows ADaM convention (CNSR=0 event, CNSR=1 censored at last follow-up). New `t-tte-summary` TLF uses a pure-Python K-M estimator to compute the median per parameter (handles censoring properly; reports "not reached" when the median is outside the observation window). First CDISC output to round-trip the sandbox: `cdisc/sandbox_scripts/survival_analysis.py` runs `statsmodels.duration.PHReg` for Cox PH (TRT01A as covariate, stratified when ≥2 arms with ≥5 events) + matplotlib K-M plots (with censor ticks). POST `/api/edc/deployments/{id}/cdisc/survival/render` (gated `cdisc.derive`, 503 when sandbox disabled, 409 before any derivation) drives the sandbox, then base64-encodes the K-M PNGs into `TlfArtefact.svg_content` as data URIs + parses the Cox JSON into a `t-cox-ph-summary` table. Re-runs are idempotent (prior K-M / Cox rows wiped before persistence). Collector's Submissions card gains the ADTTE count cell + the "📈 Render survival" button; the TLF preview detects `data:image/png` and renders via `<img>` (vs inline SVG for the hand-rolled PRISMA / AE-frequency figures). **No sandbox image rebuild required** — statsmodels was already pinned; lifelines was deliberately avoided.
- **Slice 4 (2026-05-30) — Define-XML v2.1 + SAS Transport v5:** the submission-format gap closed. New `cdisc/_metadata.py` shared registry pins per-column type/length/label across the writers. `cdisc/define_xml.py` emits a valid Define-XML v2.1 with 9 ItemGroupDefs, every ItemDef typed and labelled, CodeLists built from the existing `cdisc/terminology/*.json` files (sex/race/AE outcome+sev+rel/VS+LB test codes/EX routes/MH categories/NY/PARAMCD.ADTTE/CNSR/AVALU), and MethodDefs covering every derived column (AGEGR1, LBNRIND, MHONGO, SAFFL, DTHFL, ADTTE AVAL+CNSR). `cdisc/xpt_writer.py` is a hand-rolled SAS Transport v5 writer with IBM-360 8-byte float conversion + 80-byte record alignment + 140-byte v5 namestr records; validates the 8-char column-name and 200-char string-length v5 limits explicitly. Bundle ZIP now ships side by side: `define.xml` at root, the existing `define-overview.txt` retained as a quick human summary, `sdtm/*.{csv,xpt}` for all 7 SDTM domains + `adam/{adsl,adtte}.{csv,xpt}`. **No new dependency added** — `xport`/`pyreadstat` deliberately avoided; the format is hand-rolled to stay aligned with the project's minimal-deps posture. 42 new tests cover IBM-float byte layout, namestr record positions, observation 80-byte alignment, Define-XML structural conformance, and bundle file presence.
- **Deferred for follow-up:** real MedDRA SOC back-indexing for AEDECOD/MHDECOD (PT is captured free-text; needs license at deploy); WHODrug for CMDECOD; additional ADTTE parameters beyond TTAE/TTSAE/DEATH (the architecture supports them — add to the finders list in `derive_adtte`). ~~Randomisation-derived TRT01P/TRT01A — waiting on IRT~~ → shipped 2026-05-30 as the IRT slice; ADSL now pulls TRT01P/A from the `Allocation` row.
- **Unlocks now realised:** structured submission-bundle generation for sponsor / CRO adoption with all the most-requested SDTM domains + ADaM datasets; the CSR drafter (P0, below) now has its full ADaM analysis-dataset prerequisites and can begin.

#### ~~Clinical Study Report (CSR, ICH E3) drafter~~ · ✅ shipped 2026-05-30 (synopsis + 4 data sections) · XL

First slice landed: new `csr_drafter` specialist + 4-stage workflow (intake → synopsis → 4 data-driven sections → assembled CsrDocument). Skeleton CSR carries all 16 ICH E3 section headers; the data sections (Disposition / Demographics / Efficacy / Safety) populate from operator pastes of ADSL + ADTTE + TLF artefacts; the narrative sections (Introduction / Discussion / Overall Conclusions) ship as `[Operator to complete]` placeholders this slice — the next slice drafts them.

- **What landed:** `domain/csr.py` with the discriminated-union turn shape + 5 building blocks (`DispositionTable` / `DemographicsTable` / `EfficacyResults` / `SafetyOverview` + every count carries a required `derived_from` source-artefact id). `agent/specialists/csr_drafter.py` with the strictest anti-hallucination system prompt in the codebase ("NEVER invent patient counts / effect sizes / point estimates / CIs / p-values; EVERY number traces back to a TLF or ADaM paste"). `reports/csr.py` PDF + DOCX bundling: renders the full ICH E3 section spine + data tables with their `derived_from` footers. New `skill.csr_drafter` permission (researcher + admin; student blocked). Dispatcher routes `/csr`, `/e3`, `/study-report`, "Clinical Study Report", "ICH E3", "CSR drafter / draft / document" keywords. Disambiguated continuation prefixes (`CSR intake confirmed` / `CSR synopsis confirmed` / `CSR data sections confirmed` / `Refine CSR:` / `Finalize CSR`) avoid collision with the registration_drafter's generic `Intake confirmed`. Cross-handoff seeds: `Draft CSR from meta-analysis`, `Draft CSR from ADTTE`, `Draft CSR from SAP`.
- **Dependencies satisfied:** SDTM + ADaM + TLF (shipped via CDISC slices 1-4); ADSL + ADTTE provide the patient counts + efficacy numbers; AE summary TLF provides the safety counts; Define-XML packaged alongside the CSR bundle.
- **Anti-hallucination posture:** the strictest in the codebase. Required schema field `derived_from` carries the source artefact id for every count (e.g. `"TLF t-disposition"` / `"ADTTE PARAMCD=TTAE row"`). System prompt rule: "When a required count is missing, emit clarification rather than guessing." Forbidden inline-write of effect sizes / CIs / p-values without a TLF / ADTTE source.
- **Deferred for the next CSR slice:** narrative sections (Introduction / Discussion / Overall Conclusions) — they need their own anti-hallucination scaffolding (the model can interpret data sections without fabricating, but the system prompt + validators need careful work); Investigator structure tables; per-AE narrative auto-write; per-TLF artefact inclusion as inline figures; multi-language CSR rendering.

---

## P1 — High-leverage structural gaps

### Execution

#### ~~Protocol-deviation tracking + CAPA~~ · ✅ shipped 2026-05-29 · M

Was a regulator's first ask at inspection — closed. New `ProtocolDeviation` + `CapaAction` clinical-store models with full lifecycle: log (coordinator/monitor) → classify (data_manager/PI) → add CAPAs (data_manager) → complete CAPAs (owner) → close deviation (PI; refused while any CAPA is still open). The eCRF audit trail records *what changed* on individual items; this layer is the higher-level classification on top. Surfaces in the collector's per-subject safety panel.

#### ~~Recruitment / screening logs~~ · ✅ shipped 2026-05-30 · S

Shipped as an eCRF subsystem addition (no new agent specialist):

- **ScreeningLog model** in `persistence/clinical/models.py` — one row per prospect, with sponsor-assigned `screening_code` (NOT USUBJID), identity-light demographics (age_band, sex, race, ethnicity, dob_year — full DOB excluded as identifying PHI), and three state machines (eligibility / consent / enrolment) with documented invariants enforced at the repository layer.
- **CONSORT 2010 codebook** — `persistence/clinical/recruitment_terminology.py` exposes the 8 canonical screen-failure reasons (age_out_of_range / lab_or_imaging_abnormality / prior_treatment / pregnancy / declined_consent / inclusion_criteria_not_met / exclusion_criteria_met / withdrawn) + `other` for free-text. The repository enforces a code is supplied when marking screen_failure.
- **State invariants enforced at the repo layer**: consent_status can only become 'consented' after eligibility_status='eligible'; enrolment_status can only become 'enrolled' after consent_status='consented' AND with an enrolled_subject_id pointing at a Subject in the same deployment.
- **6 endpoints** under `/api/edc/`: POST `/deployments/{id}/screening` (coordinator records), GET `/deployments/{id}/screening` (filterable list), PATCH `/screening/{log_id}/{eligibility,consent,enrolment}` (coordinator + PI + DM update), GET `/deployments/{id}/recruitment/funnel` (all clinical roles + auditor read). New `log_id` scope resolver in `web/authz.py` returns `(deployment_id, site_id, None)` so site-scoped grants can be tightened later without changing endpoint code.
- **Recruitment-funnel rollup** — totals (screened / eligible / consented / enrolled), per-reason screen-failure counts, per-ISO-week × per-site stage counts. Computed in-Python (the per-deployment screening volume is small enough that a single fetch + dict aggregation is faster than a multi-CTE SQL aggregation and portable across SQLite + Postgres).
- **3 new RBAC permissions**: `screening.record` (coordinator), `screening.update` (coordinator + PI + DM), `screening.read` (coordinator + PI + DM + monitor + auditor). Student tier + all skill-based roles explicitly blocked from all three — recruitment is a clinical-operations action, not a researcher artefact.
- **collector.html Recruitment panel** — funnel stats (4-cell row), screen-failure reasons sorted by count, recent screening rows, and a one-field record form. Permission-gated via the 403 response.
- **Study-lock aware** — `record_screening` calls `_require_deployment_unlocked` so locking the study halts new screenings (the existing pattern from E7b).
- **PHI minimisation enforced at the schema layer** — only `dob_year` (not full DOB), age bands (not exact age). NIH diversity-reportable race + ethnicity present for FDA Form 1572 / NIH reporting; sites concerned about per-row PHI risk can leave them None.

#### ~~Visit scheduling + participant reminders~~ · ✅ shipped 2026-05-30 · M

Shipped as an eCRF subsystem addition with five new tables, a reminder send-pipeline, and an APScheduler interval job that fires every 15 min:

- **Five new clinical-store tables**: `VisitSchedule` (per-deployment, named, only one `is_active=True` at a time), `ScheduledVisit` (visit name + `day_offset` from baseline + `window_before_days` / `window_after_days` + `reminder_offsets_json` list of pre-due days when reminders fire), `PlannedVisit` (per-subject row with computed planned_date + window_start/end + status + `override_reason` for coordinator reschedules), `ParticipantContact` (1:1 with ParticipantAccess; email/phone/preferred_channel + opt_in_channels_json + opt_out_at), `SentReminder` (audit log; unique constraint on `(planned_visit_id, offset_days, channel)` enforces idempotency across scheduler restarts). New `baseline_date` column on `Subject` (nullable; falls back to `created_at` when None).
- **Repository contract**: state invariants enforced at the repo layer — reschedule requires non-empty `override_reason`; positive reminder offsets rejected (reminders are days BEFORE due_date); cross-deployment subject linkage rejected; planned-visit generation is idempotent (skips already-generated per subject + scheduled-visit pair).
- **`services/reminders.py`** with `fire_due_reminders(deployment_id)` and `fire_due_reminders_all_deployments()`. AWS SES email via boto3 when `AWS_SES_FROM_EMAIL` env is set; otherwise dry-run (writes `SentReminder` rows with `provider='dry_run'`, status='sent'). SMS skipped this slice (Twilio not wired) — emitted with `status='skipped'`. The fire loop is per-deployment and commits after each batch so a provider crash mid-batch doesn't lose audit-trail of already-sent reminders.
- **APScheduler interval job** `reminders:fire-due` registered alongside the existing watch-runner cron jobs. Interval configurable via `REMINDER_INTERVAL_MINUTES` env (default 15, set to 0 to disable). `register_reminder_job()` is called from `start_scheduler()` so the job is re-registered on every FastAPI startup.
- **11 new endpoints** under `/api/edc/`: visit-schedule CRUD + activate; scheduled-visit CRUD; planned-visit generate-for-subject + list (per-subject + per-deployment) + update (reschedule / complete / cancel); participant-contact upsert; sent-reminder list; manual reminder run-due trigger. New scope resolvers (`schedule_id`, `planned_visit_id`, `access_id`) wired into `web/authz.py`.
- **6 new RBAC permissions** wired into the role matrix: `visit_schedule.author` (study_designer + data_manager), `visit_schedule.read` (all clinical roles + auditor), `visit.update` (coordinator + PI), `participant_contact.manage` (coordinator only — point-of-care), `reminder.send` (data_manager — manual trigger only; the scheduler runs as admin), `reminder.read` (PI + DM + monitor + auditor). Student tier + skill-only roles blocked from everything.
- **collector.html Visit-calendar panel** with active-schedule indicator, add-visit form, pending-visit list with overdue badges (red when `window_end` in the past), generate-planned-visits button for the selected subject, mark-complete inline buttons.
- **PHI minimisation enforced at the schema layer**: ParticipantContact stores email + phone only when the participant has explicitly opted in (`opt_in_channels_json`); opt_out_at flips the row to skipped for all future runs without deleting the row (preserves audit trail).
- **Same pattern as the recruitment / screening slice (P1 #3)**: subsystem addition, not a new agent specialist. Lives next to the existing safety + screening + randomisation surfaces in the EDC namespace. Study-lock-aware writes deferred this slice (visit scheduling continues during lock — operationally the trial is closing out, the last few visit completions still need to be recordable).

#### ~~Source-document extraction (EHR → extraction table / eCRF pre-fill)~~ · ✅ shipped 2026-05-30 · L

Shipped as an eCRF subsystem with 4 new clinical-store tables, an `apply` pipeline supporting BOTH prospective FormInstance pre-fill AND retrospective extraction-table output, and full per-cell audit provenance:

- **`SourceDocument`** (per-upload metadata: filename, content_hash for SHA-256 dedupe, row_count, headers_json, uploaded_by_sub) + **`SourceRow`** (one per row in the upload; payload_json + denormalised subject_code_hint for fast apply-time lookup). CSV parsing via stdlib `csv` (no pandas dependency); 20MB upload cap.
- **`ExtractionMapping`** (per-deployment × per-DeployedForm; `version` int that bumps on every mutation; `subject_code_field` names the column carrying the subject identifier; `mapping_json` is the source-field → form-Item.id dict). Only one mapping per (deployment, form) is `is_active=True` at a time — creating a new version auto-deactivates the prior active row. `ondelete=RESTRICT` on the ExtractionFill FK so the mapping row can't be deleted while audit history exists.
- **`ExtractionFill`** (per-cell audit row; `mapping_version` is denormalised so the audit survives mapping mutations; `target_kind='item_data'` or `'extraction_cell'`). Regulators can click any ItemData / extraction-table cell and trace it back to the exact source row + field + mapping version that produced it.
- **`apply_extraction_to_subjects`** writes FormInstance + ItemData + ExtractionFill rows. Idempotent at the (form_instance, item_id) level — re-applying the same source row overwrites the value but appends a new ExtractionFill audit row (the audit trail of every apply attempt is preserved). Unmatched source rows (subject_code not in deployment) are counted but don't crash the pass.
- **`apply_extraction_to_table`** produces a flat per-row projection for retrospective studies / IPD meta-analyses. NO eCRF write — just the per-row dict + ExtractionFill audit rows tying each cell to its source. Operator exports.
- **Content-hash dedupe at ingest.** Re-uploading the same bytes returns the existing SourceDocument row with `rows_added=0` — no double-ingest. Re-uploads with edited content get a new row + audit trail.
- **9 endpoints** under `/api/edc/` (upload + list source docs + read rows + create / list mappings + dry-run + apply + list extraction fills). 2 new scope resolvers (`doc_id`, `mapping_id`).
- **5 new RBAC permissions**: `source_document.upload` (coordinator + data_manager), `source_document.read` (all clinical roles + auditor), `extraction_mapping.author` (study_designer + data_manager), `extraction_mapping.apply` (coordinator + data_manager), `extraction.audit_read` (PI + data_manager + monitor + auditor). Student + skill-only roles blocked.
- **collector.html Source-extraction panel** with file upload (CSV-only this slice) + subject-code field hint + recent docs list + active mappings list.
- **De-identification posture: operator pre-de-ids; platform enforces nothing.** Documented in the upload endpoint docstring; the platform stores the file bytes verbatim. PII heuristic scans deferred to a future slice; FHIR/JSON bundle support deferred to the RWE P3 work.

### Analysis / Reporting

#### ~~Manuscript drafter (IMRaD) + reviewer-response loop~~ · ✅ shipped 2026-05-29 · L

Shipped as a new `manuscript_drafter` specialist + a 3-stage workflow: intake (target journal + section seeds + source artefact paste) → IMRaD draft (title + structured abstract + Introduction + Methods + Results + Discussion + References with `origin` field) → reviewer-response loop (cover letter + per-item responses with optional suggested manuscript edits + `is_addressed` flag for pushed-back items).

- **Composition source:** the meta_analysis card gains a "→ Draft as manuscript" handoff button that seeds the new thread with the full meta-analysis JSON pasted into `intake.source_artefact_paste`; the manuscript-drafter system prompt forbids inventing Results numbers and requires every effect size to trace back to the paste verbatim.
- **Journal targets:** NEJM / Lancet / BMJ / JAMA / Annals / PLOS ONE / generic, driving abstract / body word budgets via the system prompt.
- **Reference posture:** every citation carries `origin` (`search_papers` / `web_search` / `wikipedia` / `pasted_source`) so a reader can verify each reference came from a real tool call this turn.
- **Reports:** PDF + DOCX via the existing `reports/` machinery; reviewer-response document is appended to the same download when a response round exists.
- **Sequencing benefit:** the composition primitives (per-section assembly, structured-abstract table, reference list with origin tagging) carry forward into the future CSR drafter (#6).

#### ~~GRADE summary-of-findings + PRISMA reporting checklist~~ · ✅ shipped 2026-05-30 · M

New `grade_drafter` specialist + 5-stage workflow (intake → per-outcome assessment → SoF assembly → PRISMA 2020 checklist → assembled document). **Certainty is COMPUTED via Pydantic `computed_field` from the per-domain ratings** — the agent cannot inline-assert it, which closes the most common GRADE drift mode. **Every downgrade rating carries a required `rationale` field**, system-prompt-bound to cite a source number from the meta-analysis (I² value, CI bounds, n_studies, Egger's p, etc) rather than handwaving.

- **What landed:** `domain/grade.py` — discriminated-union turn shape (6 variants); `OutcomeAssessment` carries the 5 downgrade domains (risk_of_bias / inconsistency / indirectness / imprecision / publication_bias) + the 3 observational-only upgrade domains (large_effect / dose_response / residual_confounding); `compute_certainty()` implements the GRADE Handbook scoring (RCT start=high, observational start=low; serious=−1, very_serious=−2; observational moderate-upgrade=+1, large=+2; clamped to [very_low, high]). `agent/specialists/grade_drafter.py` — system prompt with the GRADE Working Group rules verbatim + concrete thresholds (I²>50% suggests serious inconsistency; CI crosses null = serious imprecision). `reports/grade.py` — PDF + DOCX bundling: SoF table with colour-coded certainty (green/yellow/orange/red), per-outcome detail with each downgrade rationale spelled out, PRISMA 2020 checklist (27 items × 5 columns). New `skill.grade_drafter` permission (researcher + admin; student blocked). Dispatcher: `/grade` / `/sof` / `/prisma-checklist` slash + "GRADE SoF" / "Summary of Findings" / "certainty of evidence" / "PRISMA checklist" keywords. Cross-handoff seed `Draft GRADE from meta-analysis`.
- **PRISMA 2020 item registry** — full canonical text from Page et al. (BMJ 2021) embedded in `PRISMA_2020_ITEMS` constant (42 sub-items across 8 sections). Operator records `reported` (yes/no/n_a) + `location` per item; the report renders missing items as "Not reported".
- **Dependencies satisfied:** meta_analysis (shipped 2025) supplies the n_studies / n_participants / effect / CI / I² inputs that the operator pastes into the assessment turns.
- **Unlocks now realised:** journal-mandated SR/MA submission completeness; closes the most common BMJ / Cochrane reviewer-comment ("please add a GRADE SoF table" / "please complete the PRISMA 2020 checklist").

#### ~~Trial-specific statistical analysis specialist~~ · ✅ shipped 2026-05-30 · M

Shipped as a new `trial_stats` specialist + a 7-stage post-lock workflow:

- **Workflow:** intake → analysis populations → time-to-event → continuous (MMRM) → binary → subgroup → assembled artefact. Each step is a discriminated-union turn shape with `derived_from` as a required field on every numerical result row.
- **Sandbox-backed analyses:** four canonical scripts live under `src/research_assistant/trial_stats/sandbox_scripts/` (`kaplan_meier.py`, `mmrm.py`, `binary.py`, `subgroup_forest.py`). The new `run_trial_analysis(analysis_kind, data_payload)` tool loads the right script via importlib.resources and forwards to `sandbox_exec`. K-M / Cox PH reuses statsmodels.duration.PHReg (same backend as the CDISC ADTTE pipeline); MMRM uses MixedLM with random subject intercept + TRT × visit interactions + optional BASE covariate; binary uses Fisher's exact with a log-binomial GLM option that auto-falls back when convergence fails; subgroup forest fits per-subgroup Cox + a TRT × SUBGROUP interaction test.
- **Architectural anti-hallucination gate:** every `TimeToEventResult` / `ContinuousResult` / `BinaryResult` / `SubgroupAnalysis` row carries a required `derived_from = "sandbox:<kind>:<paramcd>"`. Pydantic validators forbid an HR / LSMean diff / RR without paired CI bounds; conversely they forbid a skip_reason alongside a fitted effect. The system prompt's ABSOLUTE RULES section enforces "every number comes from a `run_trial_analysis` call this turn or a previously-confirmed schema row."
- **CSR handoff:** `TrialStatsDocument.csr_artefact_ids` is a `@property` that emits ids in the canonical CSR shape (`TrialStats t-km-OS`, `TrialStats mmrm-CHGFBL-WK24`, `TrialStats binary-ORR`, `TrialStats subgroup-OS-by-SEX`). The CSR drafter's STEP 3 system prompt now explicitly lists these as valid `derived_from` sources alongside `TLF` and `ADTTE`. Cross-handoff continuation `Draft CSR from trial-stats` seeds a new CSR thread from a finished trial-stats analysis.
- **Report:** landscape PDF + DOCX with per-endpoint tables, inline K-M curves (rendered via the sandbox's PNG output), subgroup forest plots, and a closing primary-summary paragraph that references results by PARAMCD (never restating the effect size as text).
- **RBAC:** new `skill.trial_stats` permission, researcher + admin only. Student tier and all clinical roles (coordinator / data_manager / monitor / PI / auditor / reviewers) are explicitly blocked — trial_stats is a researcher-side analysis artefact, not a clinical-operations action.
- **Dispatcher:** `/trial-stats`, `/efficacy`, `/km`, `/mmrm` slash commands; keyword triggers for Kaplan-Meier / MMRM / Cox PH / log-rank / subgroup forest / ITT vs PP / per-protocol / interaction p / trial-stats / "efficacy analysis"; continuation prefixes disambiguated against CSR drafter (`Trial-stats intake confirmed` vs `CSR intake confirmed`).
- **Deferred:** unstructured covariance matrix for MMRM (current implementation uses random-intercept MixedLM — adequate for headline LSMean diff + 95% CI but not the regulator-grade MMRM that qualifies for a final CSR table; the report explicitly documents this); tipping-point / multiple-imputation sensitivity analyses; group-sequential / alpha-spending boundaries (a separate interim-analysis specialist); adjusted Cox PH covariates beyond TRT (next slice when a sponsor pushes for it).

#### ~~Beyond-forest-plot visualisations~~ · ✅ shipped 2026-05-30 · M

Shipped as extensions to the three existing specialists (no new workflow):

- **Funnel plot + Egger's regression test** (meta_analysis specialist) — `visualizations/sandbox_scripts/funnel.py` runs `statsmodels.api.OLS` on standardised effect ~ precision; STEP 5 of the meta-analysis workflow now calls `run_visualisation(viz_kind="funnel", ...)` per outcome with ≥3 studies; results land in 3 new optional fields on `MetaAnalysisOutcomeResult` (`funnel_plot_image`, `eggers_p_value`, `funnel_interpretation`). Egger's p < 0.10 by convention signals funnel asymmetry / small-study effects.
- **Waterfall plot** (trial_stats specialist) — `waterfall.py` script categorises subjects per RECIST 1.1 (PR ≤ −30%, PD ≥ +20%) and renders a coloured-by-treatment best-response bar chart sorted by magnitude. Results carry `derived_from = "sandbox:waterfall:<outcome>"`.
- **Swimmer plot** (trial_stats specialist) — `swimmer.py` renders per-subject horizontal timelines with event markers (response onset, CR, PR, progression, death, off-treatment) and ongoing-treatment arrows. Both waterfall + swimmer ride in a new optional STEP 6.5 turn variant `subject_visualisations` between subgroup_results and the assembled document. `derived_from = "sandbox:swimmer:<outcome>"`.
- **GRADE chip table** (grade_drafter / GRADE report) — hand-rolled SVG renderer (`visualizations/chip_table.py`) for the web frontend, plus native reportlab Table + python-docx Table for the PDF/DOCX downloads. Each chip is colour-coded green/amber/red per the GRADE-pro / Cochrane convention; observational upgrades use the blue palette. Auto-derived host-side from existing `OutcomeAssessment` rows — agent never authors the SVG.
- **New shared tool `run_visualisation(viz_kind, data_payload)`** — wraps `sandbox_exec` with the 3 canonical scripts via `importlib.resources`. Mirrors `run_trial_analysis` from the trial_stats slice. Registered on both meta_analysis (for funnel at STEP 5) and trial_stats (for waterfall + swimmer at STEP 6.5).
- **No new dependency** — statsmodels + matplotlib already pinned in the sandbox image; the chip SVG is hand-rolled with `html.escape` for XML safety (no svglib).
- **NMA geometry deferred** — depends on the NMA specialist (P1 Bayesian / network meta-analysis); when that ships it should land alongside an NMA geometry script in `visualizations/sandbox_scripts/`.

#### ~~Bayesian / network meta-analysis~~ · ✅ shipped 2026-05-30 · L

Shipped as a new `nma` specialist + 5-stage workflow, with BOTH frequentist (default) and Bayesian (opt-in) backends sharing a single sandbox-script + tool-wrapper substrate:

- **Frequentist backend** (default) — `nma/sandbox_scripts/frequentist.py` implements the mvmeta-style + electrical-network analogy directly on top of `numpy.linalg` and `scipy.stats.multivariate_normal`. Builds a (#contrasts, #interventions−1) design matrix from within-study contrasts; β = (X^T W X)^{-1} X^T W y is the all-vs-reference log-effects; the inverse Fisher information drives league-table CIs; SUCRA via 1000 MVN posterior draws. No netmeta R dependency.
- **Bayesian backend** (opt-in via "Run Bayesian NMA" continuation) — `nma/sandbox_scripts/bayesian.py` implements an arm-based normal-likelihood model in PyMC (random-effects τ shared across contrasts, NUTS sampler, 2 chains × 500 tune + 500 draws). **PyMC is NOT pinned in the current sandbox image** — the script gracefully writes a structured `skip_reason` when `import pymc` fails, and the host (and system prompt) treats Bayesian as a deploy-time gate. To activate: rebuild the sandbox image with `pymc>=5` + `arviz` pinned.
- **Network-geometry visualization** — `nma/sandbox_scripts/geometry.py` renders the network plot (nodes for interventions sized by √n_studies, edges weighted by n_head_to_head_trials, hand-rolled circular layout). Matplotlib-only, no networkx dependency. This is the NMA-geometry piece that was deferred from the beyond-forest-plot slice (#2).
- **5-stage workflow**: intake → PicoNetwork (≥3 interventions, transitivity rationale required) → search → per-arm extraction → results. Continuation prefixes: `NMA PICO confirmed`, `NMA studies selected`, `NMA extraction confirmed`, `Run Bayesian NMA`, `Refine NMA:`, `Finalize NMA`. Slash commands: `/nma`, `/network-ma`, `/indirect-comparison`, `/league-table`.
- **Domain schema** (`domain/nma.py`) — `NmaTurn` discriminated union with 6 variants; `PicoNetwork.interventions: list[str] = Field(min_length=3)` enforces ≥3 arms at the schema layer; `NmaStudyCandidate` has a model-validator requiring `len(arms_evaluated) >= 2`; `SucraRow.sucra` clamped to [0,1]; `NetworkEdge.n_trials >= 1`.
- **Anti-hallucination posture** — same as meta_analysis + tighter: PMIDs from `search_papers` only; league table + SUCRA + network counts from `run_nma_analysis` sandbox runs only; never authored inline. Transitivity rationale required in `pico.rationale`; consistency caveats surfaced in `results.caveats`. Bayesian skips fall back to frequentist with the skip reason persisted in caveats.
- **`run_nma_analysis(backend, data_payload)` tool** wraps `sandbox_exec` with the 3 canonical scripts via `importlib.resources`. Mirrors the `run_trial_analysis` and `run_visualisation` tool patterns from earlier slices.
- **Landscape PDF + DOCX report** — league table (square matrix, colour-coded by direction: green = protective, red = risk-increasing, white = CI crosses null), SUCRA ranking table, inline network-geometry PNG, interpretation paragraph, caveats.
- **`skill.nma` RBAC permission** in `_EVIDENCE_SKILLS` — researcher + admin only; student blocked.
- **Dispatcher routing order** — NMA checked BEFORE meta_analysis so multi-arm questions ("compare 5 DOACs") route to the NMA specialist rather than pairwise. The `compare\s+([3-9]|\d{2,})\s+\w+` regex catches 3+ arm comparisons explicitly; 2-arm comparisons still route to meta_analysis.

#### ~~Individual Patient Data (IPD) meta-analysis~~ · ✅ shipped 2026-05-31 · L

Shipped as a new `ipd` specialist + 5-stage workflow that pools subject-level rows from ≥2 trials, runs BOTH one-stage and two-stage models side-by-side (the headline methodological diagnostic), and tests treatment × subgroup interactions (the killer feature that distinguishes IPD from aggregate MA):

- **Five-stage workflow**: intake → bundle (per-trial CSV + column mapping) → main results (one-stage + two-stage) → subgroup results (treatment × subgroup interaction) → assembled document. Continuation prefixes: `IPD intake confirmed`, `IPD bundle confirmed`, `IPD main results confirmed`, `IPD subgroup confirmed`, `Add IPD subgroup: <variable>`, `Refine IPD:`, `Finalize IPD`. Slash commands: `/ipd`, `/ipdma`, `/ipd-ma`, `/subject-level`.
- **Three sandbox scripts** in `ipd/sandbox_scripts/`:
  - `one_stage.py` — single multilevel model. Continuous: `statsmodels.MixedLM` (random trial intercept + fixed treatment, REML). Binary: `GLM` Binomial logit with trial dummies + treatment (fixed-effects approximation, documented limitation). TTE: stratified `PHReg` (strata=trial_id) + treatment.
  - `two_stage.py` — per-trial estimator + classical DerSimonian-Laird random-effects pool. Per-trial binary uses Haldane-Anscombe-corrected log-OR + var; continuous uses naive mean-difference; TTE uses single-trial Cox PH.
  - `subgroup.py` — adds a treatment × subgroup interaction term to the one-stage model; per-level pooled effects + joint Wald interaction p (minimum across SGxTRT coefficients = conservative).
- **`run_ipd_analysis(stage, data_payload)` tool** wraps `sandbox_exec` via `importlib.resources`. Mirrors the trial_stats / nma / visualisations tool patterns.
- **Domain schema** (`domain/ipd.py`) — `IpdTurn` discriminated union with 6 variants. `IpdBundleTurn.trials: list[IpdTrialEntry] = Field(min_length=2)` enforces ≥2 trials. `IpdTrialEntry.rows_csv` requires ≥10 chars (rules out empty pastes). `IpdMainResults.per_trial: list[IpdPerTrialEffect] = Field(min_length=2)`. `IpdPooledEffect.i_squared` clamped to [0, 100]; `tau_squared >= 0`.
- **Anti-hallucination posture** — per-trial estimates + pooled effects + I² + τ² + interaction p come from `run_ipd_analysis` sandbox runs only; trial ids in `studies_included` must appear in the bundle; system prompt's ABSOLUTE RULES mandate dropping (not fabricating) trials the sandbox skips.
- **One-stage vs two-stage as the methodological diagnostic** — both stages ALWAYS run when feasible; large discrepancy (>0.2 in log-OR for binary; >0.1 × SD for continuous) signals model misspecification, surfaced in `discrepancy_note`. When one stage fails (small trials, sparse events), the other still runs and the failure is reported in `discrepancy_note` rather than imputed.
- **Landscape PDF + DOCX report** — side-by-side one-stage / two-stage table with effect + 95% CI + p + n + I² + τ² + method; per-trial estimates table; per-subgroup interaction tables with per-level pooled effects + joint Wald interaction p; interpretation + caveats.
- **`skill.ipd` RBAC permission** in `_EVIDENCE_SKILLS` — researcher + admin only; student blocked.
- **Dispatcher routes IPD BEFORE meta_analysis** — "IPD MA on statins" goes to IPD, not aggregate pairwise. "Compare 5 DOACs" still routes to NMA (the multi-arm `compare\s+([3-9]|\d{2,})` regex catches it first).
- **Inline CSV ingest, not SourceDocument linkage.** The bundle stage accepts CSV pastes directly (like `meta_analysis` accepts study-data JSON pastes). The SourceDocument substrate from P1 #5 remains the option for operators who want a persistent audit-chain; the IPD specialist is conversation-context-bounded. Operators with very large per-trial files would upload via SourceDocument and reference rows; this slice ships the inline path as the simplest substrate.

Limitations / deferred:
  - Random-effects logistic (strict, not fixed-effects approximation) needs a PyMC backend — deferred to a future sandbox-image rebuild.
  - Subgroup-binary and subgroup-TTE interaction p-values: per-level pooled effects work, but the joint Wald interaction p is computed only for continuous endpoints (MixedLM); binary/TTE versions need analogous joint p logic. Documented in the system prompt.
  - Multi-arm trials (>2 arms) within IPD: current code treats them as binary (active vs everything else); proper handling needs per-arm interaction term expansion.
  - No forest-plot PNG yet (would mirror trial_stats's matplotlib pattern); a future visualisation slice could add one. The PDF report currently shows the per-trial estimates as a table.

Same architecture as the previous synthesis-tier specialists (meta_analysis, NMA): sandbox-backed analysis primitives + tool wrapper + workflow specialist + PDF/DOCX report + RBAC permission.

### Cross-cutting

#### ~~Citation-manager integration~~ · ✅ shipped 2026-05-31 · S

Shipped as a pure file-format round-trip — covers Zotero / EndNote / Mendeley universally because every citation manager imports/exports the two canonical formats this slice ships:

- **`services/citations.py`** — pure-stdlib parsers + exporters for BibTeX (.bib) and RIS (.ris). Canonical `Citation` dataclass round-trips through both formats. `detect_format(text)` sniffs the format from the first 20 lines.
- **Two endpoints under `/api/citations`**:
  - `POST /parse` — multipart upload (5 MB cap); auto-detects format or accepts an explicit `format=bibtex|ris` form field; returns `{format_detected, count, citations: [...]}`.
  - `POST /export` — JSON body `{format, citations}`; returns a downloadable `.bib` or `.ris` file with the right content-type (`application/x-bibtex` / `application/x-research-info-systems`).
- **`import_citations(format, content)` tool** registered on `manuscript_drafter` and `sr_protocol` specialists — the agent can ingest a pasted BibTeX/RIS dump mid-draft and the parsed list becomes available for inclusion in the next turn's references. `format="auto"` sniffs.
- **Frontend `<CitationImporter>` component** in `index.html` — drop-zone + "choose file" button embedded in the `ManuscriptIntakeCard` (manuscript drafting) and `ProtocolMethodsCard` (sr_protocol). On drop, POSTs to `/api/citations/parse` and inlines the parsed list into the next chat message so the agent sees it.
- **No new RBAC permission** — both specialists that consume the tool are already researcher-tier; the endpoints inherit the top-level auth dependency.
- **No new dependency** — pure stdlib (`re`, `dataclasses`); no `bibtexparser` / `pyzotero` / `rispy` dep.
- **Format coverage**: BibTeX entry types article / book / incollection / inproceedings / techreport / phdthesis / misc round-trip through `_BIBTEX_TYPE_BY_RIS_TY` / `_RIS_TY_BY_BIBTEX_TYPE` maps. Unknown BibTeX fields land in `raw_fields` (preserved through the export). RIS `SP`/`EP` collapse into BibTeX `pages` and back. PubMed IDs detected from RIS `AN` / `ID` / `PM` tags (when digit-only, 4–10 chars).
- **OAuth Zotero web-API integration deferred** — per the scoping pick. The file-format round-trip is universal and avoids the API-key UI work; adding `pyzotero` later is additive.

#### ~~Portfolio dashboard across reviews + trials~~ · ✅ shipped 2026-05-31 · M

Shipped as a focused per-user dashboard with an admin-only org rollup, no new persistence — the endpoints aggregate existing rows across the research and clinical stores:

- **5 endpoints under `/api/portfolio`**:
  - `GET /threads` — per-user thread rollup. Each row: `{id, title, workflow, last_turn_kind, message_count, last_activity}`. `last_turn_kind` peeks at the most recent assistant `Message.final_answer` JSON for the `kind` discriminator so the dashboard can show "stuck at PICO confirmation" vs "produced sof_table" without re-rendering the whole turn.
  - `GET /sr-projects` — owned `SrReview` rows + projects the caller holds a `sr_review`-scoped `RoleAssignment` for (reviewer_1 / reviewer_2 / adjudicator). Per-row screening counts: `n_candidates / n_included / n_excluded / n_pending` computed from `SrCandidate.current_status`.
  - `GET /deployments` — eCRF deployments visible per the caller's `RoleAssignment` set: global-admin sees all; study-scoped see matching `research_study_id`; site-scoped resolve through `Site → Deployment`. Researcher-only callers see an empty list (deliberate — eCRF is a clinical-operations surface, not a researcher artefact). Each row: `{id, name, status, is_locked, n_subjects, last_activity}`. `is_locked` is computed from an active `StudyLock` row (`unlocked_at IS NULL`).
  - `GET /summary` — aggregated counts for the dashboard hero cards: `{threads_by_workflow, threads_total, sr_projects_total, deployments_total}`.
  - `GET /org` — **admin-only org rollup** gated by the new `portfolio.read_org` permission. Returns per-user totals (`thread_count, sr_project_count, last_activity, role_summary`) + org-wide aggregates. **No row-level data** leaked — admins see counts and roles, not other users' thread bodies or project content.
- **1 new RBAC permission**: `Permission.PORTFOLIO_READ_ORG`. Admin only (admin's `_ALL_PERMS` membership gives it automatically); every other role excluded. Researcher sees their own portfolio via the per-user routes, which need no new permission (the routes inherit the top-level auth dependency).
- **No new persistence layer** — the rollups are read-side aggregations over existing repositories (`ThreadRepository`, `SrReview`, `StudyDeployment`, `RoleAssignment`). Counts are computed in-Python rather than via SQL aggregate functions for portability across SQLite (test fixtures) and Postgres (runtime).
- **`/portfolio.html` page** — vanilla JS + fetch (no React this slice). Hero cards (Threads / SR projects / Deployments / Top workflow), then per-section tables. Org rollup section renders only when `/api/portfolio/org` returns 200 (admin-only); silently hidden otherwise. Sidebar link added to `index.html` for navigation.
- **Mirror-the-pattern across stores** — the helper `_visible_deployments(owner_id)` is called by both `/deployments` and `/summary` so the visibility logic stays in one place. Same posture as how `web/threads.py` uses `resolve_local_user_id` across endpoints.
- **PHI-safe**: deployments + SR projects are surfaced by name + count only; the dashboard doesn't expose subject identifiers / patient-level data. eCRF subject counts are aggregate (`COUNT(*) WHERE deployment_id = ?`).

#### Dev ergonomics: bind-mount frontend assets in compose · 💡 · S

`deploy/compose/docker-compose.yml`'s `agent` service builds from a Dockerfile that `COPY`s `src/` at image-build time — frontend edits (`index.html`, `collector.html`, etc.) only surface after `docker compose build agent && up -d agent`. Adding a bind-mount for `./src/research_assistant/web/static → /app/src/research_assistant/web/static` would let HTML/JSX edits hot-load on the next browser refresh without a rebuild. Pure dev-ergonomics — does not affect production deploy posture.

---

## P2 — Polish on existing workflows

#### Group-level living-review subscriptions · 📝 · M

In the feature-guide roadmap. Group watches with quorum-based notification rules — designed for guideline committees and HTA bodies who need consensus signalling on practice-changing evidence rather than per-user alerts.

#### ~~Patient-facing / lay summaries~~ · ✅ shipped 2026-05-31 · M

Shipped as the third P2 — closes the dissemination gap between the regulator-facing artefacts (CSR, IRB packet, manuscript) and the patient-facing surface (recruitment posters, shared-decision aids, return-of-results letters). Brand-new `lay_summary` specialist + host-side readability service.

- **New `lay_summary` specialist** (`agent/specialists/lay_summary.py`) with 3-stage workflow (intake → draft → document). Three intake variants discriminated by `kind`: `recruitment_intake` (protocol synopsis), `evidence_intake` (meta-analysis), `results_intake` (CSR / trial_stats). The specialist composes with `irb_drafter` (recruitment), `meta_analysis` (evidence), and `csr_drafter` / `trial_stats` (results).
- **Host-side Flesch-Kincaid grade computation** in `services/readability.py` (stdlib only, no `textstat` dep). Vowel-group syllable estimator with the standard silent-e + le-after-consonant rules. The model may estimate but the document's `grade_actual` is overwritten with the host's number before persistence — eliminates the irb_drafter weakness where the model self-reported its own grade.
- **Compute + report + iterate posture (the user-chosen scoping option)**. When a first draft scores > 8.0 grade, the specialist re-prompts with "Reduce reading level. The previous draft scored too high…" and ships the better of the two passes. `LaySummaryDocument.readability_attempts` records 1, 2, or 3 (capped) so the operator sees how hard the system worked to land under target. A chronically-over-target document still ships with the actual grade visible, not falsified.
- **AudienceProfile schema** (`target_grade` 4-12, `language` en/es/fr/de, free-text `region`, `population_descriptor`). Same multilingual posture as `irb_drafter`. `region` lets the same source generate a Spanish-Mexico draft distinct from Spanish-Spain — drives idiom + metric/imperial choice. Default target_grade = 6 (CISCRP general-adult-patient target).
- **5 plain-language sections in CISCRP / NIH order**: `what_this_is_about` → `what_we_did` → `what_we_found` → `what_this_means_for_you` → `next_steps`. The `section_id` Literal forces the order schema-side. Optional plain-language glossary as a list of `{"term": ..., "gloss": ...}` entries rendered as a sidebar on the report.
- **Anti-hallucination posture**:
  - Evidence variant: every PMID cited must be in `pmid_sources` (operator-pasted). Same posture as `meta_analysis` / `manuscript_drafter`.
  - Results variant: every numeric claim must trace to a `derived_from_ids` entry (operator-pasted). Same posture as `csr_drafter` / `trial_stats`.
  - Recruitment variant: never promise the trial will succeed; only describe what's being tested.
  - All variants: no medical-decision language ("you should", "you must"); only "may help" / "might give doctors more information" framings.
- **Reports package** — new `reports/lay_summary.py` with `assemble_report_data` + `build_pdf` + `build_docx`. Cover band shows source label + language + region + reading-grade badge (green within target+1, amber over). Footer is "not medical advice" disclaimer. Downloadable at `/api/threads/{thread_id}/report/lay_summary/{pdf|docx}`.
- **Dispatcher routing** — keyword triggers checked BEFORE `manuscript_drafter` so "draft a lay summary" doesn't get grabbed by "draft a manuscript". 5 slash commands (`/lay`, `/lay-summary`, `/plain-language`, `/pls`, `/patient-summary`). 10 continuation prefixes including 3 handoff seeds (`Draft lay summary from meta-analysis|CSR|IRB packet`).
- **RBAC** — new `Permission.SKILL_LAY_SUMMARY` (researcher-tier, student blocked, admin gets it via `_ALL_PERMS`). Same evidence-skills set as the other dissemination-tier drafters (manuscript / GRADE).
- **Frontend** — new tile in the "Analysis & reporting" section of the welcome page. New `handoffToLaySummary` function + button on the `MetaAnalysisCard` (evidence variant); the operator pastes the meta-analysis JSON via the handoff seed and the lay summary cites the underlying PMIDs verbatim.
- **30+ new tests** in 4 files: `test_readability.py` (FK math + syllable rules + edge cases), `test_lay_summary_domain.py` (3 intake variants + AudienceProfile + draft 5-section rule + document round-trip + readability_attempts cap), `test_dispatcher_lay_summary.py` (slash commands + keyword triggers + continuations + "draft a manuscript" stays in manuscript_drafter), `test_lay_summary_report.py` (assembler + PDF + DOCX signature + over-target round-trip + glossary). 1345 tests pass.
- **What's deferred**:
  - **Multi-language Flesch-Kincaid.** The FK formula was built for English; Spanish / French / German need analogue formulas (Fernández-Huerta for Spanish, Kandel-Moles for French, LIX for German). Today the host computes FK regardless of the language tag; the specialist's system prompt still nudges the model toward short words. Add per-language formulas when a real ES/FR/DE pilot lands.
  - **PDF cover art / illustrations.** The current PDF is text-only. CISCRP-style patient-facing materials usually carry an illustration band; deferred until a sponsor asks.
  - **EMA Reg (EU) No 536/2014 templated lay summary.** The EMA lay-summary obligations have a specific 10-section structure. The results variant's 5 sections cover the spirit but not the letter. Add an EMA-specific export when an EU trial lands.
  - **Reading-level enforcement loop > 2 passes.** Cap is intentionally 3; chronically high-grade content usually means the source material itself is jargon-heavy, and retrying further wastes tokens. Raise the cap if a quality study contradicts that assumption.
  - **Cross-handoff into `irb_drafter`.** Today the lay summary references a protocol synopsis but doesn't draft an ICF supplement. The pieces are adjacent — a single click could compose both.

#### ~~Drug accountability~~ · ✅ shipped 2026-05-31 · M

Shipped as the second P2 — closes the GCP-mandated IP-tracking gap that the eCRF subsystem otherwise leaves open. Builds entirely on the ClinicalBase + repo + endpoint pattern established in E1–E8.

- **4 new ClinicalBase tables** in `persistence/clinical/models.py`:
  - `InvestigationalProduct` — per-deployment catalogue (drug + strength + units + optional kit_id pattern). UNIQUE on (deployment_id, drug_name, strength) so the same lot of "DrugX 10 mg" can't be registered twice.
  - `DrugReceipt` — shipments arriving at a site or central depot. Carries `lot_number`, `expiry_date`, `temp_excursion_flag` (cold-chain breaks surface on the data-manager triage queue via the existing audit + notes path), and `packing_slip_ref` for source-data traceability.
  - `DrugDispensation` — kit-to-subject events. Optional `planned_visit_id` ties dispenses to the P1 #4 visit calendar when the calendar is in use. `kit_id` keys per-kit reconciliation.
  - `DrugReturn` — return events. Carries `quantity_returned` (what came back), `quantity_used` (consumed compliance count), `quantity_lost` (missing). The remainder (returned − used − lost) re-enters lot inventory.
- **Repository state invariants** in `persistence/clinical/repository.py` — every invariant raises `ClinicalError` at the repository layer (not the API), so direct repo callers can't bypass them:
  - dispense > current lot inventory → reject (`"only N tablet(s) of lot LOT-X in inventory"`)
  - cross-deployment subject vs IP → reject
  - return without prior dispensation → reject (FK + lookup)
  - `quantity_used + quantity_lost > quantity_returned` → reject
  - `quantity_returned > quantity_dispensed` → reject
  - negative quantities → reject
  - invalid return_reason → reject (allowed set: end_of_visit / end_of_treatment / early_termination / adverse_event / other)
- **Per-lot reconciliation rollup** computed in Python (portable across SQLite-for-tests + Postgres-at-runtime): keys `f"{ip_id}|{lot_number}"` → `{received, dispensed, returned, used, lost, current_inventory}`. `current_inventory = received + (returned − used − lost) − dispensed`. Top-level `totals` aggregates across all lots.
- **9 new endpoints** under `/api/edc/`:
  - `POST/GET /deployments/{deployment_id}/ip-catalogue` — register + list IPs
  - `POST/GET /deployments/{deployment_id}/drug-receipts` — log + list receipts (with `ip_id` + `lot_number` query filters)
  - `POST/GET /deployments/{deployment_id}/drug-dispensations` — log + list dispenses
  - `POST /drug-dispensations/{dispensation_id}/return` — log a return against a prior dispense
  - `GET /deployments/{deployment_id}/drug-returns` — list returns
  - `GET /deployments/{deployment_id}/drug-reconciliation` — per-lot rollup, gated on `ip.reconcile`
- **5 new permissions** in the RBAC matrix (`auth/rbac.py` + `rbac-design.md` §4.4):
  - `ip.catalogue` — study_designer + data_manager (design-time + mid-study additions)
  - `ip.receive` — coordinator (point-of-care) + data_manager (central depot)
  - `ip.dispense` — coordinator (POC) + PI (co-signature at sites that require it)
  - `ip.return` — coordinator only (logged at end-of-visit)
  - `ip.reconcile` — data_manager + monitor + auditor + PI (read-side; mutating roles excluded so coordinators don't see the cross-site rollup)
- **Lock-aware writes** — every mutating endpoint calls `_require_deployment_unlocked` so the IP catalogue + receipts + dispenses + returns are all refused with 409 once the study is locked (E7 hardening). The return endpoint walks `dispensation_id → deployment_id` first to resolve the lock check.
- **Resolvers added to `web/authz.py`**: `resolve_ip_scope` (InvestigationalProduct → deployment scope) + `resolve_dispensation_scope` (DrugDispensation → subject scope, so site-scoped coordinators only return kits at their own site).
- **Collector UI panel** — `web/static/collector.html` gains a "Drug accountability" card alongside Source extraction + Recruitment + Visit calendar. Loads the IP catalogue + reconciliation rollup, shows top 5 IPs + top 6 lots with stock / dispensed / returned counts, lets the designer register new IPs inline. The reconciliation block degrades gracefully when the viewer lacks `ip.reconcile`.
- **30 new tests** in `tests/unit/test_drug_accountability.py` cover the catalogue + receipts + dispensations + returns CRUD, every state invariant via `pytest.raises(ClinicalError, match=...)`, the inventory accounting (received + returned-remainder − dispensed), the per-lot reconciliation rollup (two lots, multi-event), the empty-deployment edge case, and the RBAC matrix per role.
- **What's deferred**:
  - `kit_id` pattern enforcement (catalogue carries the regex; not enforced yet)
  - Per-subject compliance metric (used / dispensed × 100) — straightforward extension of the rollup, deferred until a sponsor asks
  - Temperature-excursion CAPA auto-link — `temp_excursion_flag` is logged but not yet wired to the protocol-deviation workflow
  - IRT integration for dispensation — kits are operator-selected today; randomisation-driven kit selection lives in the IRT subsystem (E8) but the join is not yet automatic

#### Lab-data feeds (HL7 / CDISC LAB) · 💡 · L

Central labs deliver via these standards. The eCRF re-keys lab values today. Worth doing once SDTM mapping is mature (the data shape converges).

#### Multi-site / multi-tenant coordination rollup · 💡 · M

Site-level aggregations are partial today; central-coordinator view of multi-site enrolment, query backlog, and per-site monitor visit status would close it.

#### ~~Budget + cost rollup across studies~~ · ✅ shipped 2026-05-31 · S

Shipped as the first P2. Builds on the portfolio dashboard (P1 #9) and the existing done-event token tracking — no new persistence:

- **`config/bedrock_pricing.py`** — per-model USD-per-1K-token table covering Claude 4.x (Haiku 4.5, Sonnet 4.6, Opus 4.7) + legacy Claude 3 family. `lookup(model_id)` normalises both short ids ("claude-haiku-4-5") and full Bedrock inference-profile ids ("us.anthropic.claude-haiku-4-5-20251001-v1:0") to the same pricing row. Unknown / future model ids fall back to Sonnet 4.6 (conservative overestimate).
- **`services/cost_rollup.py`** — three async functions: `rollup_for_user(session, user_id)` aggregates per-workflow + per-model-family + monthly + cumulative; `rollup_for_thread(session, thread_id)` returns the per-thread total; `rollup_for_org(session)` admin-only org-wide rollup + per-user breakdowns. All read-side aggregations over the existing `done` `StreamEvent`s — no new tables.
- **`web/dispatch.py`** — done event's `usage` dict now carries the `model_id` setting at write time. Legacy events (no model_id) fall back to the default pricing at read time. Forward-looking cost attribution is per-model; backward-looking attribution is best-effort.
- **Two new endpoints**:
  - `GET /api/portfolio/costs` — per-user cost rollup. Inherits the top-level auth dependency; no new permission. Returns `{usd_total, usd_this_month, input_tokens, output_tokens, by_workflow, by_model_family, n_turns}`.
  - `GET /api/portfolio/org/costs` — admin-only, gated by the existing `portfolio.read_org` permission (from P1 #9). Returns org totals + per-user rollups sorted by spend descending.
- **`PortfolioThreadOut` extended with `cost_usd`** — each thread row on `/api/portfolio/threads` now carries its cumulative cost. Lets the dashboard show which threads are the spend hot-spots.
- **`portfolio.html` extended with**:
  - "Spend (USD)" section with This-month / Cumulative / Turns / Tokens cards + by-workflow + by-model-family breakdowns
  - "Cost" column on the threads table
  - Admin-only "Top-spend users" section that surfaces the per-user rollups from `/org/costs`
- **No new RBAC permission** — `portfolio.read_org` from the P1 #9 portfolio dashboard already covers the admin org cost view. Per-user routes use the top-level auth dependency.
- **Pricing-table provenance documented in the UI** ("AWS Bedrock public rates as of 2026-05. Verify before invoicing."). Operators relying on these numbers for sponsor billing should re-verify against the current AWS pricing page.
- **Six-decimal rounding** in the `CostRollup.as_dict()` shape — enough for fractions of a cent without showing IEEE-754 float noise like `0.30000000000000004`.

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
