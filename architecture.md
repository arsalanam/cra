# Architecture — Clinical Research Assistant

**Status:** target / forward-looking. The current repo (single FastAPI process, SQLite, no container split) is the pre-ship implementation; the diagram below is what we're building toward. Update this doc as decisions land — it is meant to evolve.

---

## System diagram

```
                                  ┌──────────────────────────────┐
                                  │        AWS Cognito           │
                                  │  invite + self-registration  │
                                  │   (researcher onboarding)    │
                                  └──────────────┬───────────────┘
                                                 │ OIDC / JWT
                                                 ▼
   ┌─────────────┐         ┌────────────────────────────────────────┐
   │   Browser   │         │     Clinical Research Agent  (pod)     │         ┌────────────────┐
   │   React UI  │ ◀─────▶ │     FastAPI + Pydantic AI              │ ◀─────▶ │  AWS Bedrock   │
   └─────────────┘  HTTPS  │     • dispatcher  /  specialists       │   IRSA  │  (Claude)      │
                           │     • watch scheduler                  │         └────────────────┘
                           │     • RAG retriever (in-process)       │
                           │     • notification dispatcher          │
                           └─┬──────────┬───────────┬───────────┬───┘
                             │          │           │           │
                ┌────────────┘    ┌─────┘           │           └─────────────┐
                ▼                 ▼                 ▼                         ▼
       ┌────────────────┐  ┌──────────────┐  ┌───────────────┐       ┌──────────────────┐
       │ Code Sandbox   │  │  PostgreSQL  │  │  Shared FS    │       │ External APIs   │
       │     (pod)      │  │  + pgvector  │  │  document     │       │  PubMed, Europe  │
       │ stateless,     │  │ relational + │  │  cache (RWX)  │       │  PMC, Embase,    │
       │ no network,    │  │   vectors    │  │ PDFs, JSON,   │       │  Cochrane,       │
       │ stats compute  │  │   in one DB  │  │ MeSH, plots   │       │  Scopus, WoS     │
       └────────────────┘  └──────────────┘  └───────────────┘       └──────────────────┘
                                  ▲                 ▲
                                  │  metadata +     │  binary content
                                  │   embeddings    │     (read by RAG,
                                  │                 │     written by tools)
                                  │                 │
                           ┌──────┴─────────────────┴───────────────┐
                           │     Future containers (post-v1)        │
                           │  • eCRF Renderer        (form builder) │
                           │  • Data Collector       (form runtime) │
                           │  • Source-doc Extractor (EHR imports)  │
                           └────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────────┐
   │  Notifications out                                                   │
   │    • AWS SES        — email: watch alerts, invites, onboarding       │
   │    • Slack Webhook  — channel: practice-changing-evidence alerts     │
   └──────────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────────┐
   │  Deploy                                                              │
   │    • Production   — single-tenant Kubernetes (EKS preferred)         │
   │    • Local dev    — docker compose                                   │
   └──────────────────────────────────────────────────────────────────────┘
```

---

## Components

### Clinical Research Agent (container)

FastAPI + Pydantic AI. Owns the dispatcher, specialists, watch scheduler, RAG retriever, and the public HTTP / SSE API. Stateless aside from in-flight requests — all durable state lives in Postgres and the shared document cache. Horizontally scalable; HPA on CPU + request queue depth.

### Code Execution Sandbox (container)

Docker-isolated Python for statistical analysis. The trust boundary: **no network**, mounted input directory **read-only**, mounted output directory writable, capped CPU / memory / wall-clock. Runs as a per-request job pod (cold-start cost) or a small warm pool (faster, slightly looser isolation) — see open questions.

### PostgreSQL with pgvector

A single Postgres instance hosts both stores:
- **Relational tables:** users, threads, messages, source_configs, watches, watch_runs, notifications, RBAC roles.
- **Vector tables:** embedding columns on the document-cache index records (`pgvector` extension).

One database keeps dev/prod parity simple and avoids a second piece of infrastructure to operate. **pgvector is the v1 and steady-state choice** — see the decision record below. The realistic corpus ceiling for this product (low single-digit millions of vectors over several years) sits well inside pgvector's comfortable operating range, and co-locating embeddings with the source rows gives us atomic writes so the RAG index can never drift from the source-of-truth tables.

