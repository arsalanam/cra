# PHI & HIPAA posture

*Trial-readiness item T7 (see [`trial-readiness.md`](trial-readiness.md)).
Drafted 2026-07-19 from a code-verified data inventory. This is an
engineering compliance assessment, **not legal advice** — the deploying
institution's privacy officer / counsel signs off before real
participants connect.*

## The one-line answer

CRA cannot claim "we only handle de-identified data." The clinical store
holds dates throughout and direct contact identifiers in one table, so it
must be treated as **PHI-capable**. Whether HIPAA legally *binds* a given
deployment depends on who runs it (§3) — but the platform's posture is
the same either way: Security-Rule-aligned controls, deploy-in-your-own-
AWS, and a documented split of responsibilities (§5).

## 1. Data inventory (verified in code, 2026-07-19)

Two separate databases by design:

**Research store** (threads, papers, meta-analyses, budgets, portfolio,
citations) — no participant data. Literature, aggregate statistics,
platform accounting. *Not PHI.*

**Clinical store** (dedicated Postgres; eCRF/EDC, subjects, visits,
labs, safety, randomisation, drug accountability):

| Data | Where | Classification |
|---|---|---|
| Subject records | `subjects` — `subject_code`, status, `baseline_date`, site | Coded data with dates → **limited-data-set-like**, not de-identified |
| Visit / lab / AE dates | throughout (`planned_visits`, `lab_results.collected_at`, AE onset) | Dates tied to an individual = HIPAA Safe-Harbor identifier category |
| Participant email / phone | `participant_contacts` (visit reminders, ePRO channel) | **Direct identifiers — outright PHI** when subjects are real people |
| Lab results | `lab_results` + `raw_segment_json` (OBX test segment only) | Coded; no demographics |
| Inbound subject id | `lab_results.subject_code_hint` (verbatim PID-3) | **Risk**: becomes an MRN if the site's LIS sends MRNs (gap G3) |
| ePRO access tokens | stored **hashed** | control, not identifier |
| Rollups (portfolio, multi-site) | counts only, PHI-minimised by design | Not PHI |

**What deliberately never persists:** HL7 v2 PID demographics — the
parser reads only PID-3 (identifier) and discards PID-5/7/11 (name,
DOB, address); raw lab payloads are not stored (SHA-256 + size only).

### Safe Harbor test, honestly applied

HIPAA de-identification (45 CFR §164.514(b)) requires removing all 18
identifier types, including *all dates* (except year) and *any* contact
info. CRA's clinical data model fails this on dates alone, and
`participant_contacts` fails it outright. The realistic classification
of a CRA study dataset is a **limited data set** (coded + dates) — which
a covered entity may only disclose under a **Data Use Agreement** — plus
one table of directly identifiable contact data that goes beyond a
limited data set.

## 2. What CRA is NOT

- CRA is **not a covered entity** — it is software.
- CRA (as currently distributed) is **not a business associate** — it
  deploys into the customer's own AWS account; no participant data ever
  reaches the vendor. If CRA were ever offered as a hosted service
  processing PHI on a covered entity's behalf, the operator would become
  a business associate (BAA + direct Security Rule obligations) — a
  deliberate non-goal of the current architecture.

## 3. Who HIPAA binds, by deployment scenario

| Deployer | HIPAA applies? | What governs |
|---|---|---|
| Hospital / academic medical center research group (covered entity or hybrid entity) | **Yes** — their use/disclosure of PHI; CRA runs inside their compliance boundary | Privacy + Security Rules, their policies, IRB authorization or waiver |
| Sponsor / CRO that is not a covered entity | Not directly — but every **site/lab disclosing to them is** a covered entity and may only disclose under: patient authorization, IRB/Privacy-Board waiver, limited data set + DUA, or true de-identification | Common Rule (45 CFR 46), FDA 21 CFR 50/56/312, Part 11 (built in), state privacy law, GDPR if EU sites |
| Anyone using synthetic / test subjects (the trial run) | No — no real PHI exists | Good hygiene anyway; posture becomes real when real sites connect |

## 4. Security Rule alignment — what exists today

| Safeguard | Control in CRA |
|---|---|
| Access control | Cognito SSO (OIDC), RBAC (~105 permissions / 12 roles), scope-typed grants, separation-of-duties matrices |
| Unique user identification | Cognito identities bound to local users (`cognito_sub`) |
| Audit controls | Append-only audit tables; Part 11 e-signatures with password re-auth; PI countersign |
| Integrity | Monotonic state machines, study-lock gates, SHA-256 idempotent ingest |
| Transmission security | HTTPS at the ALB (self-signed until T5 lands ACM) |
| Minimum necessary | PHI-minimised rollups (counts only); parser discards demographics; blinding masks |
| Isolation | Dedicated clinical Postgres separate from the research store; model-authored code confined to a network-disabled sandbox |
| Person/entity authentication | Password re-auth on signatures and study lock |
| Contingency | Compose/Postgres volumes; deployer owns backup/DR (see §5) |

## 5. Shared-responsibility matrix

**Platform provides:** everything in §4, plus the T7 hardening items
(§6), plus this document.

**Deployer owns:**

- **AWS BAA** with Amazon, and keeping PHI on HIPAA-eligible services
  (Bedrock, SES, RDS/EC2 are eligible)
- Legal basis for each data flow: authorization, IRB waiver, or DUA for
  limited data sets received from covered entities
- Workforce training, sanctions policy, designated privacy/security
  officers
- Breach risk assessment + notification procedures (45 CFR §§164.400ff)
- Backup, disaster recovery, and retention schedules
- Physical safeguards of anything outside AWS (workstations, exports —
  note every PDF/DOCX/Define-XML export leaves the platform's controls)

## 6. Gaps and remediation (T7 build items)

| # | Gap | Remediation | Status |
|---|---|---|---|
| G1 | Encryption at rest for the clinical Postgres volume not verified/documented | Verify + document for compose (host volume) and EC2 (EBS encryption in tofu) | TODO |
| G2 | Which subject-level fields may enter model prompts is undocumented (Bedrock is BAA-eligible, but the flows must be written down) | Audit trial_stats / csr_drafter / safety prompt inputs; document; minimise where cheap | TODO |
| G3 | `subject_code_hint` stores inbound PID-3 verbatim — an MRN leak if a site LIS sends MRNs | Validate against the deployment's subject-code pattern; quarantine or hash non-matching values | TODO |
| G4 | `participant_contacts` (email/phone) stored plaintext | Minimum-necessary review; consider column encryption; rides the T2 secrets gate | TODO (with T2) |
| G5 | TLS terminates on a self-signed cert | T5 (ACM + domain) | TODO (T5) |
| G6 | PHI *read* access is not comprehensively logged (audit covers writes/signatures) | Assess read-logging on subject/lab endpoints; document what is and isn't logged | TODO |

## 7. Claims language

Approved: *"CRA supports HIPAA-aligned deployment inside your own AWS
account"* — always paired with the shared-responsibility split.

Never: "HIPAA compliant" as a blanket product claim (compliance is a
property of a deployment + its organisation, not of software), "fully
de-identified", or "no PHI" (contradicted by §1).

## Maintenance

Update §1 when the clinical schema changes (new tables holding
participant-adjacent data must be classified here). Close §6 items with
date + commit. Institutional reviewers get this document plus the
IQ/OQ/PQ validation pack.
