# CLAUDE.md

Project guide for Claude Code sessions in this repo. Keep it short — link out to source files for details.

## What this is

A Pydantic AI app that started as a ReACT tutorial and was pivoted into a **clinical research assistant**. The product surface is a FastAPI + React (CDN) web UI backed by AWS Bedrock (Claude Sonnet). Conversations are persisted in SQLite (`threads.db`).

The README is older than the code — don't trust it for current architecture.

## Architecture (current)

```
HTTP POST /api/turn
    │
    ▼
web/dispatch.py          ← single entry point per user turn
    │  loads thread + last assistant turn kind
    │  builds message_history from persistence
    ▼
agent/dispatcher.py      ← heuristic router
    │  slash commands (/meta, /general) → forced workflow
    │  workflow continuations ("PICO confirmed", …) → stay in workflow
    │  thread pinned → stay in workflow
    │  keyword match → workflow
    │  default → general_qa
    ▼
agent/specialists/{meta_analysis,general_qa}.py
    │  each owns: system prompt, output union, tool subset, agent factory
    │  meta_analysis: prepare_tools gates search_papers/fetch_pmc/sandbox_exec by stage
    ▼
tools/{clinical,data_science,general}/*.py
    │
    ▼ (paper search)
tools/clinical/search_papers.py  ← fan-out over enabled sources
    │
    ▼
tools/clinical/sources/{pubmed,europepmc}.py  (behind a `PaperSource` Protocol)
    │
    ▼
config/service.py  ← merges .env defaults with the source_configs DB table
```

Key modules:
- `agent/dispatcher.py:101` — `classify(user_message, current_workflow)` is the routing brain.
- `agent/specialists/meta_analysis.py` — 5-stage PICO → search → extraction → meta-analysis workflow, with strict matplotlib rules in the system prompt to keep `sandbox_exec` reliable.
- `agent/specialists/general_qa.py` — narrow tool subset; `_reject_clinical_synthesis` validator forbids quoting effect sizes / PMIDs / guideline citations from training data.
- `tools/clinical/search_papers.py` — multi-source paper search. `asyncio.gather` over `get_enabled_sources()`, dedupe by PMID → DOI → source:source_id (PubMed wins ties for richer Medline metadata).
- `tools/clinical/sources/` — `PaperSource` Protocol plus concrete `PubmedSource` and `EuropePMCSource`. `registry.py` maps source id → builder. `normalize.dedupe_studies()` is the cross-source merge helper.
- `config/service.py` — `SourceConfigService` reads the `source_configs` table and produces `RateLimitConfig`s with `.env` fallback for empty fields.
- `web/admin.py` — `GET/PUT /api/admin/sources` for runtime source config. Gated on `User.is_admin` against the seeded `default-user` row (placeholder for real auth).
- `web/static/admin.html` — vanilla settings page served at `/admin.html`; sidebar link in `index.html`.
- `persistence/{database,models,repository}.py` — async SQLAlchemy on aiosqlite. Hand-rolled additive migrations in `_COLUMN_MIGRATIONS`. `init_db` idempotently seeds `source_configs` for `pubmed` + `europepmc`.
- `tools/data_science/sandbox_exec.py` — Docker-isolated Python execution; image is `pydantic-sandbox:latest` (`sandbox/Dockerfile`). Network disabled, RO script + input mounts, RW output mount.

## Tools by category

`tools/__init__.py` exports three lists that specialists compose:
- `GENERAL_TOOLS` = web_search, wikipedia, fetch_document, read_file, describe_image
- `CLINICAL_TOOLS` = pubmed_search, mesh_lookup, fetch_pmc_fulltext
- `DATA_SCIENCE_TOOLS` = calculator, python_repl, sandbox_exec

Each tool module exposes `register(agent)`. Adding a tool: drop a module under the right subpackage, add it to its `__init__`, add it to one of the lists above.

## Constraints that are load-bearing

