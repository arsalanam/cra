# T1 — Per-account trial spend quota (design)

*Design doc for trial-readiness item T1 (see [`trial-readiness.md`](trial-readiness.md)).
Status: **BUILT 2026-07-19** (Q1–Q4 on `feat/t1-spend-quota`) — pending
user verification on compose/EC2, then merge. All decisions D1–D6 locked.*

**Implementation notes (deltas from the design):** enforcement lives in
`services/spend.py` (not a separate `spend_budget.py`); zero-cost events
skip the ledger instead of writing zero rows; query-time RAG embeddings
are not separately metered (sub-cent noise — the drain is the real cost
and IS metered); background embedding of the shared publication cache
bills the Default Account as platform overhead.*

## Goal

A hard USD budget per **Account** — e.g. $500 for a trial-run research
group — covering **all metered spend** (Bedrock turns, Tavily searches,
Titan embeddings, vision), measured from a configured start date, enforced
**before** each turn (hard 429), and visible to the account admin with a
warning threshold.

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| D1 | Budget scope | **Per Account** (not per install). Trials inherit their account's budget; a per-Trial breakdown is reporting-only. |
| D2 | What counts | **Everything metered**: agent-turn Bedrock tokens, Tavily searches, Titan embeddings (library uploads + RAG queries), vision model calls. |
| D3 | At the cap | **Hard stop** — turns refused with 429 + budget message, same posture as the daily token quota. Admin raises the budget to resume. |

## What exists today (verified 2026-07-19)

- `Account` / `ClinicalTrial` / `AccountMember` models; a seeded
  `"Default research program"` account. `Thread.trial_id` (nullable) is the
  **only** thread→account path — NULL for all Q&A/analysis threads; there
  is no `Thread.account_id`.
- Per-turn `done` events (`stream_events.data` JSON: `usage` with
  input/output tokens + `model_id`, `tool_usage` counts, `workflow`).
  `services/cost_rollup.py` recomputes USD on read via
  `config/bedrock_pricing.py` (Claude chat models only).
- `services/quota.py::enforce_daily_token_quota` — the enforcement pattern
  to copy: pre-flight in `web/dispatch.py` (`→ HTTPException 429`) and in
  `services/watch_runner.py` (run recorded as skipped).
- **Gaps**: embeddings (`rag/embedder.py` discards `inputTextTokenCount`),
  vision (`describe_image` discards the `converse` usage block), and
  Tavily are entirely unmetered; no per-account cost view; no frontend
  quota surfacing at all.

## Design

### 1. Attribution — `Thread.account_id`

New nullable FK `threads.account_id → accounts.id` (additive migration),
set once at thread creation:

1. Thread spawned from a trial (handoff CTA / accounts UI) → the trial's
   `account_id`.
2. Otherwise → the creating user's account membership (their single
   membership; if several, their oldest `owner`/`admin` membership).
3. Fallback → the seeded default account.

One-time backfill (idempotent, in the migration path): threads with
`trial_id` → that trial's account; all remaining threads → the default
account. Every thread therefore has an account, and **setting a budget on
the default account is equivalent to an install-wide budget** — the simple
case stays one knob.

### 2. Ledger — `spend_ledger` table

Enforcement needs a cheap scoped sum; recomputing from event JSON per turn
does not scale and can't hold non-turn spend (library uploads). New
append-only table, the single source of truth for budget accounting:

| column | type | notes |
|---|---|---|
| `id` | Text PK | uuid |
| `account_id` | FK accounts, **indexed** | never NULL |
| `trial_id` | FK clinical_trials, nullable | for per-trial reporting |
| `thread_id` / `message_id` | nullable | NULL for library uploads |
| `category` | Text | `turn` \| `vision` \| `embedding` \| `search` |
| `quantity` | Integer | tokens, or search count |
| `model_id` | Text nullable | pricing provenance |
| `usd` | Float | priced at write time |
| `created_at` | DateTime tz, **indexed** | |

Writers:
- **`web/dispatch.py`** after each turn (same place the done event is
  written): one `turn` row (Bedrock usage priced), plus a `search` row when
  `tool_usage["web_search"] > 0`, plus a `vision` row when vision tokens
  were captured (below). Errored turns write rows too (placeholder zeros —
  consistent with done events).
- **Library upload / embed path**: `embedding` rows per batch with Titan
  token counts.

Existing `cost_rollup.py` (read-side, from done events) is untouched — the
ledger is additive. Prices are captured at write time; changing
`bedrock_pricing.py` later does not retroactively reprice history.

