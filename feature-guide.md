# Clinical Research Assistant — Feature Guide

**An AI co-investigator for systematic reviews, meta-analyses, and evidence synthesis — built for hospital research offices, academic medical centres, and clinical research organisations.**

---

## Why this exists

A high-quality systematic review takes a small team **6–12 months**: scoping the question, building search strategies, screening thousands of abstracts, extracting data, running the statistics, assessing bias, and writing it up. Most of that work is repetitive, methodology-bound, and easy to get wrong in ways that don't show up until a reviewer rejects the manuscript.

The Clinical Research Assistant compresses the methodological scaffolding into a guided, auditable workflow — without ever fabricating evidence. Your investigators stay in the driver's seat for clinical judgment. The assistant handles the parts that should never have been manual in the first place.

---

## What the assistant does

Five guided workflows, a continuous-monitoring service, and a local research store that grows in value the more you use it. Each workflow launches from natural language or a slash command and produces a structured, citation-anchored output you can take into a manuscript, a registration, or a grant application.

### 1. Meta-analysis workflow

End-to-end: from a research question to a forest plot. The assistant walks the user through **PICO** confirmation, runs the literature search across every database your institution has licensed, presents candidate studies for inclusion/exclusion, captures the extraction table, then computes the pooled effect estimate with heterogeneity statistics and renders a publication-grade forest plot inside an isolated compute sandbox.

**Outcome:** what used to be a multi-week analyst engagement is a single guided session. Every PMID in the output traces back to a real database hit — the model is structurally prevented from inventing citations.

### 2. Search-strategy builder

Constructs Boolean queries with MeSH terms, field tags, and database-specific syntax across **PubMed, Europe PMC, Embase, Cochrane Library, Scopus, and Web of Science** — whichever your institution has licensed, in a single fan-out. Iterates on **broaden / tighten** suggestions with the user until the strategy is registration-ready.

**Outcome:** information specialists get a defensible, multi-database, reproducible search string in minutes instead of half a day, with the iteration history preserved.

### 3. Systematic-review protocol drafter

Generates a **PRISMA-P–aligned** protocol skeleton — background, objectives, eligibility, search methods, screening plan, data items, risk-of-bias plan, synthesis approach — ready to deposit in **PROSPERO**.

**Outcome:** a protocol-ready first draft your methodologist edits, not writes from scratch. Helps surface gaps before they cost you a peer-review round.

### 4. Risk-of-Bias assessor

Supports the major instruments: **RoB 2** (RCTs), **ROBINS-I** (non-randomised), **Newcastle-Ottawa** (observational), **QUADAS-2** (diagnostic accuracy). Walks domain-by-domain, asks the operator the judgement questions, and produces the structured assessment table for the review.

**Outcome:** consistency across reviewers and a clean audit trail of *why* each judgement landed where it did.

### 5. General clinical Q&A

For the questions that aren't a full systematic review: background reading, definition of methods, navigation of guidelines. The assistant uses web and Wikipedia tools, **but is hard-prevented from quoting effect sizes, PMIDs, or guideline citations from training data** — a regex-level validator blocks unsupported clinical claims before they reach the user.

**Outcome:** safe-by-default exploratory Q&A that won't seed a manuscript with hallucinated evidence.

### 6. Living-review watches (scheduled monitoring)

A meta-analysis or systematic review doesn't have to be a snapshot. Pin a PICO and a search strategy as a **watch**, set a cadence (daily / weekly / monthly), and the assistant re-runs the search on schedule, diffs against the baseline corpus, triages newly published papers against the original PICO, and **notifies the team when something materially shifts the evidence base**.

**Outcome:** "living" reviews stop drifting out of date. Guideline committees and HTA bodies get alerted to practice-changing evidence as it lands.

---

## What makes it different

| Differentiator | Why it matters for a research org |
|---|---|
| **Anti-hallucination enforced in code, not just prompt** | A regex validator and tool-gated PMID rule block fabricated citations even if the underlying model drifts. Your investigators can trust the bibliography. |
| **Source-agnostic search across paid + open databases** | PubMed, Europe PMC, Embase, Cochrane Library, Scopus, Web of Science — all live behind a pluggable `PaperSource` interface and fanned out in parallel with cross-source de-duplication. One admin panel for credentials, rate-limits, enable/disable. |
| **Local research cache + RAG over pulled content** | Every abstract, full-text article, MeSH lookup, and extraction table you pull lands in the local research store. A retrieval-augmented pipeline searches that store first, so the same paper isn't re-fetched — or re-billed — across reviews. Pull paid content once; reuse it across the entire research programme. |
| **Sandboxed statistical compute** | All Python analysis runs inside a Docker container with networking disabled, read-only inputs, and capped CPU/memory. The model can run a random-effects meta-analysis without ever touching the host or the wider internet. |
| **Workflow-gated tools** | The assistant cannot skip ahead. It can't run a meta-analysis before a PICO is confirmed; it can't fetch full text before studies are selected. The methodology *is* the guardrail. |
| **Flexible, secure deployment** | Deploy on-premises or in your cloud. **OAuth2 / OpenID Connect** integrates with your existing identity provider; **role-based access control** scopes each workflow per user; sensitive material (API keys, PHI) lives in your **secrets manager**, never in plaintext config. |
| **Full conversation persistence** | Every turn — PICO, included studies, extraction table, generated code, plot — is stored. Reproducing an analysis a year later means re-opening the thread. |