- **Anti-hallucination is intentional.** `_reject_clinical_synthesis` (general_qa) and the "PMIDs must come from `search_papers`" rule (meta_analysis prompt) were added after the model fabricated evidence. Don't relax them without explicit user sign-off.
- **Forest plot rules** (in `meta_analysis.py` system prompt) — figsize formula, dpi ≤ 150, `bbox_inches="tight"`, no `matplotlib.patches`, no seaborn / forestplot package, file size < 2 MB. Violations crash the sandbox.
- **Workflow tool gating** — `search_papers`, `fetch_pmc_fulltext`, `sandbox_exec` are hidden until the conversation reaches the right `last_turn_kind`. This forces the model down the workflow instead of skipping to synthesis.
- **API keys in plaintext.** `source_configs.api_key` is stored unencrypted in Postgres and returned verbatim by `GET /api/admin/sources`. Fine for single-admin localhost; do NOT expose admin endpoints beyond localhost without auth + encryption.

## Dev loop

The app is **compose-only** for the runtime path. Plain `uv run` is reserved for tests and tooling — no host-mode server.

```bash
# One-time prerequisites
uv sync --all-extras                                 # local venv for tests + tooling
docker build -t pydantic-sandbox:latest ./sandbox    # sandbox image (built separately)
cp deploy/compose/.env.example deploy/compose/.env   # fill in AWS / TAVILY / NCBI keys
                                                     # AND set HOST_SANDBOX_WORK_DIR to the
                                                     # absolute host path of ./sandbox-work

# Run the stack
docker compose -f deploy/compose/docker-compose.yml up -d
docker compose -f deploy/compose/docker-compose.yml logs -f agent
docker compose -f deploy/compose/docker-compose.yml down

# Tests + tooling (no Docker needed)
uv run pytest                    # in-memory SQLite, async-mode auto
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src                  # strict mode

# Migrate the legacy SQLite threads.db into the compose Postgres (one-shot)
uv run python scripts/migrate_sqlite_to_postgres.py
```

`tests/conftest.py` sets env defaults for AWS, model id, in-memory SQLite, Tavily key. `db_session` fixture gives each test a fresh in-memory DB. The runtime app uses Postgres — `DATABASE_URL` defaults to the compose-network hostname.

## Configuration

`config/settings.py` (`pydantic-settings`) loads from `.env`. Notable knobs:
- `bedrock_model_id` — default `us.anthropic.claude-haiku-4-5-20251001-v1:0` (cost-conscious). `.env.example` ships the same; override to a Sonnet / Opus tier when a workflow needs deeper reasoning. Bedrock requires the long inference-profile ID for Haiku 4.5 — the short alias only exists for Sonnet 4.6.
- `vision_model_id` — separate model for `describe_image`.
- `ncbi_api_key` — without it, NCBI throttles to 3 req/s; chained MeSH → search → fulltext hits this fast.
- `sandbox_*` — Docker image / timeout / memory / cpu / enabled flag.
- `database_url` — `postgresql+asyncpg://cra:cra@postgres:5432/cra` by default (compose). Tests override to in-memory SQLite via conftest.
- `context_window_messages` (20), `summarize_after_messages` (500) — persistence trimming.
- `max_model_requests` (100), `agent_timeout_seconds` (120) — runaway-loop bounds.

## Working norms in this repo

- `src/` layout, Python 3.13, `uv` (not pip/poetry), ruff (line length 100), mypy strict.
- Avoid adding new dependencies without a clear reason — sandbox tier already has pandas/numpy/scipy/statsmodels/matplotlib/seaborn/forestplot pinned.
- Prefer additive migrations via `_COLUMN_MIGRATIONS` (works on both SQLite for tests and Postgres at runtime); consider Alembic only if migrations get more complex.
- `model_dump_json()` from specialists is persisted directly into `Message.final_answer`; structured output schemas must round-trip cleanly.

## Things explicitly NOT in scope right now

- Login / multi-user auth (DB has a seeded `default-user` row in anticipation; the admin endpoints use a placeholder `_require_admin` check against it).
- `research_gap` and `ecrf_design` specialists (named in dispatcher placeholders, not implemented).
- ClinicalTrials.gov as a paper source — trial registry records have a different shape (NCT IDs, eligibility/interventions/outcomes, no abstract) so they would need a separate `TrialSource` protocol, not `PaperSource`. Deliberately dropped in the multi-source design.
- Encryption of `source_configs.api_key` (stored plaintext today).
- Automated full-text fetch when abstracts lack extraction numbers — users paste values into the UI instead.
- Additional paper sources (Google Scholar, Embase) — the architecture supports them (add a `PaperSource` subclass + builder entry + `init_db` seed), just not implemented yet.