### Shared file-system document cache

A `ReadWriteMany` persistent volume (EFS / Azure Files / Filestore / NFS) mounted into the agent and the future eCRF / data-collector containers. Stores the **bytes**:
- Pulled abstracts (JSON)
- Fetched full-text PDFs
- MeSH lookup snapshots
- Generated extraction tables
- Sandbox output artefacts (forest plots, code, logs)

The **metadata + embeddings** live in Postgres so similarity search and provenance queries stay fast; the FS keeps the database lean and lets large binary payloads (full-text PDFs especially) live where they belong.

### RAG service (in-process, not a separate container)

Lives inside the agent process to keep latency tight and ops surface small. Two tools exposed to specialists:
- `rag_search(query, k)` — pgvector similarity search → ranked hits → resolve to FS-stored documents.
- Embedding helpers used by ingest paths (`search_papers`, `fetch_pmc_fulltext`, extraction-table writers) so every pulled artefact is indexed once.

The shared FS + Postgres separation means the same RAG corpus is visible to future containers (eCRF context lookups, source-doc cross-referencing).

### AWS Cognito (authentication)

Single OIDC issuer for all human users. Two enrolment paths:
- **Self-registration** for researchers — email verification, optional admin-approval gate per institutional policy.
- **Admin invitation** — admin sends invite; user lands on the Cognito hosted UI to set password and consent.

The agent receives Cognito JWTs and resolves them into local `User` rows. **Cognito hosts identity; the app owns authorisation.** RBAC roles (PI / methodologist / data manager / read-only) live in Postgres keyed to the Cognito `sub`. Per-user scoping of search-API entitlements, model tier, and shared-thread visibility is enforced in the agent.

### AWS Bedrock (LLM runtime)

Bedrock runtime endpoint, region-pinned. Credentials via IAM Roles for Service Accounts (IRSA) — never in env vars or DB. Model id configurable per workflow so cheaper tiers (Haiku, smaller Sonnet) can be used where full reasoning isn't required.

### External bibliographic APIs

PubMed (NCBI E-utils), Europe PMC, Embase, Cochrane, Scopus, Web of Science. Credentials in **AWS Secrets Manager**, projected at runtime via the External Secrets Operator. Per-source rate-limit knobs in Postgres `source_configs`. Single `search_papers` fan-out tool (see `[[project_paper_sources]]` memory).

### Notifications

A small dispatcher inside the agent fans out per-notification-policy:
- **AWS SES** — transactional email: watch alerts, Cognito-driven invitations, onboarding messages, weekly digests.
- **Slack API** (incoming webhooks) — channel posts for practice-changing-evidence alerts on living-review watches and high-severity system errors.

Notification policies (who gets what, on which channel, at what severity) live in Postgres.

### Future containers (post-v1)

Each ships as its own container; each shares the document-cache volume and consumes the agent's REST API rather than the database directly.

- **eCRF Renderer** — drafts and renders electronic case-report forms from a study protocol.
- **Data Collector** — runtime that captures responses against eCRFs and writes them back via the agent.
- **Source-doc Extractor** — ingests EHR / registry exports and populates extraction tables.

---

## Deployment

### Production — single-tenant Kubernetes

- **Distribution:** EKS preferred (matches Bedrock IAM model via IRSA); AKS / GKE / on-prem k3s acceptable.
- **Packaging:** Helm chart at `deploy/helm/cra/` (TBD).
- **Pods:**
  - `agent` — Deployment, ≥ 2 replicas, HPA.
  - `sandbox` — Job per request, *or* small Deployment pool (TBD).
  - `postgres` — managed (RDS / Aurora / Cloud SQL) preferred over self-hosted.
- **Storage:** EFS (or equivalent RWX) for the document cache; gp3 EBS for self-managed Postgres.
- **Networking:** agent behind an ingress controller (ALB / nginx); sandbox + Postgres on cluster-internal services only; egress through a NAT to the configured bibliographic and Bedrock endpoints.
- **Secrets:** AWS Secrets Manager + External Secrets Operator — never plaintext.