---

## What it costs to run

Two variable costs to plan around: LLM tokens on AWS Bedrock and web-search calls on Tavily. Everything else — paper database access, paper storage, the FastAPI app, the sandbox — sits on infrastructure you already own. Paid bibliographic databases (Embase, Scopus, Cochrane, etc.) are assumed to be covered by your existing institutional subscriptions.

| Metric | Envelope | Notes |
|---|---|---|
| **Active research day, per user** | **$200 – $300** | Covers Bedrock tokens + Tavily for a productive day: PICO refinement, multi-database search, screening + extraction across dozens of papers, statistical analysis with code generation, forest-plot rendering. |
| **Re-open / inspect an existing thread** | **≈ $0** | Past tool results (papers, extractions, generated code, plots) are already in the local store. Re-opening costs only the few tokens of the current turn. |
| **Re-using a paper across reviews** | **≈ $0** | The RAG pipeline hits the local research store first. The same landmark trial reused across cardio / onco / ID reviews is paid for once. |

**Cost-control levers built in:**

- **Local cache + RAG** — re-use of pulled abstracts and full text across reviews; no repeat API spend on content you've already paid for.
- **Workflow-gated tools** — expensive tools (sandbox, full-text fetch) are unreachable until the workflow stage needs them; no idle calls.
- **Per-turn ceilings** — `max_model_requests` and `agent_timeout_seconds` bound runaway loops to a known maximum cost.
- **Configurable model tier per workflow** — drop to a cheaper Sonnet / Haiku variant when full Opus reasoning isn't required.
- **Tavily is consumed only by general Q&A + protocol drafting** — the clinical search itself runs against bibliographic databases and does *not* consume Tavily credits.

A token-heavy day still lands comfortably **below the cost of an equivalent human-analyst day** — and per-paper marginal cost flattens as your local store grows.

---

## Deployment & governance

- **Flexible deployment:** runs on-premises or in your cloud (AWS, GCP, Azure). Single-tenant by default; same codebase, your choice of trust boundary.
- **Authentication:** OAuth2 / OpenID Connect — drops into Okta, Azure AD, Auth0, Keycloak, or your homegrown IdP. No bespoke user database.
- **Authorisation:** role-based access control per user. New-researcher onboarding is a one-step provisioning action that scopes their search-API entitlements, model access, and visibility into shared review threads.
- **Sensitive data:** API keys, Bedrock credentials, and any PHI live in a secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.) — never in plaintext config or SQLite.
- **Data egress:** outbound traffic is limited to your configured bibliographic APIs and your Bedrock endpoint. No telemetry. No shared multi-tenant cloud — LLM calls go to your own AWS account, so PHI-grade compute stays inside your trust boundary.
- **Audit:** every turn is persisted with full message + tool-call history. Re-opening a thread reproduces the analysis exactly. Exportable as part of a regulatory submission package.

---

## On the roadmap

Beyond the v1 capability set above, the architecture was built to absorb these without rebuilding the core. Each is a tractable next increment, not a moonshot.

### Automated clinical data collection

Two pieces, addressing two distinct pain points:

- **eCRF designer** — generate a draft electronic case-report form directly from a study protocol, with field types, validation rules, and skip logic mapped to the protocol's data items. Reduces the weeks-long round-trip between investigator and data manager at study start-up.
- **Source-document extraction** — point the assistant at structured exports from your EHR / registry / trial-management system and have it populate the extraction table for retrospective studies or meta-analyses of patient-level data, with the audit trail of which source row produced which output cell.

### Patient-facing research handouts

Generate **plain-language summaries** of a study's objectives, what participation involves, and what the early evidence suggests — at a configurable reading level, in the patient's preferred language. Designed for **research participant recruitment**, **shared-decision-making conversations**, and **post-study return-of-results** obligations under modern IRB / ethics guidance. Sources back to the same evidence base the clinical workflow uses, so the lay summary and the manuscript are in lock-step.

### Research-gap analysis specialist

Given a body of literature, surface where the evidence base is thin — by population, intervention, outcome, geography, or study design — so investigators can prioritise the next grant proposal where the field actually needs new data.

### Group-level living-review subscriptions

Group watches with quorum-based notification rules — designed for guideline committees and HTA bodies who need consensus signalling on practice-changing evidence rather than per-user alerts.

---

## In short

If your researchers spend more time **finding and formatting evidence** than **interpreting it**, this is what you point them at. They keep the clinical judgement. The assistant absorbs the methodological choreography — and writes it down as it goes.

---
---

# Appendix A — Capability Matrix

