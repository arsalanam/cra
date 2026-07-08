# Agent-loop review — intent analysis, retry, circuit breaker

*Reviewed 2026-07-08 on `feat/app-logging` (2dc8633). Scope: `agent/dispatcher.py`,
`agent/deps.py`, `tools/_emit.py`, specialist `run_turn`s, `web/dispatch.py`.*

## How it works today

**Intent analysis** (`src/research_assistant/agent/dispatcher.py`) — a 6-step
heuristic cascade in `classify()`:

1. slash command (`/meta`, `/nma`, …) → forced workflow
2. continuation prefix ("PICO confirmed", …) → originating workflow
3. definitional opening ("what is …", "why …") → general_qa
4. thread already pinned → stay
5. first-turn keyword regexes (ordered, first match wins)
6. default → general_qa

Pure regex. No scoring, no confidence, no record of which rule fired.

**Retry** — pydantic-ai native: per-agent `retries=2` (tool retries) and
`output_retries=1–2`; `ModelRetry` raised from output validators with
corrective instructions (`_reject_clinical_synthesis` in general_qa,
`_require_sandbox_for_results` in meta_analysis);
`UsageLimits(request_limit=settings.max_model_requests,
tool_calls_limit=_MAX_TOOL_CALLS)` where `_MAX_TOOL_CALLS` ranges 10–100 per
specialist; `asyncio.wait_for(…, timeout=settings.agent_timeout_seconds)`
wall-clock bound.

