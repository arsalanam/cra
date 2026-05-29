# Demo Guide — Clinical Research Assistant

Presenter playbook. Every paste target lives in a fenced code block — click the copy icon, drop it into the chat input, move on.

**Four demo paths:**
- **Path A** — Meta-analysis on canned synthetic data (≈ 5 min, most reliable, deterministic-ish forest plot).
- **Path B** — Real evidence pipeline: Protocol → Search → Meta → RoB (≈ 15 min, shows the full upstream→downstream story).
- **Path C** — Living-review watch (≈ 5 min setup, then background; demos the scheduler + materiality-alert loop).
- **Path D** — eCRF / EDC end-to-end: AI-draft forms from a protocol → review/edit → publish → site EDC capture → participant ePRO capture (≈ 10 min). Demos the regulatory-grade data-capture subsystem; PHI lives in a separate Postgres.

Skip to the [paste-block cheat sheet](#appendix--all-paste-blocks-cheat-sheet) if you just need the raw blocks.

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

In the browser at `http://localhost:8000/`, do a **hard-refresh** (`Ctrl+F5`) so the sidebar bell, watches link, and the latest specialist cards load. Log in via Cognito if prompted.

### AWS quota note

Path C's demo cadence (`*/2 * * * *`) issues ~30 Bedrock calls/hour. Switch any demo watches to a weekly schedule when you're done, or delete them — otherwise they'll keep firing.

---

## Path A — Meta-analysis on synthetic data (≈ 5 min)

**Goal:** 8-row forest plot, pooled RR ≈ **0.44** (95% CI ~ 0.33–0.58), I² in the **30–50%** range. Most reliable path because the synthetic data is deterministic and doesn't depend on live PubMed indexing.

> The numbers below are **fabricated** to give a plausible meta-analysis result. Author names are made up so this can never be confused with real evidence.

### Steps

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
>
> Same trap exists at the two earlier confirmation steps: the PICO card's confirm button emits `PICO confirmed …`, and the Study Selection card's button emits `Selected studies for data extraction …`. Always click the button on the card, never paraphrase in the chat input.

**6. On the Meta-Analysis Results card, click "Download PDF" or "Download DOCX" to grab a manuscript-style report.**

The report bundles, in this order: research question · PICO table · included studies table · per-outcome pooled estimate (RR, 95% CI, I², heterogeneity p) · embedded forest plot · clinical interpretation · summary · caveats. The buttons hit `GET /api/threads/{thread_id}/report/meta_analysis/{pdf|docx}` with auth cookies — the browser writes the file to disk (no inline-render). DOCX is the right choice if the reviewer is going to edit the wording into a paper; PDF is the right choice for read-only sharing.

The same **Download PDF / DOCX** panel now appears on the final cards of Paths B1 (SR/MA protocol) and B4 (Risk of Bias) — see those steps. URL pattern is `GET /api/threads/{thread_id}/report/{meta_analysis|sr_protocol|rob}/{pdf|docx}`.

### Sanity checks

- Pooled RR ≈ **0.44** (95% CI roughly 0.33–0.58), random-effects.
- I² in the **30–50%** range — some between-study variation, not extreme.
- 8 study rows + diamond. Plot well under sandbox limits (figsize / dpi / 2 MB).
- No subgroup splits needed — keeps the matplotlib code minimal.

### Variant — stress-test heterogeneity (optional)

Swap the step-4 extraction with this table to force I² ≈ **75–85%** and a wide pooled CI. Same PICO. Two outliers (Diaz 2019, Faruq 2021) favor control — pretend they used different PPI dosing or a sicker subgroup.

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

(Totals: PPI 86 / 7400 (1.16%) vs Control 142 / 7383 (1.92%).)

What to look for:
- I² lands in the **70–85%** range, clearly flagged as high heterogeneity.
- Forest-plot CIs visibly fail to overlap a single common effect.
- Excluding Diaz + Faruq (sensitivity analysis) drops I² sharply and tightens the pooled estimate toward RR ≈ 0.4.
- Narrative tempers conclusions ("substantial heterogeneity precludes strong recommendation…").

---

## Path B — Real evidence pipeline: Protocol → Search → Meta → RoB (≈ 15 min)

**Goal:** show the system's coherence — PICO carries through every handoff, so the protocol's eligibility criteria match the search strategy, which matches the meta-analysis inclusion set, which matches the RoB cohort.

### B1. Draft a PRISMA-P protocol

**Trigger** (either):

```
/protocol SGLT2 inhibitors for HF prevention in T2DM
```

```
Help me draft a PRISMA-P protocol on whether SGLT2 inhibitors reduce heart-failure hospitalization in adults with type 2 diabetes.
```

**Expected flow:**

1. `protocol_methods` card — title, review type, PICO (MeSH-verified), eligibility (inclusion/exclusion/study designs/language/dates/age/setting), info sources, RoB tool + rationale, effect-measures grid keyed by outcome, synthesis plan, GRADE checkbox. Edit any field → click **Methods confirmed — draft full protocol**.
2. `protocol_document` (draft) — grounded background with `[N]` citations, references list (origin badge + PMID/DOI/URL), PROSPERO field map with per-row Copy buttons, full Markdown (Show/Hide + Copy markdown). Iterate by typing free-form refinements ("expand the background", "add a sensitivity analysis for high-RoB studies") or click **Finalize protocol**.
3. `protocol_document` (final, `is_final=true`) adds two handoff buttons: **Build the search strategy** and **Skip to meta-analysis**, plus a **Take it away** download panel.
4. On the final card, click **Download PDF** or **Download DOCX** to grab the PRISMA-P-shaped protocol — title · research question · PICO · eligibility · information sources · RoB plan · effect-measures plan · synthesis plan · grounded background · numbered references with origin badges · PROSPERO field map (amber rows = `[USER INPUT NEEDED]`). Hits `GET /api/threads/{tid}/report/sr_protocol/{pdf|docx}`.

**Sanity checks:**

- **RoB auto-suggest** — `study_designs = ["Randomized Controlled Trial"]` only → defaults to `RoB 2.0` with an RCTs-only rationale. Add "Cohort study" and re-run methods → switches to ROBINS-I.
- **Citation provenance** — every reference card has an origin badge: `search_papers` (cyan), `web_search` (blue), `wikipedia` (amber). PubMed-sourced refs MUST display a PMID; web sources MUST display a URL.
- **No fabricated numbers** — background should not contain percentages, sample sizes, or effect estimates that aren't tied to a `[N]` citation.
- **PROSPERO placeholders are honest** — user-specific fields (Named contact, Funding sources, Country, IRB number, dates) display content starting with `[USER INPUT NEEDED:` styled in amber. No invented emails or grant IDs.
- **Effect measures match outcomes** — every key in the effect-measures grid equals an outcome in the PICO. Editing PICO outcomes should not orphan rows.
- **Handoff seeds work** — **Build the search strategy** drops you into a `search_strategy` thread whose first turn is `query_blocks` already populated with the MeSH-verified PICO concepts. **Skip to meta-analysis** drops into a `meta_analysis` thread that skips PICO build and proceeds toward search/extraction.

**Expected for the SGLT2 canonical case** (sanity targets):

- `review_type = intervention`; PICO with `population` ≈ "Adults ≥18 with T2DM", `intervention` = "SGLT2 inhibitor", `outcomes` including "Heart-failure hospitalization", "All-cause mortality", "MACE".
- Eligibility: `study_designs = ["Randomized Controlled Trial"]`, English, ≥12 weeks follow-up.
- `rob_tool.tool = RoB 2.0` (RCTs-only).
- `effect_measures_plan` populated with `RR` for each binary outcome.
- `synthesis_plan.primary_method = random_effects_meta`, planned subgroups by baseline HbA1c / prior HF status.
- Background covers T2DM burden + landmark trials (EMPA-REG / CANVAS / DECLARE / DAPA-HF — names come from real `search_papers` results) + guideline statements (ESC/AHA/ADA). 5–10 references, mostly `origin=search_papers` with PMIDs.
- Full Markdown ~1500–2500 words, copy-paste-ready into PROSPERO / a journal portal / an IRB form.

### B2. Build the search strategy

From B1, click **Build the search strategy**. Or standalone (either trigger works):

```
/search proton pump inhibitor + DAPT in post-PCI ACS
```

```
Build me a PubMed search strategy for: does adding a PPI to DAPT reduce upper GI bleeding in post-PCI ACS patients?
```

**Expected flow:**

1. `query_blocks` card — PICO concepts with MeSH terms + free-text synonyms per block. Edit synonyms, drop unwanted MeSH, set the target hit-count band (default 50–500). Click **Confirm terms**.
2. `strategy_result` (draft) — composed Boolean query, PubMed + Europe PMC hit counts, copy-paste plans for Cochrane CENTRAL + Embase, 5 sample hits, 2–3 refinement buttons (e.g. "Restrict to last 10 years"). Click a refinement → the agent re-runs and returns a fresh draft.
3. Repeat stage 2 until the band-status banner turns green ("inside target band"). Click **Finalize strategy**.
4. `strategy_result` (final, `is_final=true`) — no refinement suggestions; adds **Run meta-analysis on these results** and **Watch this query** buttons.

**Sanity checks:**

- **MeSH UIDs everywhere** — every MeSH chip on `query_blocks` shows a UID (e.g. "Proton Pump Inhibitors UID D054328"). A bare descriptor without a UID means `mesh_lookup` was skipped.
- **Embase caveat present** — the Embase plan card MUST display "⚠ Emtree terms are unverified — confirm against Embase Emtree thesaurus before running." Missing → prompt rule violated.
- **Hit-count honesty** — PubMed + Europe PMC show real numbers; Cochrane CENTRAL + Embase show "not executed here — copy & run manually" instead of a fabricated count.
- **Query identity** — the `composed_query` shown in the PubMed plan card is byte-identical to what was passed to `search_papers` (verify in server logs under the `search_papers` tool-call args).

### B3. Hand off to meta-analysis

Click **Run meta-analysis on these results** on the final `strategy_result`. The new thread arrives at its PICO step already understood; STEP 3's search query is the validated string (or a close refinement).

If you want a deterministic forest plot for the talk track, drop in Path A's synthetic extraction at this point — the audience won't see the seam.

### B4. Assess risk of bias

From a meta-analysis `data_extraction` card, click **Assess risk of bias** — a fresh thread spawns pre-loaded with the extracted studies (PMIDs + titles + designs + abstracts) plus PICO context.

Standalone trigger:

```
/rob Run RoB 2.0 on PMID 20925534, PMID 30873575, PMID 31091374
```

**Expected flow:**

1. `rob_assessments` card — one section per study, 5 (RoB 2.0) or 7 (ROBINS-I) domains, color-coded judgment pills, expandable justifications.
2. Edit any judgment via the dropdown — the **overall** judgment per study auto-recomputes by the worst-domain rule.
3. Click **RoB confirmed — generate summary**.
4. `rob_summary` card — stacked-bar plot, per-domain count table, narrative interpretation, sensitivity recommendations naming any high-RoB PMIDs as chips.
5. Click **Finalize RoB** → `is_final=true` adds **Re-run meta-analysis excluding high-RoB studies** plus a **Take it away** download panel. Clicking the re-run button spawns a fresh meta-analysis thread with the validated PICO + high-RoB PMIDs explicitly excluded from STEP 2.
6. On the final card, click **Download PDF** or **Download DOCX** to grab the RoB report — embedded stacked-bar summary plot · per-domain distribution table · narrative interpretation · per-study × per-domain assessments with verbatim quotes and colour-coded judgments · sensitivity recommendations · high-RoB PMID list. Hits `GET /api/threads/{tid}/report/rob/{pdf|docx}`.

**Sanity checks:**

- **Tool auto-pick from study designs** — all RCTs → **RoB 2.0**. Mix in a cohort study → switches to **ROBINS-I**. The pill at the top of the card shows the active tool.
- **`no_information` is honest** — expect 1–3 domains per study judged `no_information` (grey pill). Most abstracts don't describe randomization method, allocation concealment, or pre-registered analysis plans. Justifications should say "Abstract does not describe X" — never invent a description.
- **Quotes are verbatim** — italic blue quote blocks must appear word-for-word in the linked abstract; click the PMID to cross-check.
- **Worst-domain overall** — any `high` → overall `high`; any `some_concerns` (no `high`) → overall `some_concerns`; all `low` → overall `low`. `no_information` on a critical domain bumps overall to at least `some_concerns`.
- **Plot < 2 MB** — if it can't generate, the summary card shows "Summary plot was not produced" rather than crashing.
- **Sensitivity handoff** — with at least one overall-`high` study, the **Re-run meta-analysis excluding high-RoB studies** button is enabled. With none, it's disabled and the CTA reads "No high-RoB studies to exclude."
- **PICO carry-through** — when launched from a `data_extraction` handoff, the sensitivity meta-analysis thread does NOT re-ask for PICO. Standalone-launched RoB threads will lack PICO context and ask for it.

**Note on the synthetic Path A cohort:** if you run RoB on those studies, expect most domains to be `no_information` — they have no real abstracts, and the agent correctly finds nothing rather than fabricating judgments. For a substantive RoB demo, use real PMIDs (e.g. **PMID 20925534** for COGENT).

---

## Path C — Living-review watch (background)

**Goal:** show the scheduled re-search + materiality-alert loop. Setup is ~5 minutes; the rest is background.

### Architecture in one paragraph

`POST /api/watches` runs the saved search once to populate the baseline PMID set, persists the watch, and registers an APScheduler cron job. At each fire, `services/watch_runner.py` re-executes the search via `tools/clinical/search_papers._fan_out`, computes the diff against `baseline_pmids_json`, hands new papers to `agent/specialists/watch_triage.py` (one Bedrock call returning a `WatchRunSummary`), persists a `WatchRun`, and creates a `Notification` if `notify=true`. The bell polls `/api/notifications/unread-count` every 30 s. On server restart, active watches are re-registered from the DB (APScheduler's MemoryJobStore loses jobs; the DB is the source of truth).

### Steps

1. Build a search strategy end-to-end (Path B2) to a finalized `strategy_result`.
2. On that card, click **Watch this query**.
3. In the modal:
   - **Name** — pre-filled from the original research question (editable).
   - **Schedule preset** — pick **Every 2 minutes (demo)** to see the loop without waiting.
   - **Materiality threshold** — leave at `0.6`.
   - Click **Create watch**.
4. Click sidebar **▤ Watches** → the new watch appears with status `active`, baseline = 5 PMIDs, cron `*/2 * * * *`, next-run timestamp ~2 minutes out.
5. Wait 2 minutes (or click **Run now** for an immediate trigger). The page doesn't auto-refresh — reload to see the run row.
6. Click **Show runs** to expand history. Outcomes:
   - `no_change` — same baseline PMIDs (most likely on a 2-min cadence; nothing new indexed since the snapshot).
   - `success` — new PMIDs surfaced; triage results render with relevance / design-fit pills and a materiality score.
7. If `success` AND any triage hit `materiality ≥ 0.6`, the bell badge lights up. Click → dropdown; click a notification → marks read + jumps to `/watches.html`.

### Sanity checks

- **Baseline pre-populated** — watch-creation logs show `pre-populated baseline with N PMIDs from initial search`. The first scheduled run shouldn't surface dozens of "new" papers.
- **Triage pills are honest** — `relevance=high` + `design_fit=matches` + `materiality > 0.6` reserved for clearly in-scope, well-powered studies. If everything is 0.8+, the prompt is too lenient (tighten in `agent/specialists/watch_triage.py`).
- **No fabricated effect sizes** — the triage summary should NOT quote numbers the abstract didn't report; cross-check against the linked PMID.
- **Paused watches don't fire** — pause one and confirm no new runs appear after its next scheduled fire time.
- **Cascade delete** — deleting a watch removes its runs + notifications; the bell badge drops accordingly.
- **Restart durability** — restart the agent container (`docker compose -f deploy/compose/docker-compose.yml restart agent`); the startup log stream should show `Scheduler re-registered N active watch(es) from DB`. The schedule survives even though APScheduler's MemoryJobStore loses jobs across restarts — the DB is the source of truth.

### Failure modes

- **AWS quota** — one Bedrock call per fire; `*/2 * * * *` = 30 calls/hour. Switch to weekly for anything but a single demo.
- **Bad cron** — `POST /api/watches` validates via `CronTrigger.from_crontab`; malformed crons return 400 with the parser error in a bell-styled error message.
- **All sources disabled** — if every paper source is off in `/admin.html`, the runner records `status=error` with the "no sources enabled" message.
- **Process restart between fires** — `coalesce=True`, so missed fires collapse to one fire on next startup.

### REST surface

```
GET/POST/PATCH/DELETE  /api/watches
GET                    /api/watches/{id}/runs
POST                   /api/watches/{id}/run-now
GET                    /api/notifications
POST                   /api/notifications/{id}/read
GET                    /api/notifications/unread-count
```

---

## Path D — eCRF / EDC end-to-end (≈ 10 min)

**Goal:** show the regulatory-grade data-capture subsystem: AI-draft CRFs from a protocol, edit them in a designer UI, publish into immutable definitions, then capture data through both the site EDC surface and the participant ePRO magic-link surface. The reviewer sees CDISC-aligned forms, ODM-XML export, edit-checks at point of entry, the query workflow, and PHI living in a separate clinical-data store.

**Surface map for this path:**

```
http://localhost:8000/ecrf.html      — Form Builder (admin role)
http://localhost:8000/collector.html — Site EDC capture (coordinator/investigator)
http://localhost:8000/epro.html?token=…  — Participant ePRO (magic-link, consent-gated)
```

### D1. Draft CRFs from a protocol

**1. Open the Form Builder.**

```
http://localhost:8000/ecrf.html
```

**2. Click "New study" and fill in:**

- **Protocol ID:** `SMOKE-T2DM-001`
- **Title:** `Phase 2 SGLT2i in adults with T2DM (smoke study)`
- Click **Create study**. The Form Builder lists the new study with zero forms.

**3. Click "Draft forms from protocol" (or the equivalent CTA on the empty-state).** This posts to `POST /api/ecrf/draft` which routes to the `ecrf_design` specialist. Paste the canned protocol below into the textarea:

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

Click **Draft forms**. The drafter returns a `StudyDraft` — a list of proposed forms (Demographics, Eligibility, Vital Signs, Lab Safety, HbA1c, AE Reporting, DTSQ ePRO, etc.) plus a Visit Schedule. **It does NOT auto-save.** The designer reviews and accepts what they want; nothing reaches the published catalogue without a click.

**4. Click "Accept all" (or pick the subset you want) → the drafted forms appear as `status=draft` in the form list.**

### Sanity checks (D1)

- **Nothing auto-publishes.** Drafted forms land at `status=draft`. The reviewer must edit/save and explicitly Publish — the AI is never on the live data-capture path.
- **CDASH naming.** Demographics fields like `BRTHDTC` (date of birth), `SEX`, `RACE` follow CDASH conventions. If the drafter invents non-standard names, flag it as a prompt regression.
- **Visit schedule sensible.** Visits and the per-form schedule should match the pasted protocol (5 visits, Week 0/4/12/24/28).
- **Eligibility check items honest.** Numeric ranges (HbA1c 7.0–10.0%, BMI 22–40) should land as edit-check ranges on the eligibility form, not free-text fields.

### D2. Review & edit a generated form

**1. Click a form in the list (e.g. `Vital Signs`).** The Form Builder loads the full `FormDefinition` — sections, items, edit-checks. This hits:

```
GET /api/ecrf/forms/{form_id}
```

**2. Edit anything:**

- Add a field: in any section, click **+ Item**, set `item_id`, `label`, `data_type` (numeric / text / date / boolean / coded), units, required flag.
- Reorder fields by drag.
- Add an edit-check: on the systolic BP item, add `range: 70–250 mmHg, severity: hard` (blocks save) or `severity: soft` (auto-raises a query — see D4).
- Rename a field's label; **item_id stays stable** so existing data references survive the rename.

**3. Click "Save draft" (or `Ctrl+S` if the binding is wired).** Saves via:

```
PUT /api/ecrf/forms/{form_id}     # existing draft
POST /api/ecrf/studies/{study_id}/forms   # if creating a new one
```

The form stays at `status=draft`. You can iterate freely until you Publish.

### Sanity checks (D2)

- **Item-ID stability.** Renaming a label does NOT change the `item_id`. This is what lets a published form be superseded later (D3) without orphaning captured data.
- **Hard vs soft check distinction.** Hard checks return `422` from save and block submission. Soft checks save but auto-create an open `query` (visible in D4).
- **Required-flag enforcement is on save, not on input.** You can blank a required field while typing; the save round-trip rejects it.

### D3. Publish (immutable) and ODM-XML export

**1. Click "Publish" on the form.** Endpoint:

```
POST /api/ecrf/forms/{form_id}/publish
```

The form moves `draft → published`. The definition is now **immutable** — further edits require:

```
POST /api/ecrf/forms/{form_id}/new-version
```

…which clones the published definition into a new `draft` and supersedes the old one once you publish the new version. Captured data on the old version stays bound to its original definition snapshot.

**2. Export the form as ODM-XML.** Useful for cross-EDC interoperability or attaching to a regulatory submission:

```
GET /api/ecrf/forms/{form_id}/export.odm.xml
```

This returns a CDISC ODM-XML document — drop it on disk and inspect, or attach to the next step's deployment.

### Sanity checks (D3)

- **Published forms cannot be edited in place.** Attempting `PUT /api/ecrf/forms/{form_id}` on a `published` form returns `409 Conflict`. You must `new-version` first.
- **ODM-XML round-trip.** The exported XML lists every section, item, datatype, codelist (if any), and edit-check. The `<ItemRef>` order matches the on-screen order.
- **Supersede preserves history.** After `new-version` + publish, the old version's status becomes `superseded` and stays queryable for audit.

### D4. EDC capture (site coordinator)

**1. Open the EDC surface in a new tab:**

```
http://localhost:8000/collector.html
```

**2. Create a deployment of the study:**

- Click **New deployment** → select the published study → name it `Smoke deployment`.
- This calls `POST /api/edc/deployments`. The deployment snapshots the published form definitions into `DeployedForm` rows so future edits to the study don't retroactively change forms already in the field.

**3. Add a site and a subject:**

- **Add site:** `POST /api/edc/deployments/{deployment_id}/sites` — e.g. `Site 01 — University Hospital`.
- **Add subject:** `POST /api/edc/deployments/{deployment_id}/subjects` with `{site_id, subject_code}` — e.g. `subject_code = S01-001`. The system assigns an internal subject id; the code is what coordinators see.

**4. Open a form for the subject:**

- Click the subject's row → pick `Vital Signs (Visit 1, Baseline)` from the visit schedule. Endpoint: `POST /api/edc/subjects/{subject_id}/forms` with `{deployed_form_id}` → creates a `FormInstance` at `status=in_progress`.
- The Form Builder definition renders as inputs (the same `FormDefinition` snapshot, now with empty values).

**5. Enter data → save → observe edit-checks:**

- Type values into each field. Try violating a hard check (e.g. BP 300) → on save, the response is `422` with a structured failure list: `{detail: {failures: [{item_id, check_id, message}]}}`. The UI highlights the offending field; nothing is persisted.
- Fix the value and re-save → succeeds with the data persisted in `ItemData` rows.
- Try violating a **soft** check (e.g. BP 200 if the rule is "warn over 180"): saves succeed, **but a `Query` row is auto-raised** and appears in the form's query panel.

**6. Query workflow:**

- Open the **Queries** panel on the form instance (`GET /api/edc/form-instances/{form_instance_id}/queries`).
- For each open query: type a response → `POST /api/edc/queries/{query_id}/respond` → query moves `open → answered`. A coordinator with permissions can `POST /api/edc/queries/{query_id}/close` to close it.
- Manual queries are also possible: `POST /api/edc/form-instances/{form_instance_id}/queries` with `{item_id, text}`.

### Sanity checks (D4)

- **Hard check = blocked save.** Confirm the `422` response and that no `ItemData` rows were written for the violating attempt (`GET /api/edc/form-instances/{form_instance_id}` shows old values).
- **Soft check = auto-query.** The query references the offending `item_id` and the check that fired. Resolving it doesn't change the captured value — it documents the explanation.
- **Audit trail accumulates.** Every save records an `AuditEntry` row with `old_value` → `new_value`, `actor_sub`, `source=edc`, `created_at`. Inspect via `GET /api/edc/form-instances/{form_instance_id}/audit`.

### D5. ePRO capture (participant magic-link)

**1. Issue a magic link for the subject.** From the subject's row in `collector.html`, click **Issue ePRO link** (or hit the API directly):

```
POST /api/edc/subjects/{subject_id}/epro-access
```

Returns `{token, subject_id, epro_path: "/epro.html?token=…"}`. The raw token is shown **once** to the coordinator and is hashed in the DB — copy the URL.

**2. Open the participant surface in an incognito / private window** (so it doesn't inherit the coordinator's Cognito session):

```
http://localhost:8000/epro.html?token=<the-issued-token>
```

The participant sees a consent gate first (per `GET /api/epro/session?token=…`). They tap **I consent** → `POST /api/epro/consent?token=…` records the consent timestamp.

**3. Pick a PRO form** (e.g. `DTSQ (weekly)`):

- `POST /api/epro/forms?deployed_form_id=…&token=…` opens a form instance scoped to the token's subject only.
- Enter the 8 DTSQ items → click **Submit**. Hard/soft check semantics are identical to D4.
- Set `mark_complete=true` on save to lock the instance as completed.

### Sanity checks (D5)

- **Token scope is per-subject.** Trying to open a form for a different subject with the same token returns `403`. The token never grants admin access.
- **No coordinator data exposed.** The ePRO surface only renders forms flagged `epro=true` on the form definition. Site-only forms (Vital Signs, Eligibility) are invisible.
- **Consent is permanent.** Once recorded, the consent timestamp persists; the surface skips the consent gate on next open with the same token.
- **No raw token stored.** `SELECT token FROM …` returns nothing useful — only the hash is in the DB.

### D6. Sign / lock / audit (API-only today)

The sign / lock / source-data-verification flows are wired through the API but don't yet have a UI in `collector.html`. For a demo, drive them with `curl` or note them verbally:

```
POST  /api/edc/form-instances/{form_instance_id}/sign     {"meaning": "author"}
POST  /api/edc/form-instances/{form_instance_id}/unlock   {"reason": "correction"}  (admin)
POST  /api/edc/subjects/{subject_id}/sign                  {"meaning": "casebook"}
POST  /api/edc/form-instances/{form_instance_id}/verify   (monitor, source-data verification)
GET   /api/edc/form-instances/{form_instance_id}/signatures
GET   /api/edc/subjects/{subject_id}/signatures
GET   /api/edc/form-instances/{form_instance_id}/audit
```

Behaviour notes worth narrating during the demo:

- **Signing locks the form.** Once signed, attempts to PUT new `ItemData` return `409`. The signature is bound to the exact data hash at sign time.
- **Unlock voids the signature.** The original signature record is kept (with `voided=true`) and a new `AuditEntry` records the unlock — Part 11 traceability is preserved.
- **Subject casebook sign-off** is a higher-level seal across every form instance for a subject; unlocking the casebook voids that signature too.

### Sanity checks (D6)

- **Append-only audit.** No UPDATE or DELETE statements ever touch `AuditEntry`. The table has a DB-level constraint (E6) preventing modification — try a manual `UPDATE` in psql and watch it reject.
- **PHI segregation.** All data-bearing rows from D4–D6 live in `clinical-postgres`, **never** in the research `postgres`. Confirm:

  ```
  docker exec research-assistant-clinical-postgres-1 psql -U cra -d cra_clinical -c "\dt"
  docker exec research-assistant-postgres-1           psql -U cra -d cra            -c "\dt"
  ```

  Subject, FormInstance, ItemData, Signature, Query, AuditEntry tables only appear in the clinical DB.

### Failure modes you might hit

- **Editing a published form fails with 409.** Click **New version** to clone into a fresh draft.
- **ePRO 403 on a different subject.** Tokens are subject-scoped; re-issue the right token from the coordinator surface.
- **Hard-check failures persist after a refresh.** Hard checks block save, so the displayed values are the unsaved attempt — refreshing reloads the last successful save.
- **Audit endpoint slow on long histories.** Today it returns every row for the form instance unpaginated; expected for the audit-completeness invariant, may be paginated later.

---

## Appendix — All paste blocks (cheat sheet)

For when you just want every paste block in one place, in demo order.

### Canonical question (Path A)

```
Does adding a proton pump inhibitor to dual antiplatelet therapy reduce upper GI bleeding in patients who recently underwent PCI for acute coronary syndrome?
```

### PICO (Path A)

```markdown
| Element | Value |
|---|---|
| Population | Adults post-PCI for ACS, on dual antiplatelet therapy (aspirin + P2Y12 inhibitor) |
| Intervention | PPI added to DAPT (any PPI, any dose, ≥30 days) |
| Comparator | DAPT alone or DAPT + placebo |
| Outcome (primary) | Upper GI bleeding events during follow-up |
| Study design | Randomized controlled trials |
```

### Extraction — main (Path A)

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

### Extraction — high-heterogeneity variant (Path A optional)

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

### Protocol drafter triggers (Path B1)

```
/protocol SGLT2 inhibitors for HF prevention in T2DM
```

```
Help me draft a PRISMA-P protocol on whether SGLT2 inhibitors reduce heart-failure hospitalization in adults with type 2 diabetes.
```

### Search-strategy triggers (Path B2)

```
/search proton pump inhibitor + DAPT in post-PCI ACS
```

```
Build me a PubMed search strategy for: does adding a PPI to DAPT reduce upper GI bleeding in post-PCI ACS patients?
```

### Risk-of-bias trigger (Path B4 standalone)

```
/rob Run RoB 2.0 on PMID 20925534, PMID 30873575, PMID 31091374
```

### Protocol paste for the eCRF drafter (Path D1)

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

### eCRF REST surface (Path D)

Form authoring (research DB):

```
POST  /api/ecrf/draft                                  — AI form drafter
POST  /api/ecrf/studies                                — create study
GET   /api/ecrf/studies                                — list studies
PUT   /api/ecrf/studies/{study_id}/schedule            — set visit schedule
POST  /api/ecrf/studies/{study_id}/forms               — create form draft
GET   /api/ecrf/studies/{study_id}/forms               — list forms
GET   /api/ecrf/forms/{form_id}                        — get form definition
PUT   /api/ecrf/forms/{form_id}                        — update draft
POST  /api/ecrf/forms/{form_id}/publish                — publish (immutable)
POST  /api/ecrf/forms/{form_id}/new-version            — clone into a fresh draft
GET   /api/ecrf/forms/{form_id}/export.odm.xml         — CDISC ODM-XML export
```

EDC capture (clinical DB):

```
POST  /api/edc/deployments                                       — deploy a study
POST  /api/edc/deployments/{deployment_id}/sites                 — add site
POST  /api/edc/deployments/{deployment_id}/subjects              — add subject
POST  /api/edc/subjects/{subject_id}/forms                       — open form instance
GET   /api/edc/form-instances/{form_instance_id}                 — load instance + values
PUT   /api/edc/form-instances/{form_instance_id}/data            — save data (edit-checks fire)
POST  /api/edc/form-instances/{form_instance_id}/queries         — manual query
GET   /api/edc/form-instances/{form_instance_id}/queries         — list queries
POST  /api/edc/queries/{query_id}/respond                        — answer
POST  /api/edc/queries/{query_id}/close                          — close
POST  /api/edc/form-instances/{form_instance_id}/sign            — e-signature
POST  /api/edc/form-instances/{form_instance_id}/unlock          — void signature (admin)
POST  /api/edc/form-instances/{form_instance_id}/verify          — source-data verification
POST  /api/edc/subjects/{subject_id}/sign                        — casebook sign-off
POST  /api/edc/subjects/{subject_id}/unlock                      — void casebook signature
GET   /api/edc/form-instances/{form_instance_id}/audit           — audit trail (append-only)
POST  /api/edc/subjects/{subject_id}/epro-access                 — issue magic-link token
```

ePRO (participant, token-scoped):

```
GET   /api/epro/session?token=…                                  — consent state
POST  /api/epro/consent?token=…                                  — record consent
GET   /api/epro/deployed-forms/{deployed_form_id}?token=…        — load form definition
POST  /api/epro/forms?deployed_form_id=…&token=…                 — open instance
GET   /api/epro/form-instances/{form_instance_id}?token=…        — load values
PUT   /api/epro/form-instances/{form_instance_id}/data?token=…   — submit (edit-checks fire)
```

### Slash commands

```
/protocol, /sr, /prisma    → sr_protocol specialist
/search, /strategy         → search_strategy specialist
/meta, /meta-analysis, /ma → meta_analysis specialist
/rob, /bias                → risk_of_bias specialist
/general, /ask             → general_qa specialist
```

### URLs to know

```
http://localhost:8000/                          — main app (chat workflows)
http://localhost:8000/watches.html              — watches dashboard (Path C)
http://localhost:8000/admin.html                — paper-source admin
http://localhost:8000/ecrf.html                 — eCRF Form Builder (Path D, admin role)
http://localhost:8000/collector.html            — site EDC capture surface (Path D4)
http://localhost:8000/epro.html?token=…         — participant ePRO surface (Path D5, magic-link)
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

---

## Future variants (not yet generated, request on demand)

- **Null result** — pooled RR ≈ 1.0, tight CI.
- **Sparse data** — 3 trials only, wide CI, borderline significance.