### Local dev — docker compose

- Single `docker-compose.yml` at `deploy/compose/` (TBD) brings up: `agent`, `sandbox`, `postgres` (with pgvector image), `mailhog` (SES stand-in), and a webhook receiver for Slack testing.
- **Cognito locally:** either a small OIDC stub (Dex / `oidc-mock`) or point at a sandbox Cognito User Pool — pick one when wiring auth.
- Shared FS = a bind mount.

**Target dev loop:** `git clone && docker compose up` and the full stack runs locally with no AWS creds.

---

## Decision records — closed

### Vector store: pgvector, v1 and steady-state (2026-05-15)

**Decision:** PostgreSQL + pgvector hosts both the relational schema and the vector embeddings. No dedicated vector DB.

**Why:**
- The realistic corpus ceiling for this product is low single-digit millions of vectors (≈ 50 reviews/year × ~2,000 papers × ~10 chunks/paper over several years). Comfortably inside pgvector's operating range with HNSW indexing.
- **Atomic writes** — paper metadata and its embedding land in one transaction, so the RAG index can't drift from the source-of-truth tables. A separate vector store would force two-phase writes with reconciliation logic we don't want to own.
- One DB to back up, monitor, secure, and credential. Cuts the operational surface in half.
- The shared FS holds the underlying documents, so re-embedding into a different store later is a one-day exercise if we ever genuinely outgrow pgvector — but planning for it now is premature optimisation.

**Revisit if:** corpus exceeds ~10M vectors *and* query latency at HNSW probes 64+ exceeds product SLO. Neither expected in v1 or v2 timeframe.

### Daily token quota: single shared ledger via `done` events (2026-05-16)

**Decision:** Daily input + output token caps are enforced pre-flight in `services/quota.py`. Today's totals are computed by summing the existing `StreamEvent.event_type='done'` rows (which already carry per-turn `usage`) — no parallel counter table, no schema change.

**Knobs:** `MAX_INPUT_TOKENS_PER_DAY` and `MAX_OUTPUT_TOKENS_PER_DAY` in settings. `0` on either axis disables enforcement for that axis (ops escape hatch). Defaults sit comfortably under typical Bedrock per-account daily quotas so the app stops short of upstream throttling.

**Scope today:** per-account (the totals query has no tenant predicate yet). When Cognito + RBAC land, the query gets a `user_id` partition and the caps become per-user; the settings names already imply that.

**Enforcement model:** pre-flight, not in-flight. A single turn in progress can push the day's total over the line; the **next** turn is the one that gets refused (HTTP 429 with reset-at-UTC-midnight ETA). Per-turn ceilings — `settings.max_model_requests`, the per-workflow `_MAX_TOOL_CALLS` constants in each specialist, and `settings.agent_timeout_seconds` — bound how much damage that overshoot turn can do. The watch runner uses the same check and records `quota_exceeded` runs without bumping the baseline, so no papers slip through across a quota-blocked run.

**Why:**
- One ledger means `/api/threads/usage/monthly`, the new `/api/threads/usage/today`, and the per-turn `TurnResponse.quota` payload can never disagree — they all read from the same rows.
- No transactional coupling between Bedrock calls and a counter row — simpler code, no contention to manage.
- The acceptable-overshoot trade is fine for single-tenant v1 and obvious enough that we can sharpen it later without surprise.

**Revisit if:** multi-tenant + strict per-tenant billing accounting needs exact totals — switch to a transactional counter row with optimistic locking and credit-after-call semantics. Not needed in single-tenant operation.

### Sandbox execution dispatch: K8s Job per request, no broker (2026-05-16)

**Decision:** In Kubernetes prod, the agent dispatches sandbox work by creating a **K8s `Job` per request** via the Kubernetes API and watching it to completion. Outputs land on the shared FS volume (already the case for image artefacts — see the data-URI → URL decision in the per-request flow). Concurrency is capped in the agent with an `asyncio.Semaphore(8)` as cheap backpressure. Local dev keeps the existing in-process Docker model.

