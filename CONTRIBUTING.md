# Contributing

This project is released under the [MIT License](LICENSE). By contributing you
agree that your contributions are licensed under the same terms.

## Setup

```bash
git clone git@github.com:arsalanam/cra.git && cd cra
uv sync --all-extras          # locked venv: runtime + dev tools
just qa                       # verify your environment: full quality gates
```

Running the app itself needs Docker (the runtime is compose-only):

```bash
docker build -t research-assistant-sandbox:latest ./sandbox
cp deploy/compose/.env.example deploy/compose/.env   # then fill in keys
just up                       # = docker compose up -d --build
```

Tests never need the stack — they run on in-memory SQLite (`just test`).

## Quality gates

**The `just` recipes are the source of truth.** CI runs exactly `just qa`:
tests → coverage (with a `fail_under` ratchet) → ruff lint → format check →
mypy strict → dependency security scan. If `just qa` is green locally, CI
will be green.

Optional but recommended: `uvx pre-commit install` wires ruff into every
commit so formatting issues never reach a PR.

Suppressing a lint finding requires an inline `# noqa: <code>` with a short
justification on the same line — waivers live in the code where reviewers
see them, never in config.

## Dependencies

`uv` manages everything through the committed `uv.lock`. Add runtime deps
with `uv add <pkg>`, dev-only tools under `[project.optional-dependencies] dev`.
Avoid new dependencies without a clear reason — the Docker sandbox tier
already pins pandas/numpy/scipy/statsmodels/matplotlib, and every new
runtime dep widens the audit surface of a clinical product.

## Architecture rules

1. **Anti-hallucination guardrails are load-bearing.** The
   `_reject_clinical_synthesis` validator and the "PMIDs must come from
   `search_papers`" rule exist because the model once fabricated evidence.
   Never relax them without explicit maintainer sign-off.
2. **The runtime path is compose-only.** Plain `uv run` is for tests and
   tooling; there is no host-mode server.
3. **Migrations are additive**, via `_COLUMN_MIGRATIONS` in
   `persistence/database.py` — they must work on both SQLite (tests) and
   Postgres (runtime).
4. **Structured outputs round-trip.** Specialist `model_dump_json()` is
   persisted verbatim into `Message.final_answer`; schema changes must
   stay backward-readable.
5. **No secrets in the tree.** `.env`, keys, and tfvars are gitignored;
   only `.example` templates are committed. The pre-commit hooks include a
   private-key detector.
6. **No real participant data. Ever.** Development and demos use synthetic
   subjects only — see [`docs/hipaa-posture.md`](docs/hipaa-posture.md).

## Pull requests

- One feature or fix per PR; include tests for new behavior.
- Run `just qa` before pushing; the PR template checklist assumes it.
- Update docs when behavior changes (`docs/`, and `CLAUDE.md` if the
  architecture shifted).
- New agent tools follow the `register(agent)` pattern — see
  `src/research_assistant/tools/` and the tool lists in its `__init__.py`.
