# Trial-readiness TODO

*The gate list for opening the EC2 deployment to a real trial-run audience.
Created 2026-07-09 after admin login was verified working on EC2. Each item
gets designed + delivered one by one; update Status as items land.*

*Distinct from `roadmap.md` (feature roadmap): this is the milestone
checklist for ONE event — first external users.*

## At a glance

| # | Item | Status | Depends on |
|---|------|--------|-----------|
| T1 | Trial-run spend quota (per-account USD budget, all metered spend) | ✅ BUILT 2026-07-19 (`feat/t1-spend-quota`, pending verify + merge) — [t1-spend-quota.md](t1-spend-quota.md) | — |
| T2 | Paywalled sources (Lancet/Elsevier…) + credential encryption | TODO | secrets encryption (part of this item) |
| T3 | Cognito production posture (real-user invites) | TODO | T4 for invite deliverability |
| T4 | SES domain email for invites | TODO | T5 (domain) |
| T5 | Domain + ACM + Route 53 | TODO | user registers/picks domain |
| T6 | Pending eCRF / data-collection gaps (trial-relevant subset) | TODO (needs pruning) | — |

**Recommended order:** T5 → T4 → T3 form one infra arc (domain has external
lead time — start it first). T1 is pure app work and must be live BEFORE
invites go out (external users = real spend). T2 next (includes the
encryption gate). T6 in parallel as small picks.

---

## T1 — Trial-run spend quota

> **Design doc: [`t1-spend-quota.md`](t1-spend-quota.md)** (2026-07-19).
> Decisions locked: per-Account budget, all metered spend counted
> (Bedrock + Tavily + embeddings + vision), hard 429 stop.

**Goal.** A hard USD budget for the trial period — e.g. $500 across Bedrock
+ Tavily — measured from a configured trial start date, enforced before
each turn, visible to admins.

**Already have:**
- `config/bedrock_pricing.py` — pricing-as-code per model family
- `services/cost_rollup.py` — USD aggregation over `done` events (which
  carry `model_id` + per-tool usage counts)
- `services/quota.py` — daily token pre-flight (`enforce_daily_token_quota`,
  429 with reset time) — the enforcement pattern to copy
- `portfolio.html` cost section — the display surface

**To build:**
- Settings/DB: `trial_start_date`, `trial_budget_usd` (0 = disabled)
- Tavily pricing entry (per-search $) + count `web_search` calls from
  `tool_usage` in done events into the rollup
- Cumulative-since-start USD query + pre-flight enforcement (429 with
  budget-used message, mirroring the daily-quota path)
- Warning threshold surfaced in the UI (e.g. banner at 80%)
- Admin view: budget consumed / remaining / burn rate on portfolio.html

**Design decisions to make:**
1. Scope: one budget per install (simple) vs per Account/Trial (the
   RBAC "quota-tier ceilings" follow-up)? → propose per-install for the
   trial run; per-tier later.
2. What counts: Bedrock turns + Tavily searches definitely; Titan
   embeddings (library uploads) and the vision model should also be
   priced in.
3. Hard stop vs degrade (e.g. general_qa-only after budget)? → propose
   hard stop (429) — same posture as the daily quota.

## T2 — Paywalled paper sources + credential encryption

**Goal.** Admin can add sources like Lancet / Elsevier (ScienceDirect,
Scopus) with API keys / credentials, per-source throttling, rate limits,
and usage limits — safely on a public deployment.

**Already have:**
- `PaperSource` Protocol + `registry.py` builder map + `init_db` seeding —
  adding a source is a documented, proven path (pubmed + europepmc)
- `source_configs` DB table + `/admin.html` runtime config UI +
  `GET/PUT /api/admin/sources`
- `RateLimitConfig` with pluggable auth strategies (query-param / header /
  bearer) + per-source rate limits + retry/backoff (Phase-4 hardened)

**To build:**
- **SECURITY GATE (must land first):** `source_configs.api_key` is
  plaintext and returned verbatim by the admin GET — acceptable on
  localhost, NOT on EC2. Encrypt at rest (KMS envelope or move secrets to
  AWS Secrets Manager per the ship-gate) + mask in GET responses
  (write-only field, show last-4).
- Per-source **usage limits** (e.g. N calls/day) on top of rate limits —
  publishers meter by quota, not just req/s.
- First publisher backend(s): Elsevier APIs cover both ScienceDirect
  full text and Scopus search (Lancet is an Elsevier title); needs
  api_key + institutional token params → extend `SourceConfig` for
  extra per-source params (JSON field).
