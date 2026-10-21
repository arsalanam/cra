# CRA — Clinical Research Assistant

[![ci](https://github.com/arsalanam/cra/actions/workflows/ci.yml/badge.svg)](https://github.com/arsalanam/cra/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.13-blue.svg)](pyproject.toml)

**An AI research platform for the full clinical-study lifecycle — from
literature synthesis and protocol design through data capture, analysis,
and regulatory reporting — with the auditable guardrails that clinical
work demands.**

Built on Pydantic AI + AWS Bedrock behind a FastAPI + React (CDN) web UI.
A heuristic dispatcher routes each message to one of 15+ specialist
workflows (meta-analysis, systematic-review screening, SAP drafting, CDISC
mapping, CSR drafting, trial statistics, …), each with its own tool
subset, staged gating, and anti-hallucination validators. Clinical data
capture (eCRF/EDC, ePRO, randomisation, safety, labs) lives in a dedicated
PHI-capable Postgres store with Part 11-style signatures and append-only
audit trails.

📖 **Docs:** [documentation index](docs/README.md) ·
[agentic architecture](docs/agentic-architecture.md) ·
[system architecture](docs/design/architecture.md) ·
[feature guide](docs/guides/feature-guide.md) ·
[contributing](CONTRIBUTING.md)

## Install

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker,
[just](https://github.com/casey/just), and AWS credentials with Bedrock
model access.

```bash
git clone git@github.com:arsalanam/cra.git && cd cra
uv sync --all-extras                                   # locked venv (tests + tooling)
docker build -t research-assistant-sandbox:latest ./sandbox   # code-execution sandbox image
cp deploy/compose/.env.example deploy/compose/.env     # fill in AWS / TAVILY / NCBI keys
```

## Quick start

The runtime is **compose-only** — the app always runs in Docker, next to
its two Postgres stores:

```bash
just up        # build + start: postgres, clinical-postgres, agent
just logs      # follow the agent container
# open http://localhost:8000
just down      # stop the stack
```

Tests and tooling never need the stack:

```bash
just test      # full suite (~1,650 tests, in-memory SQLite)
just qa        # everything CI runs: tests, coverage, lint, format, types, security
just --list    # all recipes
```

## Project structure

```
src/research_assistant/
├── agent/            # dispatcher (routing/intent), specialists, shared runner
├── tools/            # agent tools: general / clinical / data_science, register() pattern
├── web/              # FastAPI app: dispatch, auth, RBAC-gated APIs, static React UI
├── persistence/      # research store (async SQLAlchemy) + clinical/ PHI store
├── auth/             # Cognito OIDC + RBAC engine (scoped roles, SoD)
├── rag/              # publication cache, Titan embeddings, hybrid retrieval
├── ecrf/ randomization/ trial_stats/ cdisc/ nma/ ipd/   # clinical domain engines
├── reports/          # PDF/DOCX renderers (meta-analysis, CSR, SAP, …)
├── services/         # cross-cutting: quotas, spend, reminders, watches, rollups
├── validation/       # IQ/OQ/PQ pack + requirements traceability
├── config/           # pydantic-settings, Bedrock pricing-as-code, source configs
└── domain/ data/ visualizations/
```

**Design rule:** specialists own their system prompt, output schema, and
tool subset; tools are gated by workflow stage so the model cannot skip
ahead to synthesis. Model-authored code runs only inside a
network-disabled Docker sandbox.

## The databases

Two separate Postgres 16 (pgvector) stores by design: the **research
store** (threads, papers, budgets, portfolio) and the **clinical store**
(subjects, visits, labs, safety — PHI-capable, isolated). Schema changes
are hand-rolled additive migrations that run identically on the in-memory
SQLite used by tests. See [docs/hipaa-posture.md](docs/hipaa-posture.md)
for the data classification.

## Quality gates

`just qa` is the single source of truth; [CI](.github/workflows/ci.yml)
runs exactly the same recipes:

```
just test          # pytest (~1,650 tests)
just coverage      # pytest-cov with a fail_under ratchet
just lint          # ruff: pyflakes, isort, bugbear, security (bandit), pytest-style
just format-check  # ruff format --check
just typecheck     # mypy --strict over src/
just security      # pip-audit over uv.lock
```

Lint waivers are inline `# noqa` comments with justifications — visible in
review, never hidden in config. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security & compliance

- **Auth:** Cognito SSO (OIDC); RBAC with ~105 permissions across 12
  scoped roles; separation-of-duties matrices ([design](docs/design/rbac-design.md))
- **Anti-hallucination:** output validators reject clinical claims not
  grounded in retrieved sources; PMIDs must come from live search — the
  AI model never asserts evidence from training data
- **Regulatory alignment:** Part 11-style e-signatures, append-only audit,
  PRISMA 2020 / GRADE / ICH E3/E2A / CDISC outputs
  ([details](docs/agentic-architecture.md))
- **PHI posture:** dedicated clinical store, PHI-minimised rollups,
  HL7 ingestion that discards demographics ([posture](docs/hipaa-posture.md))
- **Deployment:** runs entirely in your own AWS account — model inference
  via Bedrock; OpenTofu stack under `deploy/ec2`

## License

Released under the [MIT License](LICENSE) — free to use, modify, and
distribute with attribution and the license notice. Provided "as is",
without warranty; not a certified medical device or a substitute for
regulatory review.
