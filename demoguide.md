# Demo Guide — PPI + DAPT Meta-Analysis (Synthetic Data)

Synthetic test data for the canonical clinical question:

> *Does adding a proton pump inhibitor to dual antiplatelet therapy reduce upper GI bleeding in patients who recently underwent PCI for acute coronary syndrome?*

Numbers are **fabricated** to give a plausible meta-analysis result (~RR 0.44, moderate heterogeneity). Author names are made-up so this can never be confused with real evidence.

---

## 1. PICO (paste at the PICO confirmation step)

| Element | Value |
|---|---|
| **Population** | Adults post-PCI for ACS, on dual antiplatelet therapy (aspirin + P2Y12 inhibitor) |
| **Intervention** | PPI added to DAPT (any PPI, any dose, ≥30 days) |
| **Comparator** | DAPT alone or DAPT + placebo |
| **Outcome (primary)** | Upper GI bleeding events during follow-up |
| **Study design** | Randomized controlled trials |

---

## 2. Extraction table (paste at the extraction step)

All fields are SYNTHETIC. Event = upper GI bleeding.

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

**Totals:** PPI 73 / 8810 (0.83%) vs Control 165 / 8786 (1.88%).

---

## 3. Expected analysis behavior

- Pooled RR ≈ **0.44** (95% CI roughly 0.33–0.58), random-effects.
- I² in the **30–50%** range — some between-study variation but not extreme.
- Forest plot should be 8 rows + diamond, well within the figsize / dpi / 2 MB sandbox limits.
- No subgroup splits needed — keeps the matplotlib code minimal.

---

## 4. How to drive it through the UI