### 3. Metering the unmetered

- **Vision** — `tools/general/describe_image.py`: read the `usage` block
  from the `converse` response (`inputTokens`/`outputTokens`), accumulate
  on `AgentDeps` (`vision_usage`), surface through `turn_meta` into the
  done event and the ledger. Priced via the existing `lookup(vision_model_id)`
  (it's a Claude model — already in the table).
- **Embeddings** — `rag/embedder.py`: capture Titan's
  `inputTextTokenCount`; new pricing entry for
  `amazon.titan-embed-text-v2:0` (`$0.00002 / 1k` input tokens). Upload
  batches write ledger rows directly; query-time embeddings during a turn
  ride the turn's ledger write.
- **Tavily** — priced per search from `tool_usage` counts; new setting
  `tavily_price_per_search_usd` (proposed default `0.008`, ≈ pay-as-you-go
  credit price — confirm, D5).

### 4. Budget config + enforcement

`Account` gains three columns (additive migration):

- `budget_usd` (Float, default 0 = disabled)
- `budget_start_at` (DateTime nullable; stamped to *now* when a budget is
  first set, editable — this is the "trial start date")
- `budget_warn_percent` (Integer, default 80)

New `services/spend_budget.py`:

```
async def get_account_spend(session, *, account_id, since) -> float      # indexed SUM over spend_ledger
async def enforce_account_budget(session, *, account_id) -> BudgetStatus # raises AccountBudgetExceeded
def build_budget_payload(status) -> dict                                 # {limit, spent, remaining, percent, warn}
```

Enforcement is **pre-flight** in `web/dispatch.py` (next to
`enforce_daily_token_quota`) and in `services/watch_runner.py` (run
recorded as `budget_exceeded`, skipped). `AccountBudgetExceeded` maps to
**429** via `_classify_agent_error`:

> "Account budget exhausted ($512.40 of $500.00 spent since 2026-08-01).
> An account admin can raise the budget under Accounts → Budget."

Because ledger rows are written *after* a turn completes, the final turn
may overshoot the cap by one turn's cost (cents at Haiku rates). Accepted —
pre-flight keeps latency off the happy path and the daily token quota has
the same property.

### 5. Surfaces

- **API** — `AccountPatch`/`AccountView` (`web/accounts.py`) gain the three
  budget fields, gated `ACCOUNT_MANAGE` as today; new
  `GET /api/accounts/{id}/spend` → budget payload + per-category and
  per-trial breakdown + 7-day burn rate + projected exhaustion date.
- **Budgets page** (new `budgets.html`, D6) — every account's budget in
  one place: limit / consumed / remaining / burn rate / projected
  exhaustion, per-category and per-trial breakdown; budget fields editable
  inline (`ACCOUNT_MANAGE`). Sidebar link from `index.html`.
- **Chat** — `TurnResponse.quota` gains a `budget` axis; the composer shows
  a banner at ≥ `budget_warn_percent`; a friendly error card on 429
  (also closes the "frontend error card" T6 side item for this path).

### 6. Out of scope

Per-user ceilings within an account (RBAC quota-tier follow-up),
retroactive repricing, non-USD currencies, budget alert emails (rides T4
SES later, warning stays in-app for the trial).

## Decisions D4–D6 (locked 2026-07-19)

| # | Question | Decision |
|---|----------|----------|
| D4 | Backfill rule for existing threads without a trial | trial-bound → trial's account; all others → default account |
| D5 | Tavily per-search price default | `$0.008`, overridable via `TAVILY_PRICE_PER_SEARCH_USD` |
| D6 | Where the budget admin UI lives | **dedicated `budgets.html` page** (all accounts in one view, inline budget editing) |

## Build plan

| Phase | Content | Tests |
|---|---|---|
| Q1 | Migrations: `Thread.account_id` + backfill, `Account` budget columns, `spend_ledger`; attribution at thread creation | attribution rules incl. fallbacks; backfill idempotency |
| Q2 | Metering: vision usage capture, Titan token capture + pricing entry, Tavily pricing setting; ledger writers in dispatch + upload path | ledger rows per category; priced-at-write; errored-turn rows |
| Q3 | Enforcement: `spend_budget.py`, dispatch + watch_runner pre-flight, 429 mapping | cap boundary, disabled (0) budget, start-date window, watch skip |
| Q4 | Surfaces: accounts API fields, `/spend` endpoint, portfolio card, chat banner + error card | API auth gating; payload shape; warn threshold |

Each phase lands as its own commit on `feat/t1-spend-quota`.