| Workflow | Trigger phrasing / slash | Inputs collected | Tools available | Structured outputs | Hardened guardrails |
|---|---|---|---|---|---|
| **Meta-analysis** | "meta-analysis on…", "pooled effect of…", `/meta` | PICO → included studies → extraction table | `search_papers`, `rag_search`, `mesh_lookup`, `fetch_pmc_fulltext`, `sandbox_exec`, `calculator` | PICO card, study-selection card, extraction card, forest plot + summary | PMIDs must come from search; plot rules enforced in sandbox |
| **Search strategy** | "build a search strategy…", `/search` | Concept terms, MeSH, filters | `mesh_lookup`, `search_papers` (preview counts) | Boolean string per database, MeSH map, broaden/tighten suggestions | None needed — output is the query itself |
| **SR protocol** | "draft a protocol…", `/protocol`, `/prisma` | PICO + scope choices | `web_search`, `wikipedia`, `fetch_document` | PRISMA-P–shaped protocol sections | No effect-size or PMID synthesis allowed |
| **Risk of Bias** | "risk of bias…", "RoB 2", `/rob` | Tool choice + per-study source | `fetch_pmc_fulltext`, `rag_search`, `read_file` | Domain-by-domain judgements with rationale | Operator must confirm each judgement |
| **General Q&A** | Everything else, `/general` | Free-form question | `web_search`, `wikipedia`, `fetch_document`, `rag_search`, `read_file`, `describe_image` | Cited prose answer | Regex validator blocks unsupported clinical claims |
| **Watch triage** *(background)* | Configured per-PICO schedule | New PMIDs since last run | (none — text-only) | Per-paper triage + run summary + optional notification | Same anti-hallucination posture as user-facing flows |

## Tool inventory

| Category | Tool | What it does |
|---|---|---|
| Clinical | `search_papers` | Fan-out search across enabled databases (PubMed, Europe PMC, Embase, Cochrane, Scopus, Web of Science); dedupes by PMID → DOI → source-id |
| Clinical | `rag_search` | Retrieval-augmented search over the local research store — checks pulled content before re-issuing API calls |
| Clinical | `mesh_lookup` | MeSH term resolution + tree-walk |
| Clinical | `fetch_pmc_fulltext` | Full-text retrieval; results cached into the local research store |
| Data science | `sandbox_exec` | Docker-isolated Python with pandas / numpy / scipy / statsmodels / matplotlib / seaborn / forestplot — no network, capped CPU & memory |
| Data science | `python_repl` | Lightweight in-process Python for arithmetic-only ops |
| Data science | `calculator` | Safe arithmetic evaluator |
| General | `web_search` | Tavily-backed search for non-clinical context |
| General | `wikipedia` | Definitional / background lookups |
| General | `fetch_document` | Fetch + extract text from arbitrary URLs |
| General | `read_file` | Read user-uploaded documents (PDF, DOCX, TXT) |
| General | `describe_image` | Vision model for chart / figure / scan interpretation |

## Configuration surface

| Setting | Where | Why an admin cares |
|---|---|---|
| Paper-source enable / disable, rate limits, credentials | Admin panel (credentials read from your secrets manager) | Plug in your institution's PubMed / NCBI key, Embase, Cochrane, Scopus, Web of Science subscriptions — toggle without redeploying |
| Identity provider | OIDC client config | Wire to Okta / Azure AD / Auth0 / Keycloak / homegrown |
| Role-based access | Admin panel | Per-user scoping of search entitlements, model tier, and shared-thread visibility |
| LLM model + region | Per-workflow config | Pin a Sonnet / Opus / Haiku version your governance team has approved; drop to cheaper tier for lighter workflows |
| Sandbox limits | Config (`sandbox_timeout_seconds`, `sandbox_memory_mb`, `sandbox_cpu`) | Cap compute per-analysis |
| Context window & summarisation | Config (`context_window_messages`, `summarize_after_messages`) | Keep long meta-analysis threads from blowing past model limits |
| Runaway-loop bounds | Config (`max_model_requests`, `agent_timeout_seconds`) | Hard ceilings on per-turn cost |

---

# Appendix B — Compliance & guardrails at a glance

- **No fabricated citations.** Clinical PMIDs are only emitted when they originated from a live `search_papers` call. The general Q&A flow has a separate regex validator that rejects answers attempting to quote effect sizes, PMIDs, or guideline statements not present in the conversation's tool results.
- **No silent skipping.** Workflow specialists hide downstream tools until the upstream stage is confirmed by the user — e.g. `sandbox_exec` is invisible to the model until an extraction table exists.
- **No host access from generated code.** The Python sandbox runs in a Docker container with networking disabled, the script and inputs mounted read-only, and only a designated output directory writable.
- **No plaintext secrets.** API keys, model credentials, and any PHI live in your secrets manager and are read at runtime — never persisted in config files or the local database.
- **No third-party LLM intermediary.** Model calls go directly to your AWS Bedrock account; you choose the region; the assistant has no shared multi-tenant cloud.
- **Full reproducibility.** Threads persist every user message, model message, tool call, tool result, and structured output. The same thread re-opened tomorrow reproduces today's forest plot.