1. Start a thread with the canonical question above.
2. When the agent proposes a PICO, paste the PICO table from §1 and confirm.
3. Skip / fast-forward the search step (or let it run on real PubMed — the synthetic studies won't be there).
4. At the extraction step, paste table §2 verbatim.
5. Approve meta-analysis → forest plot.

---

## 5. High-heterogeneity variant (alternate extraction table)

Use this **instead of** §2 when you want to stress-test the random-effects model and the I² display. Same PICO (§1). Effect estimates span RR ≈ 0.25 → 1.85 across trials; expected pooled RR ≈ 0.55–0.70 with very wide CI and **I² ≈ 75–85%**.

Two outlier trials (Diaz 2019, Faruq 2021) favor control — pretend they used different PPI dosing or a sicker subgroup.

| Study | Year | Design | PPI events | PPI total | Control events | Control total | Follow-up (mo) | Approx RR |
|---|---|---|---|---|---|---|---|---|
| Anderson et al. | 2014 | Multicenter RCT | 8 | 1200 | 20 | 1198 | 12 | 0.40 |
| Bekele et al. | 2016 | Single-center RCT | 3 | 400 | 12 | 395 | 6 | 0.25 |
| Chen et al. | 2018 | Multicenter RCT | 15 | 1500 | 25 | 1495 | 12 | 0.60 |
| **Diaz et al.** | 2019 | Pragmatic RCT | **22** | 600 | **12** | 605 | 9 | **1.85** |
| Eriksen et al. | 2020 | RCT | 4 | 700 | 16 | 695 | 6 | 0.25 |
| **Faruq et al.** | 2021 | Multicenter RCT | **18** | 900 | **14** | 895 | 12 | **1.28** |
| Garcia et al. | 2022 | RCT | 5 | 800 | 19 | 795 | 6 | 0.26 |
| Huang et al. | 2023 | Multicenter RCT | 11 | 1300 | 24 | 1305 | 12 | 0.46 |

**Totals:** PPI 86 / 7400 (1.16%) vs Control 142 / 7383 (1.92%).

What to look for in the output:
- I² should land in the **70–85%** range — clearly flagged as high heterogeneity.
- Forest plot CIs should visibly fail to overlap a single common effect.
- A subgroup or sensitivity analysis excluding Diaz + Faruq should drop I² sharply and tighten the pooled estimate toward RR ≈ 0.4.
- The narrative should temper conclusions ("substantial heterogeneity precludes strong recommendation…").

---

## 6. Other variants still to generate on request

- **Null result** — pooled RR ≈ 1.0, tight CI
- **Sparse data** — 3 trials only, wide CI, borderline significance

---

## 7. Search-strategy specialist — end-to-end demo

The new `search_strategy` workflow builds a MeSH-expanded Boolean query, tests it on PubMed + Europe PMC for hit counts, and emits copy-paste plans for Cochrane CENTRAL + Embase. Once finalised it can hand off to meta-analysis with the validated query pre-loaded.

### How to drive it

1. `uv run research-assistant` — start the dev server.
2. Open `http://localhost:8000/`, click "New conversation".
3. Send either of these to enter the workflow:
   - **Slash command:** `/search proton pump inhibitor + DAPT in post-PCI ACS`
   - **Keyword trigger:** `Build me a PubMed search strategy for: does adding a PPI to DAPT reduce upper GI bleeding in post-PCI ACS patients?`

### Expected flow

| Stage | Card | What you do |
|---|---|---|
| 1 | `query_blocks` — PICO concepts with MeSH terms + free-text synonyms per block | Edit synonyms, drop unwanted MeSH terms, set the target hit-count band (default 50–500). Click **Confirm terms**. |
| 2 | `strategy_result` (draft) — composed Boolean query + hit counts for PubMed + Europe PMC + plans for Cochrane CENTRAL + Embase + 5 sample hits + 2–3 refinement buttons | Click a refinement button (e.g. "Restrict to last 10 years") to iterate. The agent re-runs the search and returns a fresh draft. |
| 3 | Repeat stage 2 until the band-status banner turns green ("inside target band"). | Click **Finalize strategy**. |
| 4 | `strategy_result` (final) — same shape, `is_final=true`, no refinement suggestions, plus a **Run meta-analysis on these results** button | Click the handoff button to spawn a fresh meta-analysis thread pre-seeded with the research question + validated PubMed query. |

### What to look for / sanity checks

- **MeSH provenance:** every MeSH chip on the `query_blocks` card includes a UID (e.g. "Proton Pump Inhibitors UID D054328"). If you see a MeSH descriptor without a UID, the agent skipped `mesh_lookup` — flag it.
- **Embase caveat:** the Embase plan card MUST display the warning "⚠ Emtree terms are unverified — confirm against Embase Emtree thesaurus before running." If it's missing, the prompt rule is being violated.
- **Hit-count honesty:** PubMed + Europe PMC plans show real numbers; Cochrane CENTRAL + Embase plans show "not executed here — copy & run manually" instead of a fabricated count.
- **Query-result match:** the `composed_query` shown in the PubMed plan card should be byte-identical to what was passed to `search_papers` (you can verify in the server logs — look for `search_papers` tool-call args).
- **Handoff:** the new meta-analysis thread should arrive at its PICO step with the question already understood, and its STEP 3 search query should be your validated string (or a close refinement).

### Slash commands now available

| Command | Routes to |
|---|---|
| `/protocol`, `/sr`, `/prisma` | sr_protocol specialist |
| `/search`, `/strategy` | search_strategy specialist |
| `/meta`, `/meta-analysis`, `/ma` | meta_analysis specialist |
| `/general`, `/ask` | general_qa specialist |

---

## 9. Living-review watches — end-to-end demo

The new living-review feature watches a saved PubMed query on a schedule, triages new hits with the LLM against the saved PICO, and raises a notification when a paper exceeds the materiality threshold. Unlike the other specialists, this is **background**: a watch persists for weeks/months and pings you when the literature actually moves.

The runner lives at `services/watch_runner.py`; scheduling at `services/scheduler.py`; the LLM triage at `agent/specialists/watch_triage.py`.

### Architecture in one paragraph

When a watch is created (POST `/api/watches`), the endpoint runs the saved search once to populate the baseline PMID set, persists the watch, and registers a cron job with APScheduler. At each scheduled fire, the runner re-executes the search via `tools/clinical/search_papers._fan_out`, computes the diff against `baseline_pmids_json`, hands new papers to the triage agent (one Bedrock call returning a `WatchRunSummary`), persists a `WatchRun`, and creates a `Notification` if `notify=true`. The bell icon polls `/api/notifications/unread-count` every 30 s.

### How to drive the full loop

1. **Hard-refresh** (`Ctrl+F5`) so the browser picks up the new sidebar bell, watches link, and StrategyResult Watch button.
2. Run the dev server (`uv run research-assistant`) — startup logs should show `Scheduler started` and `Scheduler re-registered N active watch(es) from DB`.
3. Build a search strategy end-to-end (see §7), all the way to **Finalize strategy**.
4. On the final `strategy_result` card, click **Watch this query**.
5. In the modal:
   - Name: pre-filled from the original research question (editable)
   - Schedule preset: pick **Every 2 minutes (demo)** to see the loop work without waiting
   - Materiality threshold: leave at 0.6
   - Click **Create watch**
6. Click the sidebar **▤ Watches** link → you should see your new watch with status `active`, baseline = 5 PMIDs (from sample hits), schedule `*/2 * * * *`, next-run timestamp ~2 minutes out.
7. Wait 2 minutes (or click **Run now** for an immediate trigger). The page doesn't auto-refresh — reload to see the new run row.
8. Click **Show runs** on the watch card to expand the run history. You'll see one of:
   - `no_change` — search returned the same baseline PMIDs (most likely outcome, since we just snapshotted)
   - `success` — new PMIDs appeared; triage results render with relevance/design-fit pills and a materiality score
9. If the run was `success` AND any triage hit `materiality ≥ 0.6`, the bell badge in the main app's sidebar lights up with the count. Click it to see the dropdown; clicking a notification marks it read and jumps you to `/watches.html`.

### What to look for / sanity checks

- **Baseline pre-populated**: the watch creation logs should show `pre-populated baseline with N PMIDs from initial search`. The first scheduled run shouldn't surface dozens of "new" papers — only what's actually been indexed since.
- **Triage pills are honest**: `relevance=high` + `design_fit=matches` + `materiality > 0.6` should be reserved for clearly in-scope, well-powered studies. If you see most new hits with materiality 0.8+, the prompt is too lenient — tighten in `agent/specialists/watch_triage.py`.
- **Significance summary doesn't fabricate**: it should NOT quote effect sizes the abstract didn't report. Cross-check against the linked PMID.
- **Paused watches don't fire**: pause a watch and confirm no new runs appear after the next scheduled fire time.
- **Cascade delete**: deleting a watch removes its runs + notifications (the bell badge should drop accordingly).
- **Restart durability**: stop and restart the dev server; on startup the log should show `Scheduler re-registered N active watch(es) from DB`. The schedule survives even though APScheduler's MemoryJobStore loses jobs — the DB is the source of truth.

### Failure modes you might hit

- **AWS quota** — the triage agent makes one Bedrock call per scheduled run. With the demo `*/2 * * * *` schedule that's 30 calls/hour; you'll burn token quota fast. Switch to weekly for anything but a single demo run.
- **Bad cron** — the create-watch endpoint validates via APScheduler's `CronTrigger.from_crontab`. Malformed crons return a 400 with the parser error in the bell-styled error message.
- **Search source disabled** — if PubMed is disabled in `/admin.html`, the runner falls back to whatever's enabled. If everything is disabled, the run records `status=error` with the "no sources enabled" message.
- **Process restart between runs** — the in-memory schedule is rebuilt at startup from active watches. A run scheduled to fire during downtime will be coalesced (single fire on next startup) thanks to `coalesce=True`.

### Slash commands and pages

| Surface | URL / route |
|---|---|
| Watches dashboard | `/watches.html` |
| Sidebar bell | top of the bottom panel in the main app |
| Watch creation | "Watch this query" button on `is_final=true` strategy_result card |
| REST API | `GET/POST/PATCH/DELETE /api/watches`, `GET /api/watches/{id}/runs`, `POST /api/watches/{id}/run-now`, `GET /api/notifications`, `POST /api/notifications/{id}/read` |

---

## 10. Risk-of-bias specialist — end-to-end demo

The new `risk_of_bias` workflow applies a Cochrane-style RoB rubric (RoB 2.0 / ROBINS-I / Newcastle-Ottawa / QUADAS-2) to a list of studies and produces:
- a per-study × per-domain assessment table for review/edit
- a stacked-bar summary plot via `sandbox_exec`
- sensitivity recommendations naming specific high-RoB PMIDs
- a one-click handoff that re-runs meta-analysis excluding those studies

It sits at the end of the meta-analysis lifecycle and supports two entry points: standalone (`/rob` with a list of PMIDs) or via the **Assess risk of bias** button on a `data_extraction` card.

### How to drive it (downstream from a meta-analysis)

1. **Hard-refresh** (Ctrl+F5) so the browser picks up the new cards and the RoB button on data_extraction.
2. Run the meta-analysis recipe (§4) through to the data_extraction card.
3. Click **Assess risk of bias** — a fresh thread spawns, pre-loaded with a payload of the extracted studies (PMIDs + titles + designs + abstracts) plus the PICO context for tool selection.
4. The thread auto-fires turn 1 → `rob_assessments` card appears with one section per study, each showing all five (RoB 2.0) / seven (ROBINS-I) domains, color-coded judgment pills, and expandable justifications.
5. Edit any judgment via the dropdown — the **overall** judgment per study auto-recomputes by the worst-domain rule.
6. Click **RoB confirmed — generate summary**.
7. `rob_summary` card appears with: the stacked-bar plot, per-domain count table, narrative interpretation, and sensitivity recommendations naming any high-RoB PMIDs as chips.
8. Click **Finalize RoB**.
9. `is_final=true` rendering adds the **Re-run meta-analysis excluding high-RoB studies** button. Click → fresh meta-analysis thread spawns with the validated PICO + the high-RoB PMIDs explicitly excluded from STEP 2.

### How to drive it standalone

1. New conversation, type: `/rob Run RoB 2.0 on PMID 20925534, PMID 30873575, PMID 31091374`.
2. Agent fetches each abstract via `search_papers`, applies the rubric, returns `rob_assessments`.
3. Same flow from step 5 above. (No PICO context available — the sensitivity handoff will ask the user to restate the question.)

### What to look for / sanity checks

- **Tool auto-pick from study designs:** with all RCTs, the tool should be **RoB 2.0**. Mix in a cohort study and re-run → it should switch to **ROBINS-I**. The pill at the top of the card shows the active tool.
- **`no_information` is honest:** for each study, expect 1–3 domains judged `no_information` (grey pill). Most abstracts don't describe randomization method, allocation concealment, or pre-registered analysis plans. The justification should explicitly say "Abstract does not describe X" — never invent a description.
- **Quotes are verbatim:** if a domain has a quote (italic blue block when expanded), it should appear word-for-word in the linked abstract. Cross-check by clicking the PMID.
- **Overall judgment follows the worst-domain rule:** any `high` → overall `high`; any `some_concerns` (no `high`) → overall `some_concerns`; all `low` → overall `low`. `no_information` on a critical domain bumps overall to at least `some_concerns`.
- **Plot file size:** the matplotlib template enforces `< 2 MB`. If the plot fails to generate, the summary card shows "Summary plot was not produced" instead of crashing.
- **Sensitivity handoff:** if any study is overall `high`, the **Re-run meta-analysis excluding high-RoB studies** button should be enabled. With no high-RoB studies, the button is disabled and the CTA text reads "No high-RoB studies to exclude."
- **PICO carry-through:** when launched via the data_extraction handoff, the new sensitivity meta-analysis thread should NOT re-ask for PICO — it carries through verbatim. Standalone-launched RoB threads will lack PICO context and ask for it.

### Try it on the synthetic studies from §2

After running the synthetic meta-analysis (§4), at the data_extraction card click **Assess risk of bias**. The triage will struggle on most domains because the synthetic studies have no real abstracts — most cells will be `no_information`. That's actually a good sanity check: the agent IS reading the abstract and finding nothing, rather than fabricating judgments. To see a more realistic flow, run on real PMIDs (e.g. PMID 20925534 for COGENT — has a substantive abstract).

### Slash commands

| Command | Routes to |
|---|---|
| `/rob`, `/bias` | risk_of_bias specialist |
| `/protocol`, `/sr`, `/prisma` | sr_protocol specialist |
| `/search`, `/strategy` | search_strategy specialist |
| `/meta`, `/meta-analysis`, `/ma` | meta_analysis specialist |
| `/general`, `/ask` | general_qa specialist |

---

## 8. SR/MA protocol specialist — end-to-end demo

The new `sr_protocol` workflow drafts a PRISMA-P 2015–aligned protocol with a grounded background (citations come from real `search_papers` / `web_search` calls), a full Markdown export, and a PROSPERO field map. It sits *upstream* of the existing chain — finalising it lets you hand off into either `search_strategy` or `meta_analysis` with PICO pre-loaded.

### How to drive it

1. `uv run research-assistant` — start the dev server.
2. Open `http://localhost:8000/`, click "New conversation".
3. Send either of these:
   - **Slash command:** `/protocol SGLT2 inhibitors for HF prevention in T2DM`
   - **Keyword trigger:** `Help me draft a PRISMA-P protocol on whether SGLT2 inhibitors reduce heart-failure hospitalization in adults with type 2 diabetes.`

### Expected flow

| Stage | Card | What you do |
|---|---|---|
| 1 | `protocol_methods` — title, review type, PICO (MeSH-verified), eligibility (inclusion/exclusion/study designs/language/dates/age/setting), info sources, RoB tool + rationale, effect-measures grid keyed by outcome, synthesis plan, GRADE checkbox | Edit any field. Click **Methods confirmed — draft full protocol**. |
| 2 | `protocol_document` (draft) — title, grounded background with [N] citations, references list (each with origin badge + PMID/DOI/URL), PROSPERO field map table (per-row Copy buttons), full Markdown (Show/Hide + Copy markdown) | Either type a free-form refinement ("expand the background", "add a sensitivity analysis for high-RoB studies") to iterate, or click **Finalize protocol**. |
| 3 | `protocol_document` (final) — same shape, `is_final=true`, two handoff buttons | Click **Build the search strategy** to spawn a fresh search_strategy thread with PICO + planned databases pre-loaded, OR **Skip to meta-analysis** to spawn a fresh meta_analysis thread with the validated PICO. |

### What to look for / sanity checks

- **RoB auto-suggest:** with `study_designs = ["Randomized Controlled Trial"]` only, the card should default to `RoB 2.0` with a rationale referencing RCTs-only. Add "Cohort study" and re-run methods → it should switch to ROBINS-I.
- **Citation provenance:** every reference card has an origin badge — `search_papers` (cyan), `web_search` (blue), `wikipedia` (amber). PubMed-sourced refs MUST display a PMID; web sources MUST display a URL.
- **No fabricated numbers:** the background should NOT contain percentages, sample sizes, or effect estimates that aren't tied to a `[N]` citation. If you see one, the prompt rule was violated.
- **PROSPERO placeholders are honest:** user-specific fields (Named contact, Funding sources, Country, IRB number, dates) should display content starting with `[USER INPUT NEEDED:` and styled in amber. The model should NOT have invented an email or grant ID.
- **Effect measures match outcomes:** every key in the effect-measures grid should equal an outcome in the PICO. Editing PICO outcomes should not orphan rows.
- **Handoff seeds work:** clicking **Build the search strategy** should drop you into a search_strategy thread where the agent's first turn is `query_blocks` already populated with the MeSH-verified PICO concepts. Clicking **Skip to meta-analysis** should drop into a meta_analysis thread where the agent skips PICO build (since one was provided) and proceeds toward search/extraction.

### Try the canonical case

Question: *"Help me draft a PRISMA-P protocol on whether SGLT2 inhibitors reduce heart-failure hospitalization in adults with type 2 diabetes."*

Expected `protocol_methods` highlights:
- `review_type = intervention`
- PICO with `population` ≈ "Adults ≥18 with T2DM", `intervention` = "SGLT2 inhibitor", `outcomes` includes "Heart-failure hospitalization", "All-cause mortality", "MACE"
- Eligibility: `study_designs = ["Randomized Controlled Trial"]`, language English, ≥12 weeks follow-up
- `rob_tool.tool = RoB 2.0` (since RCTs-only)
- `effect_measures_plan` populated with `RR` for each binary outcome
- `synthesis_plan.primary_method = random_effects_meta`, planned subgroups by baseline HbA1c / prior HF status

Expected `protocol_document` highlights:
- 2–3 paragraph background covering T2DM burden, prior SGLT2i landmark trials (EMPA-REG, CANVAS, DECLARE, DAPA-HF or similar — names will come from real `search_papers` results), guideline statements (ESC/AHA/ADA)
- 5–10 references, mostly origin=`search_papers` with PMIDs
- PROSPERO field map with ~12 model-fillable fields and ~6 `[USER INPUT NEEDED]` placeholders
- Full Markdown ~1500–2500 words, copy-paste ready into PROSPERO / a journal submission portal / an IRB form