**Circuit breaker** (`tools/_emit.py::emit_run`) — tools signal failure by
returning `{"error": …}` JSON (they don't raise). The shared emit wrapper
counts those per turn in `AgentDeps.error_count`; at
`max_tool_errors` (5) it raises `ToolErrorBudgetExceeded`, which
`web/dispatch._classify_agent_error` maps to a 502.

## Findings

### A. Intent analysis

**A1 — Mid-workflow definitional question derails the thread (bug, high).**
`classify` step 3 routes "what is heterogeneity?" to general_qa even when the
thread is pinned to meta_analysis (intentional detour), but
`web/dispatch.py` then re-pins the thread to the returned workflow
(`if thread.workflow != chosen_workflow: update_thread(workflow=…)`). Two
consequences: (a) subsequent free-form messages stay in general_qa — the
workflow is lost unless the user clicks a continuation button; (b)
`last_turn_kind` now reflects the general_qa answer, so the meta_analysis
`prepare_tools` stage gate sees the wrong kind when the user resumes.

**A2 — Continuation prefixes checked globally, before pinning.** 60+ magic
strings across 14 workflows matched with `startswith` regardless of
`current_workflow`. Collision risk grows with every workflow ("Intake
confirmed" vs "CSR intake confirmed" already needed disambiguation). A user
typing text that happens to start with a prefix jumps workflows.

**A3 — Keyword cascade is order-fragile with over-broad patterns.**
`\bipd\b`, `\bnma\b`, `\bsap\b` (case-insensitive → "the sap of a tree"
routes to the SAP drafter), `\b(compare|efficacy of|effect of|risk of)\s+\w`
is a very wide meta_analysis net. First match wins; nothing records which
pattern fired, so misroutes are undiagnosable after the fact.

**A4 — Implicit classification can 403.** `authorize_workflow` raises
`SkillNotAuthorizedError` even when the workflow was inferred from keywords
rather than requested. A restricted user typing "risk of dying from X" gets a
403 for a workflow they never asked for. Only slash-forced routes deserve a
hard 403; inferred routes should fall back to general_qa.

### B. Retry mechanics

**B1 — 17 copy-pasted `run_turn`s.** Policy already drifting: general_qa has
`output_retries=1` (deliberate, cost) vs `2` elsewhere; `_MAX_TOOL_CALLS`
scattered across 15 files. Any breaker/limit improvement must be edited 17
times.

**B2 — `ModelRetry` messages are static** — a second retry gets the identical
instruction that just failed. Minor.

**B3 — Timeout cancellation cleanup (audit item).** `asyncio.wait_for`
cancels the run mid-flight; verify a running `sandbox_exec` container is
killed rather than orphaned.

### C. Circuit breaker

**C1 — No identical-call detection (the core gap).** Nothing tracks
`(tool, args)`. The model can repeat `fetch_pmc_fulltext(pmid=X)` →
`{"available": false}` indefinitely; "not available" deliberately doesn't
count as an error, so the only bounds are `tool_calls_limit` (up to 100) and
the 120 s timeout — a repeat loop burns the whole budget before anything
stops it.

**C2 — Global budget, wrong granularity.** 5 errors across 5 *different*
tools aborts a turn that was making progress; conversely one flaky tool
alternating success/failure never trips.

**C3 — Tripping is all-or-nothing.** `ToolErrorBudgetExceeded` throws away
the entire turn (502), including minutes of successful work. No degraded
path ("stop using tools, answer with what you have").

**C4 — Error detection is shape-dependent.** Only JSON `{"error": truthy}`
counts; plain-text error strings and raised exceptions bypass the counter.

**C5 — No cross-turn memory.** If a source is down, every turn re-pays 5
failures before tripping.

## Improvement plan

Recommended execution order: **step 7 first** (so the breaker rewrite lands in
one place), then Phase 1, then Phase 2.

> **Status (2026-07-08):** steps 7, 1–3, 4–6, and the light version of 8
> (route rule persisted in the `done` event) are **implemented**:
> - `agent/specialists/_runner.py` — shared `run_agent_turn` + `turn_meta`;
>   all 15 registry specialists are thin wrappers now.
> - `agent/dispatcher.py` — `Route` dataclass, `classify_route()` with
>   scoped continuations + `_HANDOFF_SEEDS`, non-sticky definitional
>   detours, and general_qa fallback for unauthorized implicit routes.
> - `Message.workflow` column (additive migration) +
>   `_last_assistant_kind(messages, workflow=…)` scoping in
>   `web/dispatch.py`; threads pin only on sticky routes.
> - `tools/_emit.py` — breaker v2: identical-call short-circuit,
>   per-tool disable (3 failures), global soft-disable at 5 with
>   best-effort answer; `ToolErrorBudgetExceeded` is now only the
>   keep-calling backstop.
> - Tests: `test_dispatcher_routes.py`, `test_tool_circuit_breaker.py`,
>   `test_last_kind_scoping.py`.
> Remaining open: step 9 (LLM fallback classifier) and Phase 4 audits.

### Phase 1 — routing correctness
1. **Fix A1**: `classify` returns route metadata (workflow + sticky flag);
   definitional detours execute in general_qa but don't re-pin the thread,
   and the workflow-scoped `last_turn_kind` is preserved.
2. **Fix A2**: continuation prefixes scoped to `current_workflow`; only
   explicitly-marked handoff seeds may switch workflows. Routing-matrix test.
3. **Fix A4**: keyword-inferred workflow without permission → fall back to
   general_qa (logged); hard 403 only for slash commands.

### Phase 2 — circuit breaker v2
4. **Identical-call short-circuit** in `emit_run`: hash `(tool,
   canonical args)`; on repeat return the cached result plus "you already
   called this with identical arguments — the result has not changed; do not
   repeat this call" instead of re-executing. Third identical attempt counts
   against the error budget.
5. **Per-tool error threshold (3) → disable that tool for the rest of the
   turn** (synthetic "tool disabled" result); global budget of 5 stays as
   backstop across tools.
6. **Graceful degradation**: when the global budget trips, disable all tools
   for the remainder of the turn and let the model produce a best-effort
   text answer; raise `ToolErrorBudgetExceeded` (502) only if the model keeps
   trying to call tools past a small post-disable allowance.

### Phase 3 — structure & observability
7. **Extract a shared `run_specialist_turn()`** (deps, limits, timeout, usage
   meta) so the 17 `run_turn`s become thin wrappers; per-specialist knobs
   (`_MAX_TOOL_CALLS`, retries) collapse into one visible table.
8. **Routing observability**: persist which rule/pattern matched into turn
   `meta` — makes misroutes diagnosable, builds a corpus for step 9.
9. Optional: small-LLM (Haiku) fallback classifier for no-match/ambiguous
   first turns; regexes stay as the fast path.

### Phase 4 — audit items
10. Verify sandbox container cleanup on timeout cancellation.
11. Check whether source clients retry transient 5xx with backoff before
    returning `{"error": …}`; consider cross-turn source-health memory (C5).
