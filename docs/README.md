# CRA documentation

Start here. **New to the project?** Read the root [README](../README.md)
first, then [`agentic-architecture.md`](agentic-architecture.md) for how the
agent is built. **Contributing?** [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
has setup and the quality-gate workflow.

## Project-level docs

| Doc | What it covers |
|---|---|
| [`agentic-architecture.md`](agentic-architecture.md) ([PDF](agentic-architecture.pdf)) | How the agent is designed: routing/intent analysis, ReACT guardrails, anti-hallucination framework, deterministic workflows, FastAPI/SQLAlchemy practices, security |
| [`design/architecture.md`](design/architecture.md) | Target system topology — containers, stores, AWS deployment shape |
| [`design/rbac-design.md`](design/rbac-design.md) | Role-based access control: ~105 permissions, 12 roles, scoped grants, separation of duties |
| [`design/ecrf-design.md`](design/ecrf-design.md) | eCRF/EDC design and its locked decisions (dedicated PHI store, Part 11/ALCOA+, EDC + ePRO) |
| [`hipaa-posture.md`](hipaa-posture.md) | PHI & HIPAA posture: code-verified data inventory, Security-Rule alignment, shared-responsibility split |
| [`agent-loop-review.md`](agent-loop-review.md) | The review that produced the current agent-loop design (routing, retries, circuit breaker) |

## Trackers

| Doc | What it tracks |
|---|---|
| [`trial-readiness.md`](trial-readiness.md) | Gate list for opening the deployment to first external users (T1–T7) |
| [`roadmap.md`](roadmap.md) | Feature roadmap: everything shipped, plus the pickable backlog |
| [`t1-spend-quota.md`](t1-spend-quota.md) | T1 design doc — per-account USD spend budgets (on its feature branch until merged) |

## Guides

| Guide | What it covers |
|---|---|
| [`guides/feature-guide.md`](guides/feature-guide.md) ([PDF](guides/feature-guide.pdf)) | The full feature reference, end to end |
| [`guides/demoguide.md`](guides/demoguide.md) ([PDF](guides/demo-guide.pdf)) | Scripted demo walkthrough with sample inputs |
| [`guides/library-guide.md`](guides/library-guide.md) | The publication library + RAG: uploads, embedding, hybrid search |
| [`guides/testing-guide.md`](guides/testing-guide.md) | Testing approach and how to exercise the suite |

## Other artefacts

- [`brochure.html`](brochure.html) / [`brochure.pdf`](brochure.pdf) — executive brochure for research directors

*Note: `t1-spend-quota.md` and the T7 updates to `trial-readiness.md` land
with the `feat/t1-spend-quota` branch; links resolve once it merges.*
