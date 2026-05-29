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
| Synthesis | SR title/abstract + full-text screening UI | **P0** | 💡 proposed | M |
| Synthesis | PRISMA flow diagram generation | **P0** | 💡 proposed | S |
| Design | Sample-size + power calculator | **P0** | 💡 proposed | S |
| Design | Statistical Analysis Plan (SAP) drafter | **P0** | 💡 proposed | M |
| Start-up | ClinicalTrials.gov / EU CTR registration drafter | **P0** | 💡 proposed | M |
| Start-up | IRB / ethics submission packet + ICF drafter | **P0** | 💡 proposed | L |
| Execution | Randomisation / IRT service | **P0** | 💡 proposed | M |
| Execution | AE / SAE workflow (detect, code, escalate) | **P0** | 💡 proposed | M |
| Execution | eCRF formal validation pack (CSV / IQ-OQ-PQ) | **P0** | 📝 planned (feature-guide) | L |
| Analysis | CDISC SDTM mapping → ADaM → TLF | **P0** | 💡 proposed | XL |
| Analysis | CSR (ICH E3) drafter | **P0** | 💡 proposed | XL |
| Execution | Protocol-deviation tracking + CAPA | **P1** | 💡 proposed | M |
| Execution | Recruitment / screening logs | **P1** | 💡 proposed | S |
| Execution | Visit scheduling + participant reminders | **P1** | 💡 proposed | M |
| Execution | Source-document extraction (EHR → eCRF / extraction table) | **P1** | 📝 planned (feature-guide) | L |
| Analysis | Manuscript drafter (IMRaD) + reviewer-response loop | **P1** | 💡 proposed | L |
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

### eCRF formal validation pack (CSV / IQ-OQ-PQ) · **P0** · 📝

- Mentioned in the feature guide's "Deepening eCRF compliance" roadmap entry.
- **Effort:** L
- **Why P0:** Closes the gap between "built to Part 11 conventions" and "deployable in an audited environment". Includes full password re-authentication at signing, study-level lock (not just subject-level), and the IQ/OQ/PQ documented pack.
- **Dependencies:** RBAC.

---

## P0 — Blockers

Items here gate concrete customer segments. Ordered roughly by sequencing logic (some items unblock others).

### Synthesis

#### SR title/abstract + full-text screening UI · 💡 · M

Search + extraction + meta are best-in-class today, but the **screening loop** in between — 3–8k abstracts, two reviewers, conflict resolution — is missing. Real teams currently bounce out to Rayyan or Covidence and back. This is the single biggest seam in the SR pipeline.

- **Dependencies:** RBAC (for Reviewer-1 vs Reviewer-2 vs Adjudicator roles).
- **Unlocks:** PRISMA flow diagram as a free byproduct; team-based SRs; closes the obvious omission for any research office that does ≥3 SRs/year.

#### PRISMA flow diagram generation · 💡 · S

Every SR submission requires one. We already have every input (per-source search yields, dedupe count, included/excluded counts, screening reasons). A leaf-node feature once screening UI lands.

- **Dependencies:** SR screening UI (for the included/excluded counts at title-abstract and full-text stages).

### Design (upstream of protocol)

#### Sample-size + power calculator · 💡 · S

Without this there is no grant submission and no ethics package for prospective work. **Lowest-effort P0 in this document** — `sandbox_exec` already has `pwr`, `statsmodels`, and `scipy` pinned. Worth pairing with the SAP drafter (below).

- **Dependencies:** none.
- **Unlocks:** end-to-end design loop for prospective trials.

#### Statistical Analysis Plan (SAP) drafter · 💡 · M

Distinct artefact from PRISMA-P. For prospective trials this is its own ICH-E9-shaped document: analysis populations (ITT / mITT / PP / Safety), handling of missing data, multiplicity adjustments, interim-analysis stopping rules, sensitivity analyses. Natural fit as a new specialist or as an extension of `sr_protocol`.

- **Dependencies:** sample-size calculator (the SAP references the powering assumptions).
- **Unlocks:** prospective trial design loop; statistician-grade output.

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

#### AE / SAE workflow on top of eCRF · 💡 · M

AE capture exists in eCRFs; the **workflow** doesn't. Components:

- SAE detection rules (grade ≥3, hospitalisation, death, …)
- 24-hour reporting timer with sponsor inbox escalation
- MedDRA preferred-term coding (requires MedDRA license at deploy)
- IND safety report drafting (FDA 3500A shape)
- SUSAR (Suspected Unexpected Serious Adverse Reaction) handling

Compliance-critical for trial use; absent today.

- **Dependencies:** none hard; MedDRA license at deploy time.
- **Unlocks:** trial compliance posture; IND / CTA submissions.

### Analysis / Reporting

#### CDISC SDTM mapping → ADaM derivation → TLF generation · 💡 · XL

The eCRF subsystem produces high-quality **research-grade** extracts (ODM-XML, JSON). SDTM domain mapping (DM, AE, VS, LB, EX, …) and ADaM derivation (ADSL, ADTTE) are what **regulators consume**. Tables/Listings/Figures (TLF) live on top of ADaM.