- Admin UI: add-source form (today only edits seeded sources?verify),
  per-source health/entitlement test button.

**Design decisions to make:**
1. Which publisher first (Elsevier covers Lancet; Wiley/Springer later)?
2. Metadata search only vs full-text retrieval (entitlement-dependent)?
3. Secrets Manager vs KMS-encrypted DB column?

## T3 — Cognito production posture

**Goal.** Real trial users sign up via invite and land in the onboarding
flow; no dev fallbacks reachable.

**Already have:** all 5 Cognito phases validated; multi-tenant pool setup
script (idempotent by name); ALB + app auth working on EC2 (admin login
verified 2026-07-09); invite + onboarding + RBAC + SoD flows (U1–U4).

**To build / verify:**
- Decide pool strategy for the trial (reuse current test pool vs fresh
  trial pool via the setup script)
- Invite deliverability: Cognito's default email is a no-reply address
  with a ~50/day cap → configure Cognito to send via SES (needs T4)
- Callback URLs move to the real domain (needs T5)
- Sweep for dev artifacts: `default-user` placeholder paths, seeded test
  users, password policy, token lifetimes, MFA decision
- End-to-end drill: invite → email → first login → onboarding wizard →
  clinical-write gate, on EC2 with a non-admin role

## T4 — SES domain email

**Goal.** Invites and visit reminders sent from `@<our-domain>` reliably.

**Already have:** `services/reminders.py` already sends via SES when
`AWS_SES_FROM_EMAIL` is set (dry-run otherwise); APScheduler wiring.

**To build:**
- SES domain identity + DKIM (Route 53 records — needs T5), SPF/DMARC
- Request SES **production access** (sandbox only sends to verified
  addresses — this has AWS review lead time, start early)
- Point Cognito email at SES (custom FROM address) for invites
- Set `AWS_SES_FROM_EMAIL` in the EC2 env; verify reminder emails
- Optional: bounce/complaint SNS topic

## T5 — Domain + ACM + Route 53

**Goal.** `https://<trial-domain>` with a real certificate instead of the
raw ALB DNS name + self-signed cert.

**Already have:** OpenTofu stack at `deploy/ec2` (ALB, listeners, Cognito
integration) — additive change.

**To build:**
- User action: pick + register the domain (Route 53 registration or
  delegate an existing domain's NS)
- Tofu: hosted zone (or data-source it), ACM certificate with DNS
  validation, ALB HTTPS listener swaps to the ACM cert, Route 53 alias
  A-record → ALB
- Update `COGNITO_REDIRECT_URI` + Cognito app-client callback URLs +
  any hardcoded ALB URLs (demoguide surface map is docs-only)
- Ride-along fix: persist the deploy key in `user-data.sh.tftpl`
  (`git config core.sshCommand …`) so instance replacement doesn't break
  `git pull` redeploys (found + hand-fixed on the box 2026-07-08)

## T6 — Pending eCRF / data-collection work (trial-relevant subset)

Full deferred inventory lives in `roadmap.md` § "Pickable follow-ups".
Proposed subset that plausibly matters for a live trial run — **prune
this list before building**:

**Propose IN (small, close operational loops):**
- Visit-window violation → auto protocol-deviation (closes calendar ↔
  deviation loop; today manual)
- SUSAR detection flag (fields exist; the tightest regulatory clock
  deserves automatic surfacing)
- `kit_id` pattern enforcement at dispense (typo guard)
- Temperature-excursion → auto deviation/CAPA link
- Per-batch lab error report download (audit artefact)
- CONSORT flow-diagram PDF auto-export from the recruitment funnel
- Frontend card for `kind:"error"` payloads (side item from agent-loop
  work — real users shouldn't see raw debug dumps)
- Document `TAVILY_API_KEY` in `deploy/compose/.env.example`

**Propose OUT for the trial (defer):** SMS via Twilio, ICS attachments,
locale-aware reminders, MedDRA/WHODrug licenses, FHIR EHR pull, OCR,
MLLP listener, unit-conversion table, blinded views, IRT-driven
dispensation, destruction/recall workflows, lay_summary Trial slot.

---

## Maintenance

- Move items to a ✅ line (with date + commit/PR) as they land.
- Design docs per item go in `docs/` and get linked from the item.
- When all T-items are ✅, this document freezes as the trial-launch
  record; post-trial work goes back to `roadmap.md`.