**Explicitly rejected for v1:** Celery + Redis (or any external task broker) for sandbox dispatch.

**Why no broker:**
- Sandbox jobs are short (1-10s), stateless, idempotent, and the user is waiting synchronously for the answer. There's no fire-and-forget or batch case in v1.
- Single producer (the FastAPI agent process) — a broker exists to fan-in from many producers, which we don't have.
- Each of Celery's headline benefits has a simpler equivalent at this scale:
    - *Decoupling from the HTTP request* — already async via `asyncio.to_thread`; the event loop is free.
    - *Retries on worker crash* — jobs are idempotent; the user re-asks if it fails; the model has its own retries.
    - *Backpressure* — `asyncio.Semaphore(N)` in the agent. One variable.
    - *Persistence* — not needed; the request owns the job.
    - *Horizontal scaling* — K8s HPA on the agent pod scales sandbox capacity along with the rest of the app.
- A broker would add **Redis + Celery workers** to the deploy footprint — two more components to operate, monitor, restart, and reason about — to solve problems that don't exist yet.

**Why not in-process Docker in prod:** the current `docker.from_env()` relies on a Docker socket in the agent process. That doesn't translate to K8s (no socket; docker-in-docker is a security smell and an operational mess). K8s Job is the native primitive.

**Phasing:**
- **Phase 1 (v1):** K8s Job per request. ~1-3s cold start per job — acceptable for the meta-analysis flow where the user is already in a multi-step workflow.
- **Phase 2 (if Phase 1 cold start hurts UX):** Move to a **warm sandbox pool** (small Deployment of long-lived sandbox pods exposing a `POST /run` endpoint). Sub-100ms latency, slightly weaker per-request isolation, no broker.
- **Phase 3 (Celery becomes the right call):** Only if one of the revisit triggers below fires.

**Revisit if:**
- Sandbox jobs become long-running (minutes-to-hours, e.g. long ETL or training pipelines) — at that point a queue actually represents real queueing behaviour, not theoretical decoupling.
- A new producer joins (a scheduled batch sweep, a CLI tool, another microservice) that needs to enqueue work into the same worker pool.
- Cross-tenant priority queues with strict fairness become a product requirement.
- Durable retry-on-worker-crash semantics are needed because individual job loss would be costly (today they're cheap to re-run).

None of these are expected in v1 or v2 timeframe.

---

## Open questions — iterate on these

- **Multi-tenant story:** single-tenant for v1. If we ever go multi, namespace-per-tenant + schema-per-tenant Postgres is the likely shape — but explicitly out of scope now.
- **eCRF data residency:** ~~do form responses live in the same Postgres or in a separate clinical-data store with tighter access controls?~~ **Resolved (2026-05-23):** a **dedicated clinical-data Postgres** (separate credentials/backup/residency), distinct from the research-app DB; subject PHI never lives in the research database. See `ecrf-design.md` Decision D2.
- **Backup posture:** Postgres PITR + EFS snapshots; frequency / retention TBD per customer SLA.
- **Observability stack:** OpenTelemetry traces from agent through tool calls; Prometheus for system metrics; Loki for logs. Stack chosen, wiring TBD.
- **DR targets:** RPO / RTO per-customer-tier — not yet defined.
- **Sandbox image supply chain:** signed base image + SBOM + admission-controller gate, or trust-on-first-pull? Decide before any external pilot.
- **Auth bridge:** how to map Cognito groups → app RBAC roles cleanly. JIT provisioning vs explicit admin assignment.

---

## How this doc relates to others

- **`ecrf-design.md`** — design document for the eCRF subsystem (the *eCRF Renderer* + *Data Collector* containers named above). Resolves the eCRF data-residency open question; details the form-definition model, clinical-data model, audit/ALCOA+ and Part 11 posture.
- **`feature-guide.md`** — the buyer-facing capability story. Stays aligned with the architecture choices recorded here.
- **`CLAUDE.md`** — describes the *current* repo, which is the pre-ship implementation, not the diagram above. The gap between the two is the build plan.
- **Memory: ship-gate requirements** — the hard set of things needed before any version of this diagram is "shipping" rather than "in progress".
