# Demo Guide — Clinical Research Assistant

Presenter playbook + functional-test checklist. Organised around the **six clinical-research lifecycle phases** so a demo can walk a researcher / sponsor / institution through their actual workflow — pick the phase that matches your audience and run the demos in order, or cherry-pick individual demos for a focused conversation.

Every paste target lives in a fenced code block — click the copy icon, drop it into the chat input, move on. Use this same guide for end-to-end functional testing: each demo carries **sanity checks** that double as an acceptance test, plus **failure modes** that flag what to look for.

---

## Table of contents

- [Setup (do once before any demo)](#setup-do-once-before-any-demo)
- [Phase 01 — Evidence synthesis](#phase-01--evidence-synthesis) (8 demos)
- [Phase 02 — Trial design](#phase-02--trial-design) (1 demo)
- [Phase 03 — Start-up](#phase-03--start-up) (3 demos)
- [Phase 04 — Execution](#phase-04--execution) (14 demos, X0–X13)
- [Phase 05 — Analysis & reporting](#phase-05--analysis--reporting) (7 demos)
- [Phase 06 — Cross-cutting infrastructure](#phase-06--cross-cutting-infrastructure) (5 demos)
- [Appendix — All paste blocks (cheat sheet)](#appendix--all-paste-blocks-cheat-sheet)

> **Naming.** Each demo is identified by `<Phase letter><number>` — `E*` for Evidence synthesis, `D*` for trial Design, `S*` for Start-up, `X*` for eXecution, `A*` for Analysis & reporting, `C*` for Cross-cutting. Lets a reviewer reference "X4 AE/SAE" without ambiguity.

---

## Setup (do once before any demo)

The app runs as a Docker Compose stack (FastAPI `agent` + Postgres + clinical-Postgres). Plain `uv run` is for tests / tooling only — the runtime path is compose (per CLAUDE.md).

### Prerequisites (one-time)

- Docker Desktop running.
- Sandbox image built (separate from the agent image):

  ```
  docker build -t research-assistant-sandbox:latest ./sandbox
  ```

- `.env` populated with AWS / Tavily / NCBI keys and `HOST_SANDBOX_WORK_DIR` set to the absolute host path of `./sandbox-work`:

  ```
  cp deploy/compose/.env.example deploy/compose/.env
  ```

### Clean restart (recommended before any demo)

Brings every service down (so no stale in-memory scheduler / cached config / orphaned thread state survives), then back up detached:

```
docker compose -f deploy/compose/docker-compose.yml down
docker compose -f deploy/compose/docker-compose.yml up -d
```

### Tail the agent logs in a side terminal

Run this in a second terminal — it streams `sandbox_exec` calls, `search_papers` calls, the `_require_sandbox_for_results` retry warning, and any errors live:

```
docker compose -f deploy/compose/docker-compose.yml logs -f agent
```

On startup you should see in the log stream:

```
INFO  Application startup complete.
INFO  Uvicorn running on http://0.0.0.0:8000
INFO  Scheduler started
INFO  Scheduler re-registered N active watch(es) from DB
```

### If you changed code since last demo

The Dockerfile `COPY`s `src/` at build time — there's no host bind-mount and no `--reload`. So a plain restart of the agent container would run the *old* code. After any code change, rebuild + recreate the `agent` container atomically (Postgres untouched, data preserved, ≈ 1 minute mostly cached):

```
docker compose -f deploy/compose/docker-compose.yml up -d --build agent
```

### Open the app

In the browser at `http://localhost:8000/`, do a **hard-refresh** (`Ctrl+F5`) so the sidebar bell, watches link, multisite link, portfolio link, and the latest specialist cards load. Log in via Cognito if prompted.

### Surface map (all the URLs you'll touch)

```
http://localhost:8000/                — main app (chat workflows)
http://localhost:8000/admin.html      — paper-source admin · role admin · validation-pack
http://localhost:8000/sr.html         — SR screening (Phase 01)
http://localhost:8000/watches.html    — watches + group subscriptions (Phase 01)
http://localhost:8000/ecrf.html       — Form Builder (Phase 04, admin)
http://localhost:8000/collector.html  — Site EDC + safety + recruitment + visit + drug + labs (Phase 04)
http://localhost:8000/epro.html?token=…  — Participant ePRO (Phase 04, magic-link)
http://localhost:8000/multisite.html  — Central-coordinator multi-site rollup (Phase 04)
http://localhost:8000/portfolio.html  — Portfolio + budget dashboard (Phase 06)
http://localhost:8000/library.html    — Library + RAG (Phase 06)
```

### Pre-seed data for Phase-04 + Phase-05 demos (the "Smoke deployment")

Almost every Execution-phase + Analysis-phase demo wants an existing deployment with a site + at least one subject. Run **X1 → X2** once first to produce the SMOKE-T2DM-001 study + Smoke deployment + Site 01 + S01-001 subject. Subsequent Execution + Analysis demos assume that exists.

### AWS quota note

The watches demo cadence (`*/2 * * * *`) issues ~30 Bedrock calls/hour. Switch any demo watches to a weekly schedule when you're done, or delete them — otherwise they'll keep firing.

---

## Phase 01 — Evidence synthesis

> **Phase context.** Before any new study runs, the team needs to know what the existing evidence base looks like — for the protocol's background section, for the IRB packet's "what's known", for the grant application's justification of need, and for guideline + HTA submissions whose entire artefact is a synthesis.

### E1 — Meta-analysis on synthetic data (≈ 5 min)

**Demos.** End-to-end meta-analysis with anti-hallucination guardrails: PICO → search → extraction → forest plot, all inside the sandbox. Most reliable demo path because the data is deterministic.

**Goal.** 8-row forest plot, pooled RR ≈ **0.44** (95% CI ~ 0.33–0.58), I² in the **30–50%** range.

> The numbers below are **fabricated** to give a plausible meta-analysis result. Author names are made up so this can never be confused with real evidence.

#### Steps

**1. New conversation. Paste the canonical research question:**

```
Does adding a proton pump inhibitor to dual antiplatelet therapy reduce upper GI bleeding in patients who recently underwent PCI for acute coronary syndrome?
```

**2. At the PICO confirmation step, paste:**

```markdown
| Element | Value |
|---|---|
| Population | Adults post-PCI for ACS, on dual antiplatelet therapy (aspirin + P2Y12 inhibitor) |
| Intervention | PPI added to DAPT (any PPI, any dose, ≥30 days) |
| Comparator | DAPT alone or DAPT + placebo |
| Outcome (primary) | Upper GI bleeding events during follow-up |
| Study design | Randomized controlled trials |
```

**3. Skip / fast-forward the search step.** (You can let it run on real PubMed — the synthetic studies won't be there, that's fine.)

**4. At the extraction step, paste:**

```markdown
| Study | Year | Design | PPI events | PPI total | Control events | Control total | Follow-up (mo) |
|---|---|---|---|---|---|---|---|
| Anderson et al. | 2014 | Multicenter RCT | 12 | 1200 | 28 | 1198 | 12 |
| Bekele et al. | 2016 | Single-center RCT | 4 | 320 | 9 | 318 | 6 |
| Chen et al. | 2018 | Multicenter RCT | 18 | 2200 | 41 | 2180 | 12 |
| Diaz et al. | 2019 | Pragmatic RCT | 7 | 850 | 14 | 845 | 9 |
| Eriksen et al. | 2020 | RCT | 5 | 560 | 7 | 562 | 6 |
| Faruq et al. | 2021 | Multicenter RCT | 10 | 1400 | 25 | 1395 | 12 |
| Garcia et al. | 2022 | RCT | 3 | 480 | 8 | 478 | 6 |
| Huang et al. | 2023 | Multicenter RCT | 14 | 1800 | 33 | 1810 | 12 |
```

(Totals: PPI 73 / 8810 (0.83%) vs Control 165 / 8786 (1.88%).)

**5. On the Data Extraction card, click the primary button labeled "Run meta-analysis (N/N rows complete)".**

This button — *not* a free-form message — emits the magic prefix `Confirmed extracted data …` plus the extraction JSON, which is what triggers the model to call `sandbox_exec` and render the forest plot.

> ⚠ **Don't type "approved", "go ahead", "run it" etc. in the chat input.** The thread will stay on the meta-analysis specialist (it's pinned), but without the JSON payload the model can't call `sandbox_exec` — it will compute the pooled numbers inline from conversation history and emit a result with `forest_plot_image: null`. The runtime validator (`_require_sandbox_for_results`) will force a retry, but you'll burn an extra Bedrock round-trip per attempt. Always click the button.

**6. On the Meta-Analysis Results card, click "Download PDF" or "Download DOCX" to grab a manuscript-style report.**

The report bundles, in this order: research question · PICO table · included studies table · per-outcome pooled estimate (RR, 95% CI, I², heterogeneity p) · embedded forest plot · clinical interpretation · summary · caveats.

**7. From the same terminal card, click "→ Draft as manuscript", "→ Draft GRADE", or "→ Draft lay summary"** to demonstrate the cross-handoff into Phase 05 specialists (each seeds a new thread with the meta-analysis JSON verbatim).

#### Sanity checks

- Pooled RR ≈ **0.44** (95% CI roughly 0.33–0.58), random-effects.
- I² in the **30–50%** range — some between-study variation, not extreme.
- 8 study rows + diamond. Plot well under sandbox limits (figsize / dpi / 2 MB).
- **Funnel plot + Egger's test** appear automatically in STEP 5 (beyond-forest-plot viz, see A2).
- "Take it away" panel offers PDF + DOCX; the three handoff buttons (manuscript / GRADE / lay summary) seed new threads.

#### Variant — stress-test heterogeneity (optional)

Swap the step-4 extraction with this table to force I² ≈ **75–85%** and a wide pooled CI. Two outliers (Diaz 2019, Faruq 2021) favor control.

```markdown
| Study | Year | Design | PPI events | PPI total | Control events | Control total | Follow-up (mo) | Approx RR |
|---|---|---|---|---|---|---|---|---|
| Anderson et al. | 2014 | Multicenter RCT | 8 | 1200 | 20 | 1198 | 12 | 0.40 |
| Bekele et al. | 2016 | Single-center RCT | 3 | 400 | 12 | 395 | 6 | 0.25 |
| Chen et al. | 2018 | Multicenter RCT | 15 | 1500 | 25 | 1495 | 12 | 0.60 |
| Diaz et al. | 2019 | Pragmatic RCT | 22 | 600 | 12 | 605 | 9 | 1.85 |
| Eriksen et al. | 2020 | RCT | 4 | 700 | 16 | 695 | 6 | 0.25 |
| Faruq et al. | 2021 | Multicenter RCT | 18 | 900 | 14 | 895 | 12 | 1.28 |
| Garcia et al. | 2022 | RCT | 5 | 800 | 19 | 795 | 6 | 0.26 |
| Huang et al. | 2023 | Multicenter RCT | 11 | 1300 | 24 | 1305 | 12 | 0.46 |
```

---

### E2 — Real evidence pipeline: Protocol → Search → Meta → RoB (≈ 15 min)

**Demos.** Coherence across handoffs — PICO carries through every specialist, so the protocol's eligibility criteria match the search strategy, which matches the meta-analysis inclusion set, which matches the RoB cohort.

#### E2a. Draft a PRISMA-P protocol

**Trigger** (either):

```
/protocol SGLT2 inhibitors for HF prevention in T2DM
```

```
Help me draft a PRISMA-P protocol on whether SGLT2 inhibitors reduce heart-failure hospitalization in adults with type 2 diabetes.
```

**Expected flow:** `protocol_methods` card → edit fields → click **Methods confirmed — draft full protocol** → `protocol_document` (draft) with grounded background + numbered citations + PROSPERO field map → free-form refinements or **Finalize protocol** → final card with **Build the search strategy** + **Skip to meta-analysis** handoff buttons + **Download PDF / DOCX**.

#### E2b. Build the search strategy

From E2a, click **Build the search strategy**. Or standalone:

```
/search proton pump inhibitor + DAPT in post-PCI ACS
```

**Expected flow:** `query_blocks` card with MeSH chips → edit synonyms / drop unwanted MeSH → **Confirm terms** → `strategy_result` (draft) with composed Boolean + live hit counts → click refinement buttons until band-status banner turns green → **Finalize strategy** → adds **Run meta-analysis on these results** + **Watch this query** buttons.

#### E2c. Hand off to meta-analysis

Click **Run meta-analysis on these results**. New thread arrives at PICO step already understood; STEP 3's search query is the validated string. For a deterministic forest plot, drop in E1's synthetic extraction at this point.

#### E2d. Assess risk of bias

From the meta-analysis `data_extraction` card, click **Assess risk of bias** — pre-loaded with the extracted studies. Standalone:

```
/rob Run RoB 2.0 on PMID 20925534, PMID 30873575, PMID 31091374
```

**Expected flow:** `rob_assessments` card with 5 (RoB 2.0) or 7 (ROBINS-I) domains → edit judgments → **RoB confirmed — generate summary** → stacked-bar plot + per-domain count table → **Finalize RoB** → final card with **Re-run meta-analysis excluding high-RoB studies** + **Download PDF / DOCX**.

#### Sanity checks

- **RoB auto-suggest.** `study_designs = ["Randomized Controlled Trial"]` only → defaults to RoB 2.0; add "Cohort study" → switches to ROBINS-I.
- **Citation provenance.** Every reference card has an origin badge: `search_papers` (cyan), `web_search` (blue), `wikipedia` (amber). PubMed refs MUST display a PMID; web sources MUST display a URL.
- **No fabricated numbers.** Background should not contain percentages, sample sizes, or effect estimates that aren't tied to a `[N]` citation.
- **PROSPERO placeholders are honest.** User-specific fields (Named contact, Funding sources, IRB number) display content starting with `[USER INPUT NEEDED:` styled in amber.
- **Quotes are verbatim.** RoB italic blue quote blocks must appear word-for-word in the linked abstract.

---

### E3 — SR title/abstract + full-text screening UI (≈ 10 min)

**Demos.** The dual-review screening surface that bridges search (which returns thousands of hits) and meta-analysis (which uses the dozen that meet criteria). Closes the single biggest time-sink in any SR.

**Pre-req.** Researcher role (carries `sr.create`).

**Open at:** `http://localhost:8000/sr.html`

#### Steps

**1. Create a SR project.** Click **+ New project**. Fill in:

- **Title:** `SGLT2 inhibitors for HF prevention in T2DM (smoke review)`
- **PICO:** Adults with T2DM · SGLT2 inhibitor · standard care · HF hospitalisation.
- **Inclusion criteria:** RCT, English, ≥12 weeks follow-up, HbA1c reported at baseline.
- **Exclusion criteria:** Type 1 diabetes; pre-existing HF NYHA III-IV; no quantitative outcome data.

**2. Add reviewers.** From the project page → **Manage reviewers**:

- Invite a researcher by email → grant role `reviewer_1` at `scope_type='sr_review'`, `scope_id=<project_id>`.
- Invite a second researcher → grant role `reviewer_2`.
- Invite a third → grant role `adjudicator`.

**3. Ingest candidates.** Click **Run search** → reuses the multi-source paper search to populate `SrCandidate` rows. For a deterministic demo, paste a small canned list:

```
PMID 30859901
PMID 31535827
PMID 32865375
PMID 32865377
PMID 33069326
```

**4. Walk through the screening queue.** Open the candidate queue. For each abstract:

- **AI-assist suggestion** appears inline (include / exclude / maybe + confidence) — pre-classification by the `sr_screening_assist` specialist.
- Press keyboard shortcut: `i` = include · `e` = exclude · `m` = maybe.
- Reviewer 1's decisions are blind to Reviewer 2's — switch user accounts to demo the parallel review.

**5. Adjudicate conflicts.** Once both reviewers complete their pass, conflicts surface in the adjudicator's queue. Adjudicator clicks include / exclude / kick-back-for-discussion.

**6. Generate PRISMA flow.** From the project page → **PRISMA flow** → hand-rolled SVG diagram opens at `GET /api/sr/projects/{id}/prisma/diagram.svg`. The standard 4-row flow with side exclusion boxes including per-reason breakdown.

#### Sanity checks

- **Dual review is blind.** Reviewer 1's response shape carries no Reviewer 2 votes; switching to Reviewer 2's account shows their own queue with no agreement indicator until adjudication.
- **AI-assist persistence.** AI suggestions land in `AiSuggestion` rows so accuracy-vs-human can be measured later. Override behaviour: reviewer's manual decision is recorded separately from the AI suggestion (both survive in the audit trail).
- **Agreement rules.** Both include → terminal `include`; both exclude → terminal `exclude`; disagree → `pending_adjudication`.
- **PRISMA counts derive entirely from `SrCandidate.status` + reason codes** — no fabrication.

#### Failure modes

- Missing `sr_review` scope grant → user can't see the project at all (404, not 403, to avoid id leaks).
- Adjudicator without conflicts visible: confirm both reviewers have committed their pass.

---

### E4 — Network meta-analysis (NMA) (≈ 8 min)

**Demos.** When the clinical question involves ≥3 interventions (e.g. "of the five direct oral anticoagulants, which has the best safety-efficacy balance?"), pairwise meta-analysis can't answer it. NMA pools direct + indirect evidence across the network.

**Trigger:**

```
/nma compare five direct oral anticoagulants for stroke prevention in non-valvular AF — apixaban, dabigatran, edoxaban, rivaroxaban, warfarin
```

**Expected flow:** Five-stage workflow — intake → `PicoNetwork` with ≥3 interventions + **transitivity rationale** (schema-required) → search → per-arm extraction → NMA results with league table + SUCRA + network-geometry PNG. Default backend is **frequentist** (mvmeta + electrical-network analogy on numpy + scipy; 1000 MVN posterior draws). Type `Run Bayesian NMA` after the intake step to opt into the PyMC NUTS backend.

#### Sanity checks

- **Transitivity rationale required.** Schema rejects an NMA intake without `pico.rationale` — the operator must justify why indirect comparisons are valid (similar populations, comparable outcome definitions).
- **League table is colour-coded** square matrix — green = protective, red = risk-increasing, white = null-crossing.
- **SUCRA ranking** sums to ~1.0 across interventions.
- **Network geometry PNG** renders with node size ∝ √n_studies, edge width ∝ n_head_to_head_trials.
- **Anti-hallucination.** PMIDs from `search_papers` only; pooled effects + CIs + SUCRA from sandbox runs only.
- **Bayesian backend** writes `skip_reason` when PyMC is missing from the sandbox image (documented deploy-time gate).

#### Failure modes

- `/nma` with only 2 interventions → schema error from `PicoNetwork.interventions` `min_length=3`.
- Bayesian backend unavailable → `skip_reason="PyMC not installed in sandbox image; rebuild sandbox with pymc>=5"`.

---

### E5 — IPD meta-analysis (≈ 8 min)

**Demos.** When you have subject-level data from included trials (not just published summaries), IPD MA is the gold standard. One-stage + two-stage run side-by-side as the methodological diagnostic.

**Trigger:**

```
/ipd individual patient data meta-analysis on statins for primary prevention of cardiovascular events
```

**Expected flow:** 5-stage workflow — intake → bundle with per-trial CSV pastes + column mapping → main results (one-stage + two-stage side-by-side) → subgroup × treatment interaction → assembled document.

Paste a minimal canned bundle when the bundle step opens:

```
Trial: ASCOT-LLA
subject_id,treatment,outcome,age,sex
S001,1,0,55,M
S002,1,1,62,M
S003,0,1,58,F
S004,0,0,49,M

Trial: WOSCOPS
subject_id,treatment,outcome,age,sex
W001,1,0,60,M
W002,0,1,67,M
W003,1,0,54,F
W004,0,0,52,F
```

#### Sanity checks

- **One-stage AND two-stage always run side-by-side.** A divergence > 0.2 in log-scale flags model misspecification.
- **Sandbox-backed.** `one_stage.py` runs statsmodels MixedLM (continuous) / GLM Binomial logit / stratified PHReg (TTE); `two_stage.py` does per-trial estimate + DerSimonian-Laird random-effects pool with I² + τ².
- **Anti-hallucination.** Per-trial estimates + I² + τ² + interaction p come from sandbox runs only.

---

### E6 — GRADE Summary of Findings + PRISMA 2020 checklist (≈ 5 min)

**Demos.** Every journal-acceptable systematic review now requires a GRADE SoF + PRISMA 2020 reporting checklist. Most teams produce these as the very last step before manuscript submission.

**Pre-req.** A completed E1 or E2 meta-analysis in the same thread (or use the cross-handoff from the meta_analysis terminal card).

**Trigger** (either):

```
/grade Generate a GRADE SoF for the PPI/DAPT meta-analysis
```

Or click **→ Draft GRADE** on the meta-analysis terminal card.

**Expected flow:** 5-stage workflow — intake → per-outcome assessment → SoF assembly → PRISMA 2020 checklist → assembled document. Certainty is COMPUTED via Pydantic `computed_field` from per-domain ratings (RCT start=4 / observational=2; serious=−1, very_serious=−2; observational upgrades for large effect / dose-response / residual confounding).

#### Sanity checks

- **Certainty cannot be inline-asserted.** Try to override `certainty` in a turn → schema rejects (it's a `computed_field`).
- **Every downgrade rationale must cite a source number** (I² value, CI bounds, n_studies, Egger's p). System prompt forbids handwaving.
- **PRISMA 2020 checklist** carries 42 canonical sub-items from Page et al. (BMJ 2021). Unreported items render as "Not reported" — operator can't claim items they didn't fulfil.
- **Visual SoF chip table** auto-rendered host-side from assessments (no agent involvement) — colour-coded green / amber / red per the GRADE-pro convention.
- Landscape PDF + DOCX export.

---

### E7 — Living-review watch (≈ 5 min setup, then background)

**Demos.** Scheduled re-search + materiality-alert loop. Setup is ~5 minutes; the rest is background.

**Architecture in one paragraph.** `POST /api/watches` runs the saved search once to populate the baseline PMID set, persists the watch, and registers an APScheduler cron job. At each fire, `services/watch_runner.py` re-executes the search via `tools/clinical/search_papers._fan_out`, computes the diff against `baseline_pmids_json`, hands new papers to `agent/specialists/watch_triage.py`, persists a `WatchRun`, and creates a `Notification` if `notify=true`. The bell polls `/api/notifications/unread-count` every 30 s.

#### Steps

1. Build a search strategy end-to-end (E2b) to a finalized `strategy_result`.
2. On that card, click **Watch this query**.
3. In the modal:
   - **Name** — pre-filled from the original research question.
   - **Schedule preset** — pick **Every 2 minutes (demo)** to see the loop without waiting.
   - **Materiality threshold** — leave at `0.6`.
   - Click **Create watch**.
4. Click sidebar **▤ Watches** → the new watch appears with status `active`, baseline = 5 PMIDs, cron `*/2 * * * *`, next-run timestamp ~2 minutes out.
5. Wait 2 minutes (or click **Run now**). Click **Show runs** to expand history.
6. If `success` AND any triage hit `materiality ≥ 0.6`, the bell badge lights up. Click → dropdown → click a notification → marks read + jumps to `/watches.html`.

#### Sanity checks

- **Baseline pre-populated.** Watch-creation logs show `pre-populated baseline with N PMIDs from initial search`.
- **Triage pills are honest.** `relevance=high` + `design_fit=matches` + `materiality > 0.6` reserved for clearly in-scope, well-powered studies.
- **Restart durability.** Restart agent container → startup log shows `Scheduler re-registered N active watch(es) from DB`.

#### Failure modes

- **AWS quota.** `*/2 * * * *` = 30 Bedrock calls/hour. Switch to weekly after the demo.
- **Bad cron.** Returns 400 with parser error.
- **All sources disabled in `/admin.html`** → `status=error` with "no sources enabled".

---

### E8 — Group-level living-review subscriptions (quorum) (≈ 5 min)

**Demos.** Wrap an existing watch with an invited panel that votes yes / no / abstain on each new run. Closes the "guideline committee" gap that personal watches couldn't fill — HTA bodies need consensus, not per-individual alerts.

**Pre-req.** A finalised watch from E7.

#### Steps

**1. Open `/watches.html` → scroll to the new "Group subscriptions" section** (at the top).

**2. Click "+ New subscription".** Fill in:

- **Name:** `Cardio guideline panel`
- **Watch:** select the E7 watch from the dropdown.
- **Min votes:** `2`
- **Min fraction:** `0.5`

Click **Create**. The subscription card lists "0 voters · 0 observers · ≥ max(2, 50%)".

**3. Add members.** Click **Manage members** → paste an email + select role (voter / observer) → **Add**. Repeat for 2-3 voters + 1 observer.

**4. Trigger a watch run** (either click **Run now** on the underlying watch, or wait for the cron to fire).

**5. Cast votes.** From the subscription card → **Show runs + votes** → for the newest run, click **Vote YES** (optionally type a rationale prompt). Switch user accounts and cast votes from other voters until quorum clears.

**6. Verify group notification fan-out.** Once quorum clears, the bell badge increments by `n_members` for every member's account. Each member's notification feed shows a `[Group quorum]` titled alert with the run id + paper count.

#### Sanity checks

- **Observers don't count toward the denominator AND can't vote.** Try to cast a vote from an observer account → 422 with "Only voting members may cast votes…".
- **Vote idempotency.** Re-casting from the same voter overwrites rather than appending (UNIQUE constraint on `(subscription, run, voter)`).
- **Paused subscriptions reject votes outright.** Switch the subscription to `paused` → vote attempt returns 422 with "Subscription is 'paused'…".
- **Idempotent per-recipient fan-out.** A stray double-tally doesn't double-notify; existence check before insert.

#### Failure modes

- **Cross-watch run rejected.** A run from a different watch's id returns 422 ("Run X not found on this subscription's watch").
- Empty subscription (0 voters): quorum never clears regardless of threshold.

---

## Phase 02 — Trial design

> **Phase context.** Once the evidence base supports a new trial, the team specifies the question (PICOT), powers it (sample size), and writes the analysis plan. Deliverables: protocol body + Statistical Analysis Plan, often submitted to peer review before any subject enrols.

### D1 — Sample-size + SAP drafter (≈ 10 min)

**Demos.** Four-stage workflow producing an ICH-E9-shaped Statistical Analysis Plan with downloadable PDF + DOCX. Sample-size calculator runs directly on scipy.stats — no sandbox round-trip.

**Trigger:**

```
/sap two-arm RCT of CBT vs SSRI for depression, primary outcome HAM-D at 12 weeks
```

**Expected flow:**

1. **PICOT intake** card — Population, Intervention, Comparator, Outcome (with measurement scale), Time. Edit fields → **PICOT confirmed**.
2. **Sample-size derivation** — pick formula (two-proportions / two-means / time-to-event Schoenfeld / paired); fill effect size + α + 1-β + dropout. The card calls `sample_size` tool host-side and returns N per arm + total + dropout-inflated N. **Sample size confirmed**.
3. **Analysis plan** — ICH-E9-shaped sections (study objectives + estimands + analysis populations + primary + secondary analyses + interim + sensitivity + missing-data handling). **Analysis plan confirmed**.
4. **Assembled SAP document** — full Markdown + PDF + DOCX download.

#### Sanity checks

- **Sample-size return shape matches `SampleSizeResult`** — N_per_arm, total_n, total_inflated_n, formula, assumptions echoed back.
- **Four formulas supported.** Each one rejects nonsensical inputs (negative effect size, α outside (0,1)).
- **PDF carries the full SAP** — Title · PICOT · Sample size · Analysis plan · Methods · Assumptions · Plus statistical sensitivity tables when present.
- **Estimands framework** required (ICH E9(R1)). Each primary analysis has an `estimand` block (target population + treatment + endpoint + ICE handling + summary measure).

#### Variant — non-inferiority (currently deferred but worth narrating)

The four superiority formulas ship; non-inferiority + equivalence are roadmap items. Surface the limitation: "today the calculator covers superiority; NI / equivalence formulas are a follow-up."

---

## Phase 03 — Start-up

> **Phase context.** The trial gets registered with regulators and the ethics committee (IRB / IRC) approves the protocol + ICF. Without these the trial cannot enrol its first subject. Both deliverables have strict structured templates and unforgiving compliance requirements.

### S1 — Trial-registration drafter (CT.gov + EU CTR / CTIS) (≈ 8 min)

**Demos.** Side-by-side drafts for ClinicalTrials.gov PRS and EU CTR / CTIS. NCT IDs and CTIS numbers are NEVER fabricated — assigned by registries on submission.

**Trigger:**

```
/register Phase 2 SGLT2i in HFpEF, double-blind randomised parallel, primary outcome KCCQ Total Symptom Score at week 24
```

**Expected flow:**

1. **Registration intake** — sponsor + indication + design + Phase + primary outcome + sites. Sponsor PHI fields marked `[SPONSOR INPUT]`.
2. **Core fields** card — CT.gov + EU CTR field shapes side-by-side. Edit → **Core fields confirmed**.
3. **Registration drafts** — copy-paste-ready blocks for the CT.gov PRS portal and the EU CTIS portal, with NCT placeholder `[CT.gov-assigned]` + CTIS placeholder `[EUDRACT-assigned]`. **Drafts confirmed**.
4. **Assembled registration document** with background paragraph + PDF + DOCX export.

#### Sanity checks

- **NCT / CTIS IDs NEVER auto-filled.** Schema rejects anything matching `NCT\d{8}` from the model.
- **Background paragraph cites real evidence** via `search_papers` (PMIDs + origin badges).
- **Sponsor PHI sanitised** — `[SPONSOR INPUT]` placeholder colour-coded amber.

#### Cross-handoff

Click **Draft IRB packet from registration intake** on the final card → seeds an IRB drafter thread (see S2) with the registration's core fields pre-loaded.

---

### S2 — IRB / ethics packet + ICF drafter (≈ 10 min)

**Demos.** Protocol synopsis (1-2 page IRB-triage summary) + Informed Consent Form (schema-enforced against 21 CFR §50.25(a)) + computed Flesch-Kincaid reading-level grade. Multilingual en/es/fr/de.

**Trigger** (or click the cross-handoff from S1):

```
/irb Phase 2 trial of Drug X in disease Y, US IRB, language English, reading grade 8
```

**Expected flow:**

1. **IRB intake** — protocol summary + jurisdiction + language + reading-level target + population descriptor. Default reading grade = 8.
2. **Protocol synopsis** — 8 ICH E6(R2)-aligned sections (design / objectives / endpoints / methods / statistics / eligibility / schedule / risks). **Synopsis confirmed**.
3. **Informed Consent Form** — schema-enforced 9-section minimum covering 21 CFR §50.25(a) elements A-I. ICF carries `reading_level_target` + `reading_level_grade_actual` (Flesch-Kincaid computed at draft time). Multilingual — try `language: es` to switch to Spanish.
4. **Assembled IRB packet** — synopsis + ICF + signature block + PDF + DOCX.

#### Sanity checks

- **ICF schema-enforced.** Try to emit an ICF with 8 sections → schema rejects (`min_length=9`).
- **Reading-grade overshoot flagged in PDF.** Above-target grades render with an amber warning band on the cover page.
- **Multilingual.** `language: en|es|fr|de` switches the ICF body language. Any other language → clarification asking for a human translator.
- **No medical-decision language.** Try to inject "you should consent" → system prompt blocks; ICF uses "you may choose to take part" framings.
- **No promissory benefit claims.** "you will get better" forbidden; "may benefit" / "may provide information that may help future patients" allowed.

#### Cross-handoff

Click **Draft lay summary from IRB packet** on the final card → seeds a lay-summary thread (see A6) with the protocol synopsis pre-loaded as the recruitment-language source.

---

### S3 — Randomisation / IRT (≈ 8 min)

**Demos.** Schedule generation (DM role) → at-enrolment allocation (coordinator + PI roles) → emergency code-break (PI role only). Pulls TRT01P / TRT01A into SDTM ADSL on derivation.

**Pre-req.** SMOKE-T2DM-001 deployment from X1-X2.

#### Steps

**1. Generate randomisation schedule.** Switch to a data_manager account. Open the Smoke deployment's "Randomisation" panel in `collector.html`. Click **Generate schedule**:

```json
{
  "blinding_mode": "double_blind",
  "algorithm": "permuted_block",
  "block_size": 4,
  "arms": [{"name": "Active", "ratio": 1}, {"name": "Placebo", "ratio": 1}],
  "n_subjects": 60,
  "random_seed": 12345
}
```

Schedule appears as a sequence of 60 allocations, masked (you see slot numbers, not arms, because blinding_mode=double_blind).

**2. Allocate a subject at enrolment.** Switch to a coordinator account. On the subject S01-001 page → **Randomize**:

```
POST /api/edc/subjects/{subject_id}/randomize
```

Returns `Allocation.id` + masked arm placeholder (`"Arm A"`). The allocation is bound to the next unused schedule entry.

**3. Emergency code-break.** Switch to the PI account. From the subject row → **Code-break**:

```json
{
  "reason": "Subject presented to ED with severe hypoglycaemia; treating physician requires arm to choose appropriate management."
}
```

The reason is required (≥8 chars). The PI sees the actual arm; the audit trail records the code-break event with reason + actor + timestamp.

**4. Re-derive CDISC ADSL** to confirm `TRT01P` / `TRT01A` populated. Trigger:

```
POST /api/edc/deployments/{deployment_id}/cdisc/derive
```

→ Open the Submissions card → click **Download adsl.csv** → `TRT01P` column populated from `Allocation.arm` (not "TBD").

#### Sanity checks

- **`randomization.generate` is DM-only.** Coordinator allocation attempt to `/schedule` → 403.
- **`randomization.codebreak` is PI-only.** Coordinator + DM attempts → 403.
- **Code-break reason validated.** `reason < 8 chars` → 422.
- **Blinding mask honoured in API responses.** `GET /api/edc/subjects/{subject_id}/allocation` returns masked arm until code-break flips `unblinded=True`.
- **ADSL pulls from Allocation.** After derive, `TRT01P` matches the schedule's arm for each allocated subject; un-allocated subjects render `TRT01P="TBD"`.

---

## Phase 04 — Execution

> **Phase context.** Once the trial is approved and registered, real subjects enrol and real data accumulates. This is the largest, longest, and most operationally complex phase. The platform's regulatory-grade infrastructure (Part 11 / ALCOA+ / GCP / ICH E6) does the most work here.

### X0 — Trial-staff setup: invite, scope, onboard, countersign (≈ 15 min)

**Demos.** The regulatory-grade user-administration loop you walk before X1. Admin invites the trial team with scope-aware role grants; the SoD matrix blocks ICH E6 §5.18–5.19 + Part 11 §11.10(d) violations at invite time; each invitee completes an `/onboarding.html` wizard that captures the fields their role requires; clinical-data surfaces stay locked until onboarding is complete (server-side gate); the PI countersigns delegation entries with Part 11 §11.200 password reauth; every action lands in an append-only audit log.

> Skip this demo for the smoke-deployment quick run — the legacy `default-user` placeholder is admin + everything in single-user dev mode. Run X0 when you want to walk through the actual multi-user posture an audit / inspection would see.

**Pre-req.** An admin Cognito user already onboarded (run cognito_setup.py + complete the wizard once; default-user account works in auth-disabled mode).

**Open at:** `http://localhost:8000/users-admin.html`

#### X0a. Roles catalogue + SoD reference

**1. Open the roles catalogue.** Top-right: `📖 Roles catalogue` button.

The side drawer surfaces every canonical role with its regulatory basis (ICH E6 §4.1, 21 CFR §312.60–62, Part 11 §11.10, EU CTR Art. 49). Skim:

- **Principal Investigator** — license + GCP + CV + financial disclosure required.
- **Clinical Research Coordinator** — GCP required.
- **Data Manager** — GCP required.
- **Monitor** — sponsor-side; independent from site staff per §5.18.
- **Auditor** — independent from the team being audited per §5.19.

Close the drawer.

#### X0b. Invite the trial team with scoped role grants

The SMOKE-T2DM-001 trial needs a 5-person team. We'll invite each with the right scope, watching the SoD matrix block the obvious conflicts.

**1. Open the Invite modal.** Top-right: `+ Invite user`.

**2. Invite the PI.**

- Email: `pi@smoke.example`
- Role row 1: `Principal Investigator (PI)` · scope `study` · scope id `SMOKE-T2DM-001`
- Click **Send invitation**.

The status pill reads `Invited pi@smoke.example (cognito=FORCE_CHANGE_PASSWORD).` The Cognito invitation email lands in the sponsor's mailbox; the PendingInvitation row carries the scoped grant so first login auto-assigns `principal_investigator @ study:SMOKE-T2DM-001`.

**3. Invite the coordinator.**

- Email: `coord@smoke.example`
- Role row 1: `Clinical Research Coordinator (CRC)` · scope `site` · scope id `SITE-01`
- Click **Send invitation**.

**4. Demonstrate an SoD block: try to invite a person as both DM AND PI on the same study.**

- Email: `dm@smoke.example`
- Role row 1: `Data Manager (DM)` · scope `study` · scope id `SMOKE-T2DM-001`
- Click **+ Add another role**
- Role row 2: `Principal Investigator (PI)` · scope `study` · scope id `SMOKE-T2DM-001`
- Notice the **conflict block** appears inline:

  > **Data Manager (DM) ↔ Principal Investigator (PI)** on study `SMOKE-T2DM-001`
  > 21 CFR Part 11 §11.10(d) + ICH E6 §1.27 — the Data Manager locks the database and the Principal Investigator signs the casebook; they must be distinct individuals to preserve the audit + accountability separation.

- Click **Send invitation** → 422 with the conflict surfaced. The matrix is enforced server-side; the client-side block is a UX nudge.

**5. Fix it — invite just the DM.**

- Delete the PI row (`×` button) so only `Data Manager @ study:SMOKE-T2DM-001` remains.
- Click **Send invitation** → succeeds.

**6. Invite the monitor.**

- Email: `monitor@smoke.example`
- Role row 1: `Clinical Research Associate (Monitor)` · scope `study` · scope id `SMOKE-T2DM-001`
- Click **Send invitation**.

**7. Invite the auditor (demonstrates the global-scope path).**

- Email: `auditor@smoke.example`
- Role row 1: `Auditor (independent QA)` · scope `global` · scope id (omit)
- Click **Send invitation**.

**8. (Optional) Legitimate SoD override.** Single-site academic studies sometimes need the same person to wear two regulatory hats. The platform allows an explicit override with rationale, NEVER silently:

- Open Invite modal → email `dual@smoke.example`
- Role row 1: `Coordinator @ site:SITE-01`
- Role row 2: `Monitor @ study:SMOKE-T2DM-001`
- Conflict block appears (ICH E6 §5.18 — sponsor monitor cannot also be site staff)
- Fill the **Override rationale** textarea: `Investigator-initiated single-site academic study; institutional approval CAS-2026-014 on file. Reviewed by IRB minutes 2026-05-15.`
- Click **Send invitation** → succeeds. The rationale is stored on the RoleAssignment row + replicated to the audit log as `role.grant_overridden`.

#### X0c. Invitee onboarding — PI walks the wizard

Switch to an incognito browser window (or a separate browser profile).

**1. Open the Cognito invite link from `pi@smoke.example`'s mailbox** → set a permanent password → land on `/`.

**2. Try to reach `/collector.html`** → IIFE redirects to `/onboarding.html` because `/auth/me.onboarding_required: true`.

**3. Walk the stepper.** The wizard reads the PI's role assignments + REQUIRED_FIELDS and renders only the conditional steps the role needs.

For the PI, the visible steps are:

- **Welcome** — shows the regulatory basis for the PI role (ICH E6 §4.1; 21 CFR §312.60+§312.62; EU CTR Art. 49). Click **Next**.
- **Identity** — title `Dr`, first `Ada`, last `Lovelace`, credentials `MD MRCP`. Save → Next.
- **Licensure** — license number `GMC-12345678`, country `United Kingdom`. Save → Next.
- **Training** — completed date `2026-01-15`, provider `CITI`, certificate URL `https://drive.example/cita-cert.pdf`. Save → Next.

  *Behind the scenes:* the wizard POSTs `/api/onboarding/me/training-records` with `training_type=ich_gcp`; the server creates the TrainingRecord row AND mirrors `gcp_training_completed_date` + `gcp_training_provider` + `gcp_certificate_url` into UserProfile so the gate sees the GCP field as filled.

- **Disclosure** — CV URL `https://drive.example/cv-pi.pdf`, financial-disclosure signed date `2026-02-01`. Save → Next.
- **Delegation** — for each trial-scoped grant, capture delegation entries:
  - Trial id: (paste the trial id from `/accounts.html` trial detail page)
  - Study role: `Principal Investigator`
  - Delegated tasks: `final medical review, AE classification, casebook signature, SAE causality assessment`
  - Start date: `2026-06-01`
  - Click **Add entry**

  The entry is captured *unsigned* — `signed_by_pi_user_id` stays null. The PI countersigns later (X0e).

- **Complete** — review screen. If any field is still missing, the wizard shows AMBER "still missing" chips with the role + field name; the Complete button stays enabled but a 422 from the server kicks the user back. Click **Complete onboarding**.

The server re-validates EVERY required field (anti-tamper), sets `onboarding_completed_at = now()`, records the `onboarding.completed` audit event, and redirects to `/`.

**4. Verify the gate dropped.** Click the sidebar `+ Data Capture` (or visit `/collector.html`) → loads. The IIFE no longer redirects because `onboarding_required` is now false.

#### X0d. Coordinator walks a shorter wizard

The wizard's `activeSteps()` filters conditional steps per role. A coordinator's REQUIRED_FIELDS = `first_name + last_name + gcp_training_completed_date`, so:

**1. Log in as `coord@smoke.example`** (different incognito window).

**2. Steps shown:** Welcome → Identity → Training → Delegation → Complete. (Licensure + Disclosure hidden — not required for coord per ICH E6 §4.1.5.)

**3. Fill Identity + Training** with mock values. Skip Disclosure entirely. Capture a delegation entry on the trial: study_role `Clinical Research Coordinator`, tasks `informed consent, enrolment, data entry, query response`.

**4. Click Complete** → server-side check passes → gate drops.

#### X0e. PI countersigns the team's delegation entries

Switch back to the admin window. The PI account (`pi@smoke.example`) now needs to sign the coordinator's + DM's + monitor's delegation entries — ICH E6 §4.1.5 requires the PI signature on the delegation log.

**1. Open `/users-admin.html`** as the PI (PI also holds admin if you grant `user.manage`; otherwise, the PI sign queue is just for visibility). Click the **PI sign queue** tab.

**2. The queue lists every unsigned delegation entry on trials the caller is PI on.** Each row carries the member email + study role + delegated tasks + period + a **Sign** button.

**3. Click Sign** on the coordinator's entry → password modal opens.

**4. Enter the PI's Cognito password** → Submit → 21 CFR Part 11 §11.200 reauth fires against Cognito (the same path eCRF E7 form-signing uses) → on success, the entry is countersigned with `signed_by_pi_user_id` + `signed_at` + the audit event `delegation.signed`.

**5. Repeat for each unsigned entry.**

#### X0f. Audit log inspection

Still on `/users-admin.html`, click the **Audit log** tab.

**1. Click Apply** (no filters). The 6-column table renders the last N events newest-first:

```
2026-06-01 14:22  pi@smoke      delegation.signed     coord@smoke   trial:SMOKE-...  entry_id=del-..., study_role=Clinical Research Coordinator
2026-06-01 14:15  coord@smoke   onboarding.completed  coord@smoke   —                roles=["coordinator"]
2026-06-01 14:14  coord@smoke   training.recorded     coord@smoke   —                training_type=ich_gcp, topic=ICH E6 GCP
2026-06-01 14:01  admin@smoke   invite.created        —             —                email=coord@smoke.example, assignments=[{role: coordinator, scope_type: site, scope_id: SITE-01}]
2026-06-01 13:58  admin@smoke   role.grant_overridden dual@smoke    study:SMOKE-...  override_rationale=Investigator-initiated...
```

**2. Filter for `role.grant_overridden`** (Action dropdown). Every legitimate SoD override surfaces here — auditors filter on this to inspect every exception.

**3. Click Download PDF** → `user-admin-audit.pdf` lands per Part 11 §11.10(e). Compact-encoded payloads in monospace; truncated at 200 chars per cell so a single long rationale doesn't blow up the layout.

#### X0g. Regulatory PDF reports

Click the **Reports** tab.

**1. Per-trial delegation log.** Paste the trial id → Download. ICH E6 §4.1.5 format: signed vs unsigned counts at the top, table of (member, role, tasks, period, PI signature). UNSIGNED rows highlight AMBER with a footer reminder.

**2. Per-site training matrix.** Paste a site id (`/accounts.html` site picker has the id) → Download. ICH E6 §4.2.4 format: members + expired + expiring-soon counts, table with `EXPIRED` / `DUE SOON` AMBER pills on the status cell.

**3. Audit log.** Click Download full audit (no filters) → same PDF as X0f step 3.

#### X0h. Suspension demonstration

A user fired / on leave / under investigation needs to be fully blocked from every gated endpoint. The U4 suspension gate (in `web/authz.py.require_permission_scoped`) handles this.

**1. Switch to the Users tab** on `/users-admin.html`. Locate `coord@smoke.example` → View.

**2. Click Suspend.** Reason: `Departure from institution; access pending review.` → Submit.

**3. Switch to the coordinator's window.** Try to load `/collector.html` → 403 with `Your account is suspended. Contact a platform administrator to reactivate access.`

Try to POST data via API: `POST /api/edc/subjects/{id}/forms` → 403 same message.

**4. Back in admin → Click Reactivate** on the coordinator's detail panel → access restored on next request.

#### X0i. Training expiry surface

Click the **Training expiring** tab.

**1. Default within_days = 30.** Records with `expires_date` in the past or ≤ 30 days out surface here. The auditor / regulatory-affairs lead uses this to head off lapses before they happen.

**2. Click Apply** → table renders per-user training records with `EXPIRED` (red) vs `DUE SOON` (amber) pills + the topic + provider + completed/expires dates.

#### Sanity checks

- **SoD enforcement is server-side.** The client-side warning is a UX nudge; the actual 422 fires from `web/user_admin.py.invite` and `grant` even if the operator hits the API directly.
- **Override rationale is REQUIRED to bypass a conflict** and is stored on the RoleAssignment row + replicated to the audit log. NEVER silently accepted.
- **Onboarding gate fires for `Permission` in `services/user_admin.CLINICAL_WRITE_PERMS`** (37 perms: data.enter, query.*, sdv.verify, form.sign, casebook.signoff, study.lock, ae.record/classify, screening.*, visit.update, ip.dispense, lab.upload, source_document.upload, cdisc.derive, randomization.*, etc.). Read perms + research-tier skills + admin perms remain open.
- **Suspension gate fires for EVERY perm**, not just clinical-write. It runs BEFORE the onboarding check so the 403 message ("suspended") wins over "onboarding required".
- **PI countersign authority** walks `global` → `trial:<entry.trial_id>` → `study:<id>` (via EcrfStudy.trial_id) on the caller's RoleAssignments. A global PI grant sees every unsigned entry; trial/study-scoped PIs see only their scope.
- **Password reauth is Part 11 §11.200.** Hits the same Cognito path the eCRF E7 form-sign endpoint uses. Falls open in auth-disabled tests; runs the real Cognito call in production.
- **The audit log is append-only by convention.** No `update_audit` / `delete_audit` methods on the repo; static-file test pins this.
- **`onboarding.completed` is recorded for every user including admins** — the audit log shows when each member joined the platform's regulatory-grade posture.
- **GCP cert + CV are URL strings**, not file blobs. S3 multipart upload deferred to a polish slice.
- **The wizard re-validates server-side on `POST /me/complete`.** A misbehaving client can't lie about field completeness via DOM manipulation.

---

### X1 — eCRF design (AI-draft CRFs from a protocol) (≈ 5 min)

**Demos.** AI-drafted CRFs from a protocol paste — designer reviews and edits, never auto-publish.

**Open at:** `http://localhost:8000/ecrf.html`

#### Steps

**1. Click "New study"** and fill in:

- **Protocol ID:** `SMOKE-T2DM-001`
- **Title:** `Phase 2 SGLT2i in adults with T2DM (smoke study)`
- Click **Create study**.

**2. Click "Draft forms from protocol"** and paste:

```
Phase 2, single-arm, open-label study of an SGLT2 inhibitor in adults with type 2 diabetes mellitus.

Eligibility:
- Adults aged 18–75 years.
- HbA1c 7.0–10.0% at screening.
- On stable metformin ≥3 months prior to enrolment.
- BMI 22–40 kg/m².

Schedule of assessments:
- Visit 1 (Baseline, Week 0): consent, demographics, vital signs, fasting glucose, HbA1c, body weight, eligibility verification, study-drug dispensing.
- Visit 2 (Week 4): vital signs, fasting glucose, AE review.
- Visit 3 (Week 12): vital signs, fasting glucose, HbA1c, body weight, AE review, lab safety panel.
- Visit 4 (Week 24, primary endpoint): vital signs, fasting glucose, HbA1c, body weight, AE review, lab safety panel.
- Visit 5 (EOS, Week 28): final vital signs, AE review, treatment-emergent serious AE inventory.

Primary outcome: change in HbA1c from baseline to Week 24.
Secondary outcomes: fasting glucose, body weight, proportion with HbA1c <7.0% at Week 24.
Safety outcomes: vital signs, lab safety panel, treatment-emergent adverse events (graded per CTCAE v5).

Patient-reported outcomes (ePRO, weekly):
- Diabetes Treatment Satisfaction Questionnaire (DTSQ, 8 items).
- Self-reported hypoglycaemia events (frequency + severity).
```

Click **Draft forms** → `StudyDraft` returned with proposed forms (Demographics, Eligibility, Vital Signs, Lab Safety, HbA1c, AE Reporting, DTSQ ePRO, etc.) + Visit Schedule. **Nothing auto-saves.**

**3. Click "Accept all"** (or pick a subset) → forms appear at `status=draft`.

**4. Review + edit a form.** Click `Vital Signs`:

- Add a field (+ Item) for systolic BP with edit-check `range: 70–250 mmHg, severity: hard`.
- Soft check: BP 200 if rule is "warn over 180" → auto-raises a query on save.
- Click **Save draft**.

**5. Publish (immutable).** Click **Publish** → form moves `draft → published`. Definition is now immutable; further edits require **New version**.

**6. Export ODM-XML.** `GET /api/ecrf/forms/{form_id}/export.odm.xml` → CDISC ODM-XML document.

#### Sanity checks

- **Nothing auto-publishes.** Drafted forms land at `status=draft`.
- **CDASH naming.** Demographics fields like `BRTHDTC` / `SEX` / `RACE` follow CDASH conventions.
- **Item-ID stability.** Renaming a label does NOT change the `item_id`.
- **Hard vs soft checks distinct.** Hard returns 422; soft saves but creates a query.
- **Published forms cannot be edited in place.** `PUT /api/ecrf/forms/{form_id}` on a published form → 409. Must `new-version` first.

---

### X2 — EDC capture (site coordinator) (≈ 5 min)

**Pre-req.** X1 (published Vital Signs form).

**Open at:** `http://localhost:8000/collector.html`

#### Steps

**1. Create a deployment.** Click **New deployment** → select the published study → name it `Smoke deployment`. This snapshots published form definitions into `DeployedForm` rows.

**2. Add a site:** `Site 01 — University Hospital`.

**3. Add a subject:** subject_code `S01-001`.

**4. Open a form for the subject:** click the subject's row → pick `Vital Signs (Visit 1, Baseline)` → creates a `FormInstance` at `status=in_progress`.

**5. Enter data → save → observe edit-checks:**

- Type values. Violate a hard check (BP 300) → 422 with structured failure list `{detail: {failures: [{item_id, check_id, message}]}}`. Nothing persisted.
- Fix → save succeeds; persists in `ItemData` rows.
- Violate a soft check (BP 200) → saves but **auto-raises a `Query`** in the form's query panel.

**6. Query workflow:** Open the **Queries** panel → respond / close queries. Manual queries: `POST /api/edc/form-instances/{form_instance_id}/queries`.

#### Sanity checks

- **Hard check = blocked save**, 422 response, no `ItemData` written.
- **Soft check = auto-query**, referencing offending `item_id` + check that fired.
- **Audit trail accumulates.** Every save records `AuditEntry` with `old_value → new_value`, `actor_sub`, `source=edc`. Inspect: `GET /api/edc/form-instances/{form_instance_id}/audit`.

---

### X3 — ePRO capture (participant magic-link) (≈ 5 min)

**Pre-req.** X1 (published DTSQ form flagged `epro=true`) + X2 (subject S01-001).

#### Steps

**1. Issue a magic link.** From the subject's row in `collector.html` → **Issue ePRO link** → `POST /api/edc/subjects/{subject_id}/epro-access` returns `{token, epro_path: "/epro.html?token=…"}`. Raw token shown **once**; hashed in DB.

**2. Open the participant surface in an incognito window:**

```
http://localhost:8000/epro.html?token=<the-issued-token>
```

Consent gate appears first → tap **I consent** → `POST /api/epro/consent?token=…` records timestamp.

**3. Pick a PRO form** (DTSQ weekly) → enter 8 items → **Submit**. Set `mark_complete=true` on save to lock the instance.

#### Sanity checks

- **Token scope is per-subject.** Trying to open a form for a different subject → 403.
- **No coordinator data exposed.** ePRO surface only renders forms flagged `epro=true`. Site-only forms invisible.
- **Consent permanent.** Recorded timestamp persists.
- **No raw token in DB.** Only the hash.

---

### X4 — AE / SAE workflow + auto-classification + FDA 3500A (≈ 10 min)

**Demos.** ICH E2A auto-classification on every AE write, 24h escalation timer, FDA 3500A IND safety report draft.

**Pre-req.** SMOKE deployment + subject S01-001 from X1-X2.

#### Steps

**1. Capture an AE (coordinator).** In `collector.html` → Safety panel → **+ Adverse Event**:

```json
{
  "subject_id": "<S01-001 id>",
  "term_text": "Severe hypoglycaemic episode requiring ED visit",
  "meddra_pt": "Hypoglycaemia",
  "start_date": "2026-05-31T08:30:00Z",
  "severity_grade": 4,
  "outcome": "recovering",
  "relationship_to_intervention": "probable"
}
```

→ on save, `safety_rules.auto_classify_serious(...)` fires:
- `is_serious=True` (severity 4 = life-threatening)
- `serious_reasons=["life_threatening", "hospitalisation"]`
- `reportable_deadline = now() + 24h`

**2. PI review (PI account).** PATCH the AE to confirm classification:

```json
PATCH /api/edc/ae/{ae_id}
{
  "is_serious": true,
  "pi_classified_at": "2026-05-31T09:00:00Z"
}
```

**3. Check overdue-SAE dashboard.** `GET /api/edc/ae/overdue` → returns any AEs past their `reportable_deadline` without `reported_to_authority_at` set.

**4. Generate FDA 3500A draft.** Click **Draft FDA 3500A** → `reports/sae_3500a.py` renders PDF with platform-derived fields filled + sponsor-supplied fields marked `[SPONSOR INPUT REQUIRED]`.

**5. PI downgrades severity (optional).** PATCH severity to 2 → auto-clears `reportable_deadline`; audit row records the downgrade.

#### Sanity checks

- **`ae.record` is coordinator-tier**, `ae.classify` is PI-tier. Coordinator PATCH attempt → 403.
- **`is_serious` + `serious_reasons` persisted at write time**, NOT recomputed on read. A rule change after the fact doesn't quietly re-classify history.
- **`reportable_deadline` set to now()+24h** on first serious classification. Cleared on downgrade.
- **3500A masks PHI** — only `subject_code`, never participant name.
- **MedDRA PT free-text MVP.** Real validation requires MedDRA license at deploy time.

---

### X5 — Protocol deviation + CAPA lifecycle (≈ 5 min)

**Demos.** Major / minor / critical classification + CAPA author + close.

#### Steps

**1. Log a deviation (coordinator).**

```json
POST /api/edc/deployments/{deployment_id}/deviations
{
  "subject_id": "<S01-001 id>",
  "category": "visit_window",
  "description": "Visit 3 occurred 11 days outside window due to subject travel"
}
```

**2. Classify (DM).**

```json
PATCH /api/edc/deviations/{deviation_id}/classify
{ "severity": "minor", "rationale": "Outside window but no impact on endpoint" }
```

**3. Author a CAPA (DM).**

```json
POST /api/edc/deviations/{deviation_id}/capas
{ "action_text": "Re-train site coordinator on visit-window definitions", "owner_sub": "<DM sub>", "due_date": "2026-06-15" }
```

**4. Complete CAPA + close deviation (PI).**

```json
POST /api/edc/capas/{capa_id}/complete
POST /api/edc/deviations/{deviation_id}/close
```

#### Sanity checks

- **`deviation.classify` is DM**; **`capa.close` is PI**.
- **PI cannot close deviation while any CAPA is `open`** → 422.
- Audit trail per state transition.

---

### X6 — Recruitment / screening log + CONSORT funnel (≈ 8 min)

**Demos.** Screening → eligible → consented → enrolled funnel + per-reason exclusion histogram.

**Open at:** `collector.html` → Recruitment panel.

#### Steps

**1. Record screenings (coordinator).** For each prospective subject:

```json
POST /api/edc/deployments/{deployment_id}/screening-logs
{
  "screening_code": "SCR-0001",
  "site_id": "<Site 01 id>",
  "age_band": "40-49",
  "sex": "F",
  "race": "white"
}
```

Repeat with varying outcomes — for the exclusion path:

```json
PATCH /api/edc/screening-logs/{log_id}/eligibility
{ "eligibility_status": "screen_failure", "exclusion_reason_code": "hba1c_out_of_range" }
```

For the consent + enrolment path:

```json
PATCH /api/edc/screening-logs/{log_id}/eligibility    { "eligibility_status": "eligible" }
PATCH /api/edc/screening-logs/{log_id}/consent        { "consent_status": "consented", "consent_date": "2026-05-31" }
PATCH /api/edc/screening-logs/{log_id}/enrolment      { "enrolment_status": "enrolled", "enrolled_subject_id": "<S01-001 id>" }
```

**2. View the funnel rollup.** `GET /api/edc/deployments/{deployment_id}/recruitment-funnel` returns:
- 4 stage counts (screened / eligible / consented / enrolled).
- Per-reason exclusion histogram (8 CONSORT-aligned codes + 'other').
- Per-week × per-site stage counts.

**3. Collector.html Recruitment panel** surfaces the funnel + recent screenings + a record form.

#### Sanity checks

- **State machines monotonic.** Cannot consent before `eligible`, cannot enrol before `consented`. Repo raises `ClinicalError`.
- **8 canonical exclusion reasons** (consent_refused / age_out_of_range / disease_severity / contraindication / not_meeting_disease_criteria / unable_to_comply / lost_to_contact / other).
- **PHI minimisation.** `age_band` + `sex` + `race` + `dob_year` only — no full DOB / MRN.
- **Funnel rollup computed in Python** for SQLite/Postgres portability.

---

### X7 — Visit scheduling + reminders (≈ 8 min)

**Demos.** VisitSchedule → ScheduledVisit → PlannedVisit auto-gen at enrolment → email reminders.

#### Steps

**1. Create a visit schedule (study_designer).**

```json
POST /api/edc/deployments/{deployment_id}/visit-schedules
{ "name": "Main protocol", "description": "5-visit schedule per protocol" }
```

**2. Add scheduled visits.**

```json
POST /api/edc/visit-schedules/{schedule_id}/visits
{ "visit_name": "Baseline (Visit 1)", "day_offset": 0, "window_before_days": 3, "window_after_days": 3, "reminder_offsets_days": [-3, -1] }

POST /api/edc/visit-schedules/{schedule_id}/visits
{ "visit_name": "Week 4 (Visit 2)", "day_offset": 28, "window_before_days": 5, "window_after_days": 5, "reminder_offsets_days": [-7, -3, -1] }
```

(Repeat for Visits 3, 4, 5 at day_offsets 84, 168, 196.)

**3. Activate the schedule (DM):**

```json
POST /api/edc/visit-schedules/{schedule_id}/activate
```

**4. Set subject baseline date + generate planned visits:**

```json
PATCH /api/edc/subjects/{subject_id}/baseline-date    { "baseline_date": "2026-06-01T00:00:00Z" }
POST  /api/edc/subjects/{subject_id}/planned-visits/generate
```

5 PlannedVisits land for S01-001 at the schedule-implied dates.

**5. Register a participant contact:**

```json
POST /api/edc/subjects/{subject_id}/participant-contact
{ "email": "participant@example.com", "opt_in_channels": ["email"] }
```

**6. Trigger reminders manually:**

```json
POST /api/edc/deployments/{deployment_id}/reminders/fire-due
```

→ returns `{queued: N, sent: M, failed: K, skipped: 0}`. If `AWS_SES_FROM_EMAIL` not set, `provider='dry_run'` is logged.

#### Sanity checks

- **Reminder offsets must be non-positive ints** (days BEFORE due_date). Positive → 422.
- **Planned-visit generation idempotent** per subject × scheduled_visit (UNIQUE).
- **PlannedVisit window** = `planned_date ± window_before/after_days`.
- **SES dry-run mode** writes `SentReminder` with `status='sent', provider='dry_run'` when env var unset.
- **SMS reminders** logged with `status='skipped'` (Twilio not wired).
- **Reschedule requires non-empty `override_reason`.**

---

### X8 — Drug accountability (≈ 8 min)

**Demos.** Per-lot inventory + dispense/return state invariants + reconciliation rollup.

#### Steps

**1. Register an investigational product (study_designer or DM):**

```json
POST /api/edc/deployments/{deployment_id}/ip-catalogue
{ "drug_name": "Empagliflozin", "strength": "10 mg", "units": "tablet" }
```

**2. Receive a shipment (coordinator or DM):**

```json
POST /api/edc/deployments/{deployment_id}/drug-receipts
{ "ip_id": "<ip_id>", "lot_number": "LOT-A1", "quantity_received": 1000, "site_id": "<Site 01 id>" }
```

**3. Dispense to subject (coordinator or PI):**

```json
POST /api/edc/deployments/{deployment_id}/drug-dispensations
{ "subject_id": "<S01-001 id>", "ip_id": "<ip_id>", "lot_number": "LOT-A1", "kit_id": "KIT-001", "quantity_dispensed": 30 }
```

**4. Try to over-dispense:**

```json
POST /api/edc/deployments/{deployment_id}/drug-dispensations
{ "subject_id": "<S01-001 id>", "ip_id": "<ip_id>", "lot_number": "LOT-A1", "kit_id": "KIT-002", "quantity_dispensed": 5000 }
```

→ 422: "Cannot dispense 5000 — only 970 tablet(s) of lot LOT-A1 in inventory."

**5. Log a return (coordinator):**

```json
POST /api/edc/drug-dispensations/{dispensation_id}/return
{ "quantity_returned": 10, "quantity_used": 18, "quantity_lost": 0, "return_reason": "end_of_visit" }
```

→ 422: "quantity_used + quantity_lost cannot exceed quantity_returned." Adjust:

```json
{ "quantity_returned": 18, "quantity_used": 12, "quantity_lost": 0, "return_reason": "end_of_visit" }
```

**6. View reconciliation rollup (DM / monitor / auditor / PI):**

```json
GET /api/edc/deployments/{deployment_id}/drug-reconciliation
```

→ per-lot inventory: `current_inventory = received + (returned − used − lost) − dispensed`.

#### Sanity checks

- **Dispense over inventory → 422.** Inventory derived (not stored).
- **Return without prior dispensation → 422** (FK on dispensation_id).
- **quantity_used + quantity_lost > quantity_returned → 422**.
- **Cross-deployment subject vs IP → 422.**
- **`ip.reconcile` gates the rollup endpoint.** Coordinator GET → 403.

---

### X9 — Source-document extraction (CSV → eCRF) (≈ 10 min)

**Demos.** EHR / registry CSV → versioned mapping → audit-traced ExtractionFill.

#### Steps

**1. Upload a source CSV (coordinator).** Prepare a minimal CSV:

```csv
patient_id,visit_date,sbp_mmhg,dbp_mmhg,heart_rate_bpm,weight_kg
S01-001,2026-06-01,128,82,72,84.5
S01-001,2026-06-29,125,80,70,84.0
```

Upload via collector.html Source-extraction panel:

```
POST /api/edc/deployments/{deployment_id}/source-documents (multipart)
```

→ `SourceDocument` row with SHA-256 content hash + N parsed rows.

**2. Author an extraction mapping (study_designer or DM).**

```json
POST /api/edc/deployments/{deployment_id}/extraction-mappings
{
  "deployed_form_id": "<Vital Signs form id>",
  "name": "default",
  "subject_code_field": "patient_id",
  "mapping": {
    "sbp_mmhg": "sbp",
    "dbp_mmhg": "dbp",
    "heart_rate_bpm": "hr",
    "weight_kg": "weight"
  }
}
```

**3. Dry-run the mapping (coordinator or DM).**

```json
POST /api/edc/extraction-mappings/{mapping_id}/dry-run?source_document_id={doc_id}
```

→ returns the rows that would be written, no persistence yet.

**4. Apply the mapping in `subjects` mode (pre-fills eCRF instances).**

```json
POST /api/edc/extraction-mappings/{mapping_id}/apply
{ "source_document_id": "<doc_id>", "mode": "subjects" }
```

→ `{subjects_filled: 1, items_written: 8, source_rows_unmatched: 0}`. Existing FormInstances for S01-001 receive ItemData with audit chain.

**5. Inspect per-cell audit (DM / monitor / auditor):**

```json
GET /api/edc/deployments/{deployment_id}/extraction-fills?target_id=<subject_id>
```

→ each ExtractionFill row carries `source_row_id`, `source_field`, `mapping_version` (denormalised so audit survives mapping mutations), `applied_value`, `applied_by_sub`, `applied_at`.

**6. Re-upload the same file** → SHA-256 dedupes; returns existing `SourceDocument` row.

#### Sanity checks

- **SHA-256 dedupes.** Same content → existing doc returned.
- **Versioned mappings.** Authoring a new mapping for the same (deployment, form) auto-deactivates the prior version.
- **`mapping_version` denormalised** in ExtractionFill so audit chain survives mapping mutations.
- **RESTRICT FK on mapping** prevents deletion while audit history exists.
- **Dual modes.** `mode="subjects"` pre-fills eCRF; `mode="table"` returns a flat projection without persistence.

---

### X10 — Lab-data feeds (HL7 v2 + CDISC LAB + FHIR) (≈ 10 min)

**Demos.** Three stdlib parsers, idempotent SHA-256 ingest, SDTM LB cascade.

#### X10a. HL7 v2 ORU^R01 upload

**1. Save the following to `cbc.hl7`:**

```
MSH|^~\&|LAB|HOSP|EDC|TRIAL|20260531120000||ORU^R01|MSG1|P|2.5
PID|||S01-001
OBR|1||ACC123|CBC^Complete Blood Count^L|||20260531110000
OBX|1|NM|HGB^Hemoglobin^L||14.2|g/dL|13.0-17.0|N|||F
OBX|2|NM|GLUC^Glucose^L||102|mg/dL|70-100|H|||F
```

**2. Upload via collector.html Lab-data feeds panel:**

```
POST /api/edc/deployments/{deployment_id}/lab-uploads (multipart, source_format=hl7v2)
```

→ `LabBatch.row_count=2` + `was_new=True`.

**3. Verify subject linkage.** `GET /api/edc/lab-batches/{batch_id}/results` → both rows have `subject_id` set (S01-001 was registered before ingest).

#### X10b. Re-upload (dedupe)

Re-POST the same file → returns existing batch with `was_new=False`. Confirm only 2 rows total in `GET /api/edc/deployments/{id}/lab-results`.

#### X10c. CDISC LAB tab-delimited

Save as `lab.tsv`:

```
STUDYID	SITEID	SUBJID	ACCSNNUM	LBTESTCD	LBTEST	LBORRES	LBORRESU	LBORNRLO	LBORNRHI	LBNRIND	LBDTC
STUDY1	01	S01-001	A1	CREAT	Creatinine	0.9	mg/dL	0.7	1.3	NORMAL	2026-05-31T11:05:00
STUDY1	01	S01-001	A1	BUN	Urea Nitrogen	18	mg/dL	7	20	NORMAL	2026-05-31T11:05:00
```

Upload with `source_format=cdisc_lab`.

#### X10d. FHIR R4 Bundle (listener endpoint)

Save as `obs.json`:

```json
{
  "resourceType": "Bundle",
  "type": "collection",
  "entry": [
    {
      "resource": {
        "resourceType": "Observation",
        "subject": { "reference": "Patient/S01-001" },
        "code": { "coding": [{"code": "TSH", "display": "TSH"}] },
        "valueQuantity": { "value": 2.1, "unit": "mIU/L" },
        "referenceRange": [{ "low": {"value": 0.4}, "high": {"value": 4.0} }],
        "effectiveDateTime": "2026-05-31T11:05:00Z"
      }
    }
  ]
}
```

POST to the listener endpoint with the right Content-Type:

```
POST /api/edc/deployments/{deployment_id}/lab-feed
Content-Type: application/fhir+json
<the JSON body>
```

#### X10e. SDTM LB cascade

Re-derive CDISC:

```json
POST /api/edc/deployments/{deployment_id}/cdisc/derive
```

→ Download `sdtm/lb.csv` from the Submissions card → confirm LB rows include the HL7 + CDISC LAB + FHIR results (USUBJID=`<study_id>-S01-001`, LBSEQ unique per subject, LBNRIND derived).

#### Sanity checks

- **SHA-256 dedupe** by `(deployment_id, content_hash)`.
- **Subject linkage.** `subject_code_hint` preserved when no Subject match yet; `POST .../lab-results/backfill-subjects` re-links after a Subject is created.
- **Empty payload → 422.** Unknown format → 422.
- **Listener Content-Type → source_format mapping.** `application/hl7-v2+er7` → hl7v2, `application/fhir+json` → fhir, `text/tab-separated-values` → cdisc_lab.
- **SDTM LB cascade additive.** Form-based `derive_lb` runs first; parsed labs append with `starting_seq_by_subject` continuation.

---

### X11 — Multi-site rollup dashboard (≈ 5 min)

**Demos.** Per-site KPIs across enrolment / queries / safety / operations.

**Pre-req.** A deployment with ≥2 sites + some screenings + queries + AEs + planned visits + lab batches.

**Open at:** `http://localhost:8000/multisite.html`

#### Steps

**1. Pick the SMOKE deployment from the deployment picker.**

**2. Each site card surfaces 4 KPI groups:**
- **Enrolment funnel** — screened / eligible / consented / enrolled.
- **Query backlog** — open / answered / closed.
- **Safety** — open AEs / open serious AEs / open deviations / open CAPAs.
- **Operational** — overdue visits / low-IP lots / last SDV timestamp.

**3. Tune the low-IP threshold** via the input field (default 10).

**4. Admin-only org rollup.** Switch to an admin account → an **Organisation rollup** section appears at the top with one card per deployment + org-wide totals. Click a deployment card → drills into its per-site view.

#### Sanity checks

- **Pure read-side aggregation** — no new tables, no new columns.
- **Open-AE definition.** `outcome NOT IN ('recovered', 'death')`. Recovering / not_recovered / unknown / default all count as open.
- **Overdue visit = `status='pending' AND window_end < now`.**
- **Org rollup gated on `portfolio.read_org`** (admin only).
- **Per-deployment rollup gated on `study.read`** at deployment scope.
- **`low_ip_lots` surface on totals row**, NOT per-site cards (IP is deployment-scoped today).

---

### X12 — Sign / lock / SDV / audit (≈ 5 min, partly API-only)

**Demos.** Form-instance signing → casebook sign-off → SDV → unlock voids signature with audit.

**Pre-req.** Completed FormInstance from X2.

```
POST  /api/edc/form-instances/{form_instance_id}/sign     {"meaning": "author", "password": "<re-auth>"}
POST  /api/edc/form-instances/{form_instance_id}/verify   (monitor, source-data verification)
POST  /api/edc/subjects/{subject_id}/sign                  {"meaning": "casebook"}
POST  /api/edc/form-instances/{form_instance_id}/unlock   {"reason": "correction"}
GET   /api/edc/form-instances/{form_instance_id}/signatures
GET   /api/edc/form-instances/{form_instance_id}/audit
```

#### Sanity checks

- **Signing requires password re-auth** (Part 11 §11.200) — verified against Cognito.
- **Signing locks the form.** PUT new ItemData → 409. Signature bound to data hash at sign time.
- **Unlock voids the signature** (`voided=True`) but keeps the row + writes a new AuditEntry.
- **Append-only audit.** No UPDATE or DELETE statements ever touch `AuditEntry`. DB-level constraint enforces this (E6). Try `UPDATE audit_entries …` in psql → rejected.
- **PHI segregation.** `docker exec research-assistant-clinical-postgres-1 psql -U cra -d cra_clinical -c "\dt"` shows Subject + FormInstance + ItemData + Signature + Query + AuditEntry **only** in the clinical DB.

---

### X13 — eCRF validation pack (IQ / OQ / PQ + study lock) (≈ 8 min)

**Demos.** "Deployable in an audited environment" gate — IQ snapshot + OQ pytest run + PQ runbook + ZIP bundle + study-level database lock.

#### Steps

**1. Lock the database (DM).**

```json
POST /api/edc/deployments/{deployment_id}/lock
{ "reason": "Pre-CSR database lock" }
```

→ tries to lock. If any non-closed queries exist, fails with 422 listing the queries. Override with `force_open_queries: true` (audited):

```json
POST /api/edc/deployments/{deployment_id}/lock
{ "reason": "Pre-CSR database lock", "force_open_queries": true }
```

**2. Try to write data while locked.** Attempt any PUT / POST against `/api/edc/.../data` → 409 with "Study deployment X is locked".

**3. View lock status + history:**

```
GET /api/edc/deployments/{deployment_id}/lock-status
GET /api/edc/deployments/{deployment_id}/lock-history
```

**4. Download the validation pack (admin).** From `admin.html` → Validation Pack section:

- **IQ.pdf** — package version + Python version + pinned dependencies from `uv.lock` + Cognito pool id + audit-trigger detection.
- **OQ.pdf** — shells out to `pytest` over the test node-ids declared in the Requirements Traceability Matrix (`validation/requirements_matrix.json`) and joins pass/fail with each requirement's regulatory anchor (Part 11 §, ICH E6, ICH E2A, ALCOA+).
- **PQ.pdf** — 8-step customer-side runbook (login → study create → form sign with reauth → study lock → IQ download → OQ run).
- **bundle.zip** — all three PDFs + the RTM JSON.

**5. Unlock (DM only):**

```json
POST /api/edc/deployments/{deployment_id}/unlock
{ "reason": "Found a missing AE — needs to be entered before CSR" }
```

#### Sanity checks

- **`study.lock` is DM-only.** PI attempt → 403 (separation of duties: PI signs casebook, DM locks DB).
- **Locked deployment refuses every mutation** — PUT data, POST sign, POST verify, POST AE record, POST deviation record, POST query raise, etc. all 409.
- **CDISC derivation is allowed post-lock** (lock-then-derive is regulator-default).
- **OQ requirement traceability.** Each row in the OQ PDF carries Part 11 § / ICH E6 / ICH E2A / ALCOA+ anchor.

---

## Phase 05 — Analysis & reporting

> **Phase context.** After database lock, the team runs the planned statistical analyses, assembles the CDISC submission bundle, writes the CSR, drafts the manuscript, and produces patient-facing materials.

### A1 — Trial-specific statistical analysis (KM + Cox + MMRM + binary + subgroup) (≈ 10 min)

**Demos.** 7-stage post-lock workflow, sandbox-backed, every result row carries a `derived_from` audit anchor.

**Trigger:**

```
/trial-stats run efficacy + safety analyses on the SGLT2i SMOKE-T2DM-001 ADaM bundle, primary endpoint HbA1c at Week 24
```

**Expected flow:** 7 stages — intake → analysis populations (ITT / mITT / PP / Safety) → time-to-event (Cox PH + K-M + log-rank) → continuous (MMRM with TRT×visit) → binary (Fisher + log-binomial GLM) → subgroup (per-subgroup Cox + interaction p + forest PNG) → assembled document.

Operator pastes ADSL + ADTTE + ADLB extracts at each stage; the model calls `run_trial_analysis(kind, payload)` which fans out to `kaplan_meier.py` / `mmrm.py` / `binary.py` / `subgroup_forest.py` in the sandbox.

#### Sanity checks

- **Every result row carries `derived_from = "sandbox:<kind>:<paramcd>"`.** HRs / LSMean diffs / CIs / p-values cannot be inline-asserted.
- **MMRM fits via statsmodels.MixedLM** with visit fixed effects + TRT × visit + optional BASE covariate.
- **Cox PH fits via statsmodels.duration.PHReg.**
- **Subgroup forest** PNG embedded in PDF; per-subgroup HR + interaction p surfaced.
- **`TrialStatsDocument.csr_artefact_ids`** is a `@property` the CSR drafter cites.

#### Cross-handoff

Click **→ Draft CSR from trial-stats** on the assembled card → seeds the CSR drafter (see A4) with the trial-stats document JSON.

---

### A2 — Beyond-forest-plot visualisations (funnel + waterfall + swimmer + GRADE chip) (≈ 8 min)

**Demos.** Four plot types across three specialists; sandbox-rendered diagnostics + host-rendered GRADE chip.

#### Funnel + Egger's test

Built into meta-analysis STEP 5 (E1 already exposes this). New `funnel.py` script uses `statsmodels.api.OLS` for Egger's regression. Each outcome with ≥3 studies gets `funnel_plot_image` + `eggers_p_value` + `funnel_interpretation`.

#### Waterfall (per-subject best response)

Trigger after a trial-stats workflow:

```
Add waterfall: per-subject HbA1c percent-change from baseline, threshold ±10%
```

→ `waterfall.py` script renders RECIST 1.1 categorised bars (CR / PR / SD / PD). `WaterfallResult.derived_from = "sandbox:waterfall:<outcome>"`.

#### Swimmer (treatment timeline)

```
Add swimmer: per-subject treatment timeline with response_onset / cr / progression event markers
```

→ `swimmer.py` renders horizontal bars with event markers + ongoing-treatment arrows.

#### GRADE chip table (host-side)

Auto-generated host-side from `GradeAssessment` rows (no agent involvement). Rows = outcomes; columns = 5 downgrade domains + 3 upgrade domains + computed certainty. Each cell colour-coded per the GRADE-pro convention. Embedded in GRADE PDF + DOCX.

#### Sanity checks

- **No new dependency.** `statsmodels` / `matplotlib` already pinned in sandbox.
- **Chip SVG hand-rolled with `html.escape`** for XML safety.
- **NMA geometry PNG** lives in the NMA specialist (E4), not here.

---

### A3 — CDISC SDTM → ADaM → TLF submission pipeline (≈ 10 min)

**Demos.** Full submission bundle — 7 SDTM domains + ADSL + ADTTE + TLF + Define-XML v2.1 + SAS Transport v5.

**Pre-req.** Smoke deployment with screenings + AEs + lab feeds + at least one FormInstance per domain.

#### Steps

**1. Map forms to domains (one-time per deployment, study_designer).** Configure `ItemMappingConfig.form_to_domain_map`:

```json
{
  "vital_signs": "VS",
  "labs": "LB",
  "exposure": "EX",
  "concomitant_meds": "CM",
  "medical_history": "MH",
  "demographics_form": "DM",
  "ae_form": "AE"
}
```

**2. Derive (DM):**

```
POST /api/edc/deployments/{deployment_id}/cdisc/derive
```

→ runs the pipeline: 7 SDTM domains + ADSL + ADTTE. Submission bundle assembled in-process.

**3. Render survival TLF (optional):**

```
POST /api/edc/deployments/{deployment_id}/cdisc/survival/render
```

→ sandbox runs statsmodels K-M + Cox PH stratified by TRT01A; writes K-M PNGs back as data-URI `TlfArtefact.svg_content` + a Cox PH summary table.

**4. Download the bundle.** Submissions card in collector.html → **Download bundle.zip**:

- `define.xml` at root (CDISC ODM/Define-XML 2.1 covering all 9 datasets with ItemGroupDefs + ItemDefs + CodeLists + MethodDefs).
- `define-overview.txt` (human summary).
- `sdtm/*.{csv,xpt}` × 7 (DM / AE / VS / LB / EX / CM / MH).
- `adam/{adsl,adtte}.{csv,xpt}`.
- TLF tables + figures.

#### Sanity checks

- **Hand-rolled XPT writer** — IBM-360 8-byte float conversion + 80-byte record alignment + 140-byte v5 namestr records. ASCII-only CHAR + NUM; 8-char column-name limit + 200-char string limit enforced.
- **No new dependency.** `xport` / `pyreadstat` deliberately avoided.
- **Define-XML v2.1** validates against the CDISC ODM schema.
- **ADSL.TRT01P / TRT01A** populated from `Allocation.arm` (S3) — no "TBD".
- **ADTTE rows** carry SRCDOM/SRCVAR audit anchors per regulator requirement.
- **MedDRA + WHODrug remain deploy-time gates** — PT / DECOD captured as free-text MVP.

---

### A4 — CSR (ICH E3) drafter (≈ 10 min)

**Demos.** ICH E3 synopsis + 4 data-driven sections (Disposition / Demographics / Efficacy / Safety) populated from ADSL + ADTTE + TLF. Strictest anti-hallucination posture in the codebase.

**Trigger** (or cross-handoff from A1 / A3):

```
/csr Phase 2 study of Empagliflozin in adults with T2DM, lock date 2026-05-31, double-blind, FDA submission
```

**Expected flow:** 4-stage workflow — intake → synopsis → 4 data sections → assembled document.

The data sections require the operator to paste source-artefact ids (e.g. `TLF t-disposition`, `ADTTE PARAMCD=TTAE`); every count, effect size, CI, p-value must cite its `derived_from` source. Narrative sections (Introduction / Discussion / Overall Conclusions) ship as `[Operator to complete]` placeholders.

#### Sanity checks

- **Every count / effect size / CI / p-value carries `derived_from`.** Try to type "HR 0.81 (0.71, 0.93)" inline without a source → validator rejects with "Effect sizes must cite an ADTTE / TLF / TrialStats artefact id."
- **Narrative placeholders honest.** Introduction / Discussion / Overall Conclusions show `[Operator to complete]` colour-coded amber.
- **Skeleton CSR carries all 16 ICH E3 section headers.**
- Continuation prefixes disambiguated (`CSR intake confirmed`, `CSR synopsis confirmed`, etc) so they don't collide with the generic registration prefix.

---

### A5 — Manuscript drafter (IMRaD) + reviewer-response (≈ 8 min)

**Demos.** Compose a meta-analysis into a journal-ready paper; iterate point-by-point on reviewer comments with a cover letter.

**Trigger** (or click `→ Draft as manuscript` on E1's terminal card):

```
/manuscript compose an IMRaD manuscript from my last meta-analysis, target NEJM
```

**Expected flow:** 3-stage workflow — intake → IMRaD draft → reviewer-response.

After Stage 2, paste a fake reviewer comment block:

```
Reviewer 1:
- The forest plot doesn't include a leave-one-out sensitivity analysis. Please add.
- Some included studies appear to have high risk of bias. How did this affect conclusions?

Reviewer 2:
- The discussion is too long. Please cut by 30%.
```

The drafter responds with **point-by-point response** + revised manuscript + cover letter. Targets: NEJM / Lancet / BMJ / JAMA / Annals / PLOS ONE / generic.

#### Sanity checks

- **Journal target enforced** — schema validates against the 7-value enum.
- **Cover letter auto-drafts** with sponsor + word count + financial disclosure placeholders.
- **PDF + DOCX export** bundles cover letter + point-by-point response + revised manuscript.

---

### A6 — Lay summary (3 variants: recruitment + evidence + results) (≈ 10 min)

**Demos.** Host-side Flesch-Kincaid + compute-and-iterate readability loop + multilingual.

#### A6a. Recruitment lay summary (from protocol)

```
/lay-summary plain-language recruitment material at grade 6 English for parents of children with asthma — Phase 2 RCT of inhaled corticosteroid X vs standard care
```

Intake = a protocol synopsis. Drafts a recruitment-language summary at the configured FK grade.

#### A6b. Evidence lay summary (from meta-analysis)

Click **→ Draft lay summary** on E1's meta-analysis terminal card. The handoff seed carries the meta-analysis JSON. The lay summary cites the underlying PMIDs verbatim.

#### A6c. Results lay summary (from CSR)

After A4 completes, trigger:

```
Draft lay summary from CSR
```

Carries the CSR's primary outcome summary + safety summary + `derived_from` ids forward.

#### Sanity checks

- **Host-side Flesch-Kincaid grade** computed in `services/readability.py` (stdlib only — no `textstat` dep). The model's self-reported grade is **overwritten** by the host's number.
- **Retry cap = 3.** When first draft scores > 8.0, the specialist re-prompts with simplification directive. `readability_attempts` records 1, 2, or 3.
- **AudienceProfile.** `target_grade` 4-12 (default 6); `language` en/es/fr/de; free-text `region`; `population_descriptor`.
- **5 CISCRP sections** schema-enforced.
- **Anti-hallucination per variant.** Evidence cites only from `pmid_sources`. Results cites only from `derived_from_ids`. Recruitment never promises trial success.
- **No medical-decision language.** "You should" / "you must" forbidden; "may help" / "might give doctors more information" allowed.
- **PDF + DOCX export** with reading-grade badge in cover band + glossary sidebar + "not medical advice" disclaimer footer.

---

### A7 — Citation manager (BibTeX + RIS round-trip) (≈ 5 min)

**Demos.** Pure file-format round-trip covering Zotero / EndNote / Mendeley.

#### Steps

**1. Export from Zotero / EndNote / Mendeley** as `.bib` or `.ris`. Or use this minimal `test.bib`:

```bibtex
@article{anderson2014,
  author = {Anderson, J. and Smith, K.},
  title = {Proton Pump Inhibitor Use in ACS},
  journal = {Lancet},
  year = {2014},
  pmid = {25040175}
}
```

**2. In a manuscript_drafter or sr_protocol intake card, drop the file on the `<CitationImporter>` drop-zone.** Or POST directly:

```
POST /api/citations/parse (multipart file)
```

→ returns `{citations: [...]}`.

**3. Inline citations** in the next chat message; the specialist uses them as bibliography seed.

**4. Export back.** From the terminal card → **Export bibliography** → choose BibTeX or RIS → downloads file.

#### Sanity checks

- **Stdlib-only parsers** — no `bibtexparser` / `pyzotero` / `rispy` dependency.
- **5MB upload cap.**
- **Round-trip preservation.** `article` / `book` / `incollection` / `inproceedings` / `techreport` / `phdthesis` / `misc` map across BibTeX / RIS via `_BIBTEX_TYPE_BY_RIS_TY` / `_RIS_TY_BY_BIBTEX_TYPE`.
- **Unknown BibTeX fields preserved** through export via `raw_fields`.
- **PMID detection from RIS** `AN` / `ID` / `PM` tags.

---

## Phase 06 — Cross-cutting infrastructure

> **Phase context.** Behind every phase is the same underlying platform — auth, RBAC, library + retrieval, sandboxing, portfolio + budget tracking, and configuration.

### C1 — Portfolio dashboard (≈ 3 min)

**Open at:** `http://localhost:8000/portfolio.html`

#### What to demo

- **Hero cards** — total threads / SR projects / deployments / top workflow.
- **Recent threads** table with cost per thread + workflow pill + last activity.
- **SR projects** rollup (owned + sr_review-membership combined).
- **eCRF deployments** rollup resolved through `RoleAssignment` scope (global / study / site).
- **Admin org section** auto-appears for admin accounts (gated on `portfolio.read_org`).

#### Sanity checks

- **Researcher sees own data only.** Non-admin → org section hidden.
- **No row-level data leaked in org rollup** — only counts + roles.
- **`last_turn_kind` peeks at the most recent assistant Message.final_answer JSON** for the kind discriminator.

---

### C2 — Budget + cost rollup (≈ 3 min)

Same `portfolio.html` page → "Spend (USD)" section.

#### What to demo

- **Four hero cards** — This month / Cumulative / Turns / Tokens.
- **By-workflow breakdown** — per-specialist USD spend.
- **By-model-family breakdown** — Haiku / Sonnet / Opus split.
- **Cost column on threads table.**
- **Admin-only Top-spend users** section.

#### Sanity checks

- **Pricing-table-as-code** in `config/bedrock_pricing.py` — Claude 4.x (Haiku 4.5 / Sonnet 4.6 / Opus 4.7) + legacy 3.x.
- **Pricing provenance documented** in UI ("AWS Bedrock public rates as of 2026-05. Verify before invoicing.").
- **Six-decimal rounding** in `CostRollup.as_dict()`.
- **Legacy events without `model_id` parsed gracefully** — fall back to Sonnet default at lookup (conservative overestimate).

---

### C3 — RBAC role admin (≈ 5 min)

**Open at:** `http://localhost:8000/admin.html`

#### What to demo

- **Grant role to user** — pick user, role, scope (global / study / site / sr_review), scope_id.
- **Revoke role** — list assignments; click revoke; row deletes.
- **Effective permissions** — query `effective_permissions_for_sub(sub)` → shows the permission set the user actually carries.
- **Frontend gating** — sidebar links + welcome-page tiles disappear when the user lacks the corresponding permission.

#### Sanity checks

- **Three-tier scope.** `global ⊃ study ⊃ site`. Broader scope satisfies narrower checks.
- **`sr_review` scope independent.** A `study` grant doesn't satisfy an `sr_review` check.
- **`require_permission_scoped(perm, resource_param=...)`** — resolves the resource id off the request path, runs through the right resolver, asks the RBAC module whether the caller's permissions cover it.
- **Foreign rows 404 not 403** to avoid id leaks.

---

### C4 — Library + RAG (≈ 5 min)

**Open at:** `http://localhost:8000/library.html`

#### What to demo

- **Library list** — every abstract + full-text article pulled across reviews.
- **Search box** runs `rag_search` (hybrid BM25 + dense vector via Titan v2 embeddings).
- **Upload a PDF** → parses + embeds + appears in the library.
- **Re-import lineage** — same paper across multiple reviews shows as one library entry with N citing threads.

#### Sanity checks

- **HNSW index** (Postgres + pgvector) for vector search.
- **PDF upload** uses pypdf for parsing.
- **Embedding dimension** = 1024 (Titan v2). Must match `models.EMBEDDING_DIM`.
- **RAG hits surface in any specialist** that has `rag_search` registered (meta_analysis, sr_protocol, general_qa).

---

### C5 — General clinical Q&A (≈ 3 min)

**Demos.** Anti-hallucination validator on the general-purpose chat surface.

#### Steps

**1. Ask a defensible question:**

```
/ask what does a forest plot show in a meta-analysis?
```

→ background explanation citing methodology textbook patterns; no PMIDs, no effect sizes.

**2. Try to get a fabricated effect size:**

```
What's the pooled HR for SGLT2 inhibitors on cardiovascular mortality?
```

→ `_reject_clinical_synthesis` validator fires. The response refuses to quote a numeric HR and redirects to the meta-analysis specialist.

**3. Try to get a fabricated PMID:**

```
Give me five PMIDs for landmark sepsis trials.
```

→ validator fires. Response says it can't emit PMIDs from training data; suggests running `/search` instead.

#### Sanity checks

- **`_reject_clinical_synthesis` regex** blocks effect-size patterns (HR / OR / RR + CIs + p-values).
- **PMID rule** blocks any 8-digit PMID-like number that didn't come from a `search_papers` tool result in the same thread.
- **Guideline-citation regex** blocks "per [guideline] §[x.y]" claims without a tool-result source.

---

## Appendix — All paste blocks (cheat sheet)

For when you just want every paste block in one place, in demo order.

### E1 — Meta-analysis on synthetic data

```
Does adding a proton pump inhibitor to dual antiplatelet therapy reduce upper GI bleeding in patients who recently underwent PCI for acute coronary syndrome?
```

```markdown
| Element | Value |
|---|---|
| Population | Adults post-PCI for ACS, on dual antiplatelet therapy (aspirin + P2Y12 inhibitor) |
| Intervention | PPI added to DAPT (any PPI, any dose, ≥30 days) |
| Comparator | DAPT alone or DAPT + placebo |
| Outcome (primary) | Upper GI bleeding events during follow-up |
| Study design | Randomized controlled trials |
```

```markdown
| Study | Year | Design | PPI events | PPI total | Control events | Control total | Follow-up (mo) |
|---|---|---|---|---|---|---|---|
| Anderson et al. | 2014 | Multicenter RCT | 12 | 1200 | 28 | 1198 | 12 |
| Bekele et al. | 2016 | Single-center RCT | 4 | 320 | 9 | 318 | 6 |
| Chen et al. | 2018 | Multicenter RCT | 18 | 2200 | 41 | 2180 | 12 |
| Diaz et al. | 2019 | Pragmatic RCT | 7 | 850 | 14 | 845 | 9 |
| Eriksen et al. | 2020 | RCT | 5 | 560 | 7 | 562 | 6 |
| Faruq et al. | 2021 | Multicenter RCT | 10 | 1400 | 25 | 1395 | 12 |
| Garcia et al. | 2022 | RCT | 3 | 480 | 8 | 478 | 6 |
| Huang et al. | 2023 | Multicenter RCT | 14 | 1800 | 33 | 1810 | 12 |
```

### E1 variant — high heterogeneity

```markdown
| Study | Year | Design | PPI events | PPI total | Control events | Control total | Follow-up (mo) | Approx RR |
|---|---|---|---|---|---|---|---|---|
| Anderson et al. | 2014 | Multicenter RCT | 8 | 1200 | 20 | 1198 | 12 | 0.40 |
| Bekele et al. | 2016 | Single-center RCT | 3 | 400 | 12 | 395 | 6 | 0.25 |
| Chen et al. | 2018 | Multicenter RCT | 15 | 1500 | 25 | 1495 | 12 | 0.60 |
| Diaz et al. | 2019 | Pragmatic RCT | 22 | 600 | 12 | 605 | 9 | 1.85 |
| Eriksen et al. | 2020 | RCT | 4 | 700 | 16 | 695 | 6 | 0.25 |
| Faruq et al. | 2021 | Multicenter RCT | 18 | 900 | 14 | 895 | 12 | 1.28 |
| Garcia et al. | 2022 | RCT | 5 | 800 | 19 | 795 | 6 | 0.26 |
| Huang et al. | 2023 | Multicenter RCT | 11 | 1300 | 24 | 1305 | 12 | 0.46 |
```

### E2a — Protocol triggers

```
/protocol SGLT2 inhibitors for HF prevention in T2DM
```

```
Help me draft a PRISMA-P protocol on whether SGLT2 inhibitors reduce heart-failure hospitalization in adults with type 2 diabetes.
```

### E2b — Search-strategy triggers

```
/search proton pump inhibitor + DAPT in post-PCI ACS
```

### E2d — RoB standalone trigger

```
/rob Run RoB 2.0 on PMID 20925534, PMID 30873575, PMID 31091374
```

### E4 — NMA trigger

```
/nma compare five direct oral anticoagulants for stroke prevention in non-valvular AF — apixaban, dabigatran, edoxaban, rivaroxaban, warfarin
```

### E5 — IPD trigger + bundle

```
/ipd individual patient data meta-analysis on statins for primary prevention of cardiovascular events
```

```
Trial: ASCOT-LLA
subject_id,treatment,outcome,age,sex
S001,1,0,55,M
S002,1,1,62,M
S003,0,1,58,F
S004,0,0,49,M

Trial: WOSCOPS
subject_id,treatment,outcome,age,sex
W001,1,0,60,M
W002,0,1,67,M
W003,1,0,54,F
W004,0,0,52,F
```

### E6 — GRADE trigger

```
/grade Generate a GRADE SoF for the PPI/DAPT meta-analysis
```

### D1 — SAP drafter trigger

```
/sap two-arm RCT of CBT vs SSRI for depression, primary outcome HAM-D at 12 weeks
```

### S1 — Registration trigger

```
/register Phase 2 SGLT2i in HFpEF, double-blind randomised parallel, primary outcome KCCQ Total Symptom Score at week 24
```

### S2 — IRB trigger

```
/irb Phase 2 trial of Drug X in disease Y, US IRB, language English, reading grade 8
```

### X1 — Protocol paste for eCRF drafter

```
Phase 2, single-arm, open-label study of an SGLT2 inhibitor in adults with type 2 diabetes mellitus.

Eligibility:
- Adults aged 18–75 years.
- HbA1c 7.0–10.0% at screening.
- On stable metformin ≥3 months prior to enrolment.
- BMI 22–40 kg/m².

Schedule of assessments:
- Visit 1 (Baseline, Week 0): consent, demographics, vital signs, fasting glucose, HbA1c, body weight, eligibility verification, study-drug dispensing.
- Visit 2 (Week 4): vital signs, fasting glucose, AE review.
- Visit 3 (Week 12): vital signs, fasting glucose, HbA1c, body weight, AE review, lab safety panel.
- Visit 4 (Week 24, primary endpoint): vital signs, fasting glucose, HbA1c, body weight, AE review, lab safety panel.
- Visit 5 (EOS, Week 28): final vital signs, AE review, treatment-emergent serious AE inventory.

Primary outcome: change in HbA1c from baseline to Week 24.
Secondary outcomes: fasting glucose, body weight, proportion with HbA1c <7.0% at Week 24.
Safety outcomes: vital signs, lab safety panel, treatment-emergent adverse events (graded per CTCAE v5).

Patient-reported outcomes (ePRO, weekly):
- Diabetes Treatment Satisfaction Questionnaire (DTSQ, 8 items).
- Self-reported hypoglycaemia events (frequency + severity).
```

### X10 — Lab feed samples

HL7 v2 ORU^R01:

```
MSH|^~\&|LAB|HOSP|EDC|TRIAL|20260531120000||ORU^R01|MSG1|P|2.5
PID|||S01-001
OBR|1||ACC123|CBC^Complete Blood Count^L|||20260531110000
OBX|1|NM|HGB^Hemoglobin^L||14.2|g/dL|13.0-17.0|N|||F
OBX|2|NM|GLUC^Glucose^L||102|mg/dL|70-100|H|||F
```

CDISC LAB:

```
STUDYID	SITEID	SUBJID	ACCSNNUM	LBTESTCD	LBTEST	LBORRES	LBORRESU	LBORNRLO	LBORNRHI	LBNRIND	LBDTC
STUDY1	01	S01-001	A1	CREAT	Creatinine	0.9	mg/dL	0.7	1.3	NORMAL	2026-05-31T11:05:00
STUDY1	01	S01-001	A1	BUN	Urea Nitrogen	18	mg/dL	7	20	NORMAL	2026-05-31T11:05:00
```

FHIR R4:

```json
{
  "resourceType": "Bundle",
  "type": "collection",
  "entry": [
    {
      "resource": {
        "resourceType": "Observation",
        "subject": { "reference": "Patient/S01-001" },
        "code": { "coding": [{"code": "TSH", "display": "TSH"}] },
        "valueQuantity": { "value": 2.1, "unit": "mIU/L" },
        "referenceRange": [{ "low": {"value": 0.4}, "high": {"value": 4.0} }],
        "effectiveDateTime": "2026-05-31T11:05:00Z"
      }
    }
  ]
}
```

### A1 — Trial-stats trigger

```
/trial-stats run efficacy + safety analyses on the SGLT2i SMOKE-T2DM-001 ADaM bundle, primary endpoint HbA1c at Week 24
```

### A4 — CSR trigger

```
/csr Phase 2 study of Empagliflozin in adults with T2DM, lock date 2026-05-31, double-blind, FDA submission
```

### A5 — Manuscript trigger + reviewer comments

```
/manuscript compose an IMRaD manuscript from my last meta-analysis, target NEJM
```

```
Reviewer 1:
- The forest plot doesn't include a leave-one-out sensitivity analysis. Please add.
- Some included studies appear to have high risk of bias. How did this affect conclusions?

Reviewer 2:
- The discussion is too long. Please cut by 30%.
```

### A6 — Lay summary trigger

```
/lay-summary plain-language recruitment material at grade 6 English for parents of children with asthma — Phase 2 RCT of inhaled corticosteroid X vs standard care
```

### A7 — Sample BibTeX

```bibtex
@article{anderson2014,
  author = {Anderson, J. and Smith, K.},
  title = {Proton Pump Inhibitor Use in ACS},
  journal = {Lancet},
  year = {2014},
  pmid = {25040175}
}
```

### Slash commands

```
/protocol, /sr, /prisma           → sr_protocol specialist
/search, /strategy                → search_strategy specialist
/meta, /meta-analysis, /ma        → meta_analysis specialist
/rob, /bias                       → risk_of_bias specialist
/nma, /network-ma                 → nma specialist
/ipd, /ipdma                      → ipd specialist
/grade, /sof, /prisma-checklist   → grade_drafter specialist
/sap, /samplesize, /power         → sap_drafter specialist
/register, /ctgov, /euctr, /ctis  → registration_drafter specialist
/irb, /icf, /consent              → irb_drafter specialist
/manuscript, /imrad               → manuscript_drafter specialist
/csr, /e3, /study-report          → csr_drafter specialist
/trial-stats, /efficacy, /km, /mmrm → trial_stats specialist
/lay, /lay-summary, /pls          → lay_summary specialist
/general, /ask                    → general_qa specialist
```

### Server commands

Clean restart of the whole stack:

```
docker compose -f deploy/compose/docker-compose.yml down
docker compose -f deploy/compose/docker-compose.yml up -d
```

Tail the agent logs:

```
docker compose -f deploy/compose/docker-compose.yml logs -f agent
```

Rebuild + recreate the agent container after a code change (Postgres untouched):

```
docker compose -f deploy/compose/docker-compose.yml up -d --build agent
```

Restart just the agent container (no rebuild — only useful for transient process state, not code changes):

```
docker compose -f deploy/compose/docker-compose.yml restart agent
```

### Verify PHI segregation

```
docker exec research-assistant-clinical-postgres-1 psql -U cra -d cra_clinical -c "\dt"
docker exec research-assistant-postgres-1           psql -U cra -d cra            -c "\dt"
```

Subject / FormInstance / ItemData / Signature / Query / AuditEntry / Allocation / AdverseEvent / ScreeningLog / PlannedVisit / InvestigationalProduct / LabResult / etc. should only appear in the clinical DB.

---

## Walking a customer through a phase

If you're demoing to a researcher / sponsor / institution, pick the phase that matches their pain point and run the demos in order:

- **A researcher writing a SR / meta-analysis** → Phase 01 demos E1 → E2 → E3 → E6 (≈ 35 min).
- **A guideline committee / HTA body** → Phase 01 demos E2 → E7 → E8 → E6 (≈ 30 min).
- **A trial PI preparing to submit a protocol** → Phase 02 D1 → Phase 03 S1 → S2 → S3 (≈ 35 min).
- **A site coordinator / data manager running an active trial** → Phase 04 X1 → X2 → X4 → X5 → X6 → X7 → X8 → X10 → X11 (≈ 60 min).
- **A biostatistician + medical writer at database lock** → Phase 04 X13 → Phase 05 A1 → A3 → A4 → A5 (≈ 45 min).
- **A patient-engagement / comms team** → Phase 05 A6 + Phase 01 E2 → E6 (≈ 25 min).
- **An institutional admin** → Phase 06 C1 → C2 → C3 → Phase 04 X11 → X13 (≈ 30 min).

For a 15-minute "elevator demo" pick one from each phase: E1 → D1 → S2 → X2 → A4 → C2. Shows the lifecycle end-to-end with the cross-handoff story.

---

## Functional-test mode

Every demo doubles as an end-to-end functional test. To run the full surface as an acceptance check:

1. Clean restart (Setup).
2. Walk E1 through C5 in order, executing each demo's **Steps**.
3. Confirm every **Sanity check** passes.
4. Note any **Failure modes** that fire unexpectedly.

A complete pass takes ~3.5 hours and exercises every shipped specialist, every eCRF endpoint, the SDTM/ADaM/TLF pipeline, the validation pack, the RBAC matrix, and the dashboards. Failure-mode coverage is intentional — most regressions surface here, not in the unit suite.