Without these, eCRF data cannot reach a regulatory submission — capping the trial use case at "we collected the data" instead of "we submitted the trial".

- **Effort:** XL — multi-quarter, ongoing. Likely incremental: SDTM first, then ADaM, then TLF.
- **Dependencies:** eCRF ✅ shipped.
- **Open question:** OSS mapping library (e.g. `pinnacle`, `OAK`) vs build internally? Material effort difference.
- **Unlocks:** regulator-grade submissions; sponsor / CRO adoption; HTA dossiers downstream.

#### Clinical Study Report (CSR, ICH E3) drafter · 💡 · XL

A real CSR is 80–150 TLF artefacts plus narrative. The per-workflow report engine (meta-analysis · protocol · RoB, all ✅ shipped) is the scaffolding; CSR composition is the natural capstone of the trial workflow.

- **Dependencies:** SDTM/ADaM (above). Without ADaM, CSR has no analysis data to narrate.
- **Sequencing:** start the manuscript drafter (P1, below) first — it shares 80% of the composition mechanics and is useful on its own.

---

## P1 — High-leverage structural gaps

### Execution

#### Protocol-deviation tracking + CAPA · 💡 · M

A regulator's first ask at inspection. Per-subject log with classification (major / minor / critical), root cause, Corrective and Preventive Action (CAPA) workflow. The eCRF audit trail records *what changed*; deviations are a higher-level classification on top.

#### Recruitment / screening logs · 💡 · S

Per-site, per-day enrolment numbers; ineligibility reason coding; recruitment-funnel attrition. Operational hygiene PIs ask for weekly. Low effort; lives naturally next to the eCRF subject roster.

#### Visit scheduling + participant reminders · 💡 · M

Today the visit schedule is part of the form definition, but there is no calendar surface for coordinators and no SMS/email reminder to participants. **ePRO compliance dies without reminders** — this is what makes the difference between 95% and 60% ePRO completion.

#### Source-document extraction (EHR → extraction table / eCRF pre-fill) · 📝 · L

Already on the feature-guide roadmap. Point the assistant at structured exports from EHR / registry / TMS and have it populate the extraction table (or pre-fill eCRF instances) for retrospective studies or patient-level meta-analyses, with an audit trail of which source row produced which output cell.

- **Dependencies:** RBAC (PHI-bearing).
- **Adjacency:** opens RWE work if generalised to FHIR (P3, below).

### Analysis / Reporting

#### Manuscript drafter (IMRaD) + reviewer-response loop · 💡 · L

Full Introduction / Methods / Results / Discussion composition with reference management. You already have building blocks (background paragraphs from `sr_protocol`, results from `meta_analysis`); a manuscript specialist that composes them into a journal-shaped artefact is the missing seam.

The **reviewer-response generator** is a high-value follow-on — peer review is the most iterative, LLM-natural part of the cycle and nobody else does it well today.

- **Sequencing:** lands well before CSR; shares 80% of the composition primitives.

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
| 1 | **RBAC implementation** | Unblocks SR screening UI, randomisation, source-doc extraction, and every multi-user workflow. Highest dependency-fan-out item in this document. |
| 2 | **SR screening UI + PRISMA flow diagram** | Single biggest seam in the existing SR pipeline. PRISMA diagram is a free byproduct. RBAC must land first or be stubbed. |
| 3 | **Sample-size + SAP drafter (paired)** | Cheapest P0 in this doc (sample-size is S effort). Unlocks the prospective-trial design loop end-to-end with the existing `sr_protocol` specialist. |
| 4 | **AE/SAE workflow + protocol-deviation tracking (paired)** | Compliance-critical eCRF v2 features; lifts eCRF subsystem from "research-grade" to "trial-grade". No new architecture; bolts onto existing capture. |
| 5 | **Manuscript drafter (IMRaD) + reviewer-response loop** | Composes existing per-workflow reports into journal-shaped artefacts. 80% of the primitives carry forward into CSR. Reviewer-response is a high-delight feature with low marginal cost. |
| 6 | **CDISC SDTM mapping (start the multi-quarter build)** | Largest effort item; needs to start now so it lands when CSR begins to demand it. Choose OSS library vs build during the design pass. |

Beyond these six, sequencing flexes with customer pull.

---

## Open questions for product owner

1. **Feature-guide "On the roadmap" section** — keep it as a short customer-facing teaser of P0 items, or remove it entirely now that the full list lives here? (Recommendation: keep, but trim to the top-3 P0 items + a pointer to this document.)
2. **RBAC vs SR screening sequencing** — SR screening *needs* RBAC for the two-reviewer model. Two options:
   - (a) RBAC first (delays screening start by ~1 quarter)
   - (b) screening built with placeholder two-user model, RBAC retrofitted later (faster MVP, ~1 week of rework when RBAC lands).
3. **SDTM mapping** — adopt an existing OSS mapping library (e.g. `pinnacle`, `OAK`) or build internally? Material effort difference; affects sequencing of CSR.
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
