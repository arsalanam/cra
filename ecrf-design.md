# eCRF System — Design Document

**Status:** design / pre-planning. This document is the first deliverable for the
electronic Case Report Form (eCRF) subsystem. It defines *what we are building and
why*, the standards we hold ourselves to, and the architecture and data model — so a
detailed implementation plan can be written against it next. It is forward-looking and
will evolve; it does **not** describe code that exists yet.

**Relationship to other docs:** extends `architecture.md` (which already names the
*eCRF Renderer* and *Data Collector* containers and flags the eCRF data-residency open
question). This document **resolves that open question** (see Decision D2). Aligns with
the ship-gate and compliance posture implied by `feature-guide.md`.

---

## 1. Purpose & scope

A Case Report Form (CRF) is the instrument that captures, per study subject, the data a
clinical study's protocol requires. An **electronic** CRF (eCRF) inside an Electronic
Data Capture (EDC) system replaces paper: it enforces validation at the point of entry,
keeps a tamper-evident audit trail, supports query/discrepancy resolution, and produces
regulator-ready exports.

This subsystem delivers two distinct runtimes:

1. **Authoring (design-time):** a study designer builds a CRF — its sections, fields,
   data types, controlled vocabularies, visit schedule, and validation rules — and
   publishes a versioned, immutable **form definition**. AI assistance drafts a first
   version from the study protocol.
2. **Collection (run-time):** a *separate* runtime renders a published form definition
   and lets end users submit subject data against a study — with validation, audit, and
   electronic signatures — without any access to the authoring tools or the broader
   research application.

The build-time/run-time split is deliberate and is the core architectural idea: a CRF is
**designed once, published, then executed many times** by different people in a
controlled environment.

---

## 2. Goals & non-goals

### Goals (v1)
- Author CRFs: forms, sections, items (fields), data types, controlled vocabularies
  (code lists), visit/event schedule, and an edit-check (validation) engine.
- AI-assisted authoring: draft a CRF from a study protocol (the `ecrf_design`
  specialist), human-reviewed before publish.
- Versioned, immutable published form definitions; controlled mid-study amendments.
- A separate data-collection runtime supporting **two entry personas**:
  **site staff (EDC)** and **participants (ePRO)**.
- Validation at entry (field, cross-field, cross-form, cross-visit) with automatic and
  manual **query** generation and resolution.
- A **21 CFR Part 11 / ALCOA+-aligned** architecture: immutable audit trail, electronic
  signature hooks, RBAC, time-stamping, lock/freeze/sign-off hierarchy.
- Subject data (PHI) isolated in a **dedicated clinical-data datastore**.
- Standards-aligned export: CDISC **ODM-XML**, **Define-XML**, and CSV; **CDASH**-aligned
  item naming.

### Non-goals (v1 — candidates for later phases)
- Formal computer-system validation (CSV: IQ/OQ/PQ) and a signed validation pack. The
  architecture is *built to be validatable*; the formal ceremony is deferred until a
  pilot requires it (Decision D3).
- Randomization / trial supply management (IWRS/RTSM).
- Medical coding dictionaries (MedDRA for AEs, WHODrug for medications) — integration
  seam only.
- Offline/disconnected data entry with sync (noted as a risk for ePRO; not v1).
- Multi-tenant isolation (single-tenant per `architecture.md`).
- Lab data integration (HL7/FHIR/LOINC feeds), central imaging, eConsent workflows.

---

## 3. Standards & compliance basis

We adopt established clinical-research standards rather than inventing our own. The
system is designed to satisfy them; formal validation is phased (D3).

| Standard | What it governs | How we use it |
|---|---|---|
| **CDISC ODM-XML** | Vendor-neutral interchange of CRF *definitions*, clinical *data*, audit, and admin metadata | Export/import boundary format (not our internal storage — see D1) |
| **CDISC CDASH** | *What* to collect on CRFs; standard item naming/structure | Item naming + starter form templates |
| **CDISC SDTM / Define-XML** | Tabulation for regulatory submission + its metadata | Downstream export target |
| **21 CFR Part 11** | Electronic records & signatures (US FDA) | Audit trail (§11.10(e)), access controls (§11.10(d)), e-signatures (§11.50/11.70/11.200) |
| **EU Annex 11** | Computerised systems (EU equivalent of Part 11) | Same controls satisfy both |
| **ICH E6 (R2/R3) — GCP** | Good Clinical Practice; data integrity & sponsor oversight | Roles, source-data verification, essential-record retention |
| **ALCOA+** | Data-integrity principles | First-class design constraint (§9.3) |
| **HIPAA / GDPR** | PHI / personal-data protection | PHI isolation (D2), encryption, consent for ePRO |

**ALCOA+** — every captured datum must be **A**ttributable, **L**egible,
**C**ontemporaneous, **O**riginal, **A**ccurate, plus **C**omplete, **C**onsistent,
**E**nduring, and **A**vailable. This is the acceptance bar for the data model and audit
trail, not an afterthought.

---

## 4. Key design decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | **JSON-native internal model; CDISC ODM-XML / Define-XML export at the boundary; CDASH-aligned item naming.** | Fast to build, natural to render and to query in Postgres; ODM/Define-XML are interchange formats, not storage formats. We stay interoperable where it matters (import/export) without paying ODM's authoring/query overhead internally. |
| **D2** | **Subject data (PHI) lives in a dedicated clinical-data datastore (separate Postgres), distinct from the research-app database.** Resolves `architecture.md`'s open eCRF-residency question. | Strongest PHI isolation: own credentials, backup/retention, network policy, and data-residency region. Clean blast-radius and audit boundary. The research app never holds subject PHI. |
| **D3** | **Part 11 / ALCOA+-*aligned* architecture in v1; formal CSV (IQ/OQ/PQ) deferred.** | Build immutable audit trail, e-signature hooks, RBAC, versioning, and edit checks from day one so the system is *validatable*. Defer the formal, costly validation ceremony until a real pilot needs it. |
| **D4** | **Two entry personas: site staff (EDC) and participants (ePRO).** | Covers the classic site-coordinator data-entry model and direct patient-reported outcomes. Shared form-definition + audit core; the two surfaces differ only in auth, rendering, and the subset of forms they can touch. |
| **D5** | **Collection runtime talks to the platform only via the agent's REST API, never the DB directly** (per `architecture.md` line 125). | Keeps the container boundary clean; the API is the single enforcement point for authz, validation, and audit writes. |
| **D6** | **AI authoring operates on protocol/metadata only — never on subject PHI.** | The `ecrf_design` specialist uses Bedrock (an external API). Subject data must never cross that boundary. AI drafts *form definitions*; it has no path to the clinical-data store. |

---

## 5. System overview — three planes

```
   ┌──────────────────────── AUTHORING PLANE (design-time) ───────────────────────┐
   │  Study designer (browser)                                                     │
   │      │                                                                        │
   │      ▼                                                                        │
   │  eCRF Renderer container  ──"draft CRF from protocol"──▶  Agent API           │
   │   • form-definition builder UI                          (ecrf_design          │
   │   • renders/validates a definition                       specialist, Bedrock) │
   │   • publish → immutable versioned FormDefinition                              │
   └───────────────────────────────────┬──────────────────────────────────────────┘
                                        │ publishes definition (JSON, ODM-exportable)
                                        ▼
                          ┌──────────────────────────────┐
                          │   Agent (FastAPI)  — control  │  authz · validation · audit
                          │   plane + REST API            │  · publish registry · export
                          └───────────────┬───────────────┘
                                          │ REST only (D5)
   ┌──────────────────────── COLLECTION PLANE (run-time) ─┴────────────────────────┐
   │  Data Collector container                                                     │
   │   • Site EDC surface  (CRC / investigator)     ┐                              │
   │   • ePRO surface      (participant)            ┘ render published def + submit │
   │                                                                               │
   │            writes subject data + audit + e-sign  ──▶ Agent API ──▶            │
   └───────────────────────────────────────────────────────────────┬──────────────┘
                                                                     ▼
                                                   ┌────────────────────────────────┐
                                                   │ Clinical-Data Store (Postgres) │
                                                   │  PHI · study/site/subject/      │
                                                   │  visit/form-instance/item-data  │
                                                   │  audit · queries · signatures   │
                                                   │  (own creds/backup/residency)   │  (D2)
                                                   └────────────────────────────────┘
```

- **Authoring plane** — design-time. The *eCRF Renderer* container hosts the form
  builder and calls the agent's `ecrf_design` specialist to draft from a protocol.
  Output: a published, versioned `FormDefinition`.
- **Control plane** — the existing agent (FastAPI). Single authority for authentication,
  authorization, validation, audit writes, the published-definition registry, and
  exports. Both other planes go through it.
- **Collection plane** — run-time. The *Data Collector* container renders published
  definitions and captures data via two surfaces (EDC, ePRO). It never touches a
  database directly (D5).

---

## 6. Architecture & topology

Reuses the platform rails in `architecture.md`; adds two containers and one datastore.

- **eCRF Renderer (container)** — authoring UI + definition rendering/validation; AI
  draft via the agent API. Stateless; published definitions persist via the agent.
- **Data Collector (container)** — run-time capture; EDC + ePRO surfaces. Stateless;
  all writes go through the agent API. Horizontally scalable.
- **Agent (existing FastAPI)** — gains an eCRF control-plane module: definition
  registry, publish/version lifecycle, capture endpoints, edit-check execution, audit
  writer, query workflow, export jobs. Owns the connection to the clinical-data store.
- **Clinical-Data Store (new Postgres)** — PHI, audit, queries, signatures (D2). Only
  the agent connects to it. Separate from the research-app Postgres+pgvector.
- **Shared FS document cache** — eCRF *attachments* (e.g. uploaded source images/PDFs
  linked to a form instance) follow the existing shared-FS pattern, content-addressed.
- **Cognito** — identity for staff; an additional path for participants (ePRO) — see §8.
- **Deploy** — additional Deployments in EKS (`ecrf-renderer`, `data-collector`),
  added to the compose stack for local dev. The clinical-data Postgres is a second
  managed instance in prod, a second compose service locally.

**PHI boundary:** the AI/Bedrock path (authoring) is wholly inside the authoring/control
plane and sees only protocol + definition metadata. There is **no path** from the
clinical-data store to Bedrock or to the RAG/library corpus (D6). Subject data is never
embedded or indexed.

---

## 7. Roles & access control

Cognito hosts identity; the app owns authorization (per `architecture.md`). eCRF adds
**study-scoped** roles on top of the existing global RBAC. A user's permissions are
`(global role) × (study, site) assignment`.

| Role | Plane | Can |
|---|---|---|
| **Study Designer / Sponsor** | Authoring | Create/edit/publish form definitions; define visit schedule + edit checks |
| **Principal Investigator (PI)** | Collection | Oversee a site; sign off subject/visit data; resolve queries |
| **Clinical Research Coordinator (CRC)** | Collection (EDC) | Enter/edit subject data at assigned site(s); answer queries |
| **Data Manager** | Control | Raise/close queries; lock/freeze data; run exports; review audit trail |
| **Monitor / CRA** | Collection (read + SDV) | Source-data verification; raise queries; read-only on data |
| **Participant** | Collection (ePRO) | Enter only their own assigned ePRO forms; no access to other subjects or staff tools |
| **Auditor / read-only** | All | Read data + audit trail; no writes |

- **Separation of duties:** the person entering data cannot also be the sole signer of
  its correctness where the protocol requires independent sign-off; query
  raise/resolve are distinct permissions.
- **Site scoping:** CRC/PI/Monitor are scoped to assigned `Site`s; a coordinator at site
  A cannot see site B's subjects.
- **Least privilege for participants:** an ePRO participant is bound to a single
  `Subject` and only the ePRO-flagged forms for their current visit.

---

## 8. Identity for the two personas

- **Site staff (EDC):** existing **Cognito** users + study/site role assignments.
  Standard OIDC/JWT, MFA per institutional policy. Reuses the platform's auth.
- **Participants (ePRO):** a *separate* enrolment path — participants are not research
  staff and must not appear in the staff directory. Options to settle in planning
  (Open Question O1): a dedicated Cognito user pool, magic-link/OTP tokens scoped to a
  subject+visit, or invitation codes issued by the site. Whatever the mechanism:
  participant sessions are bound to a single subject, consent is captured before first
  entry, and the same audit/ALCOA+ rules apply.

---

## 9. Domain & data model

Two model families, in two stores. The **definition** model lives with the research app
(it's metadata, not PHI); the **clinical-data** model lives in the dedicated store (D2).

### 9.1 Form-definition model (metadata; JSON-native, D1)

```
Study
 └─ ProtocolVersion
     ├─ VisitSchedule          (planned visits/events + windows)
     │    └─ ScheduledEvent    (e.g. Screening, Baseline, Week 4, …)
     └─ FormDefinition (versioned, immutable once published)
          ├─ Section / ItemGroup
          │    └─ Item (field)
          │         ├─ data_type   (text, integer, decimal, date, datetime,
          │         │                boolean, single-select, multi-select, file)
          │         ├─ cdash_var    (CDASH-aligned name)
          │         ├─ units, format, required, derivation?
          │         ├─ CodeList ref (controlled vocabulary)
          │         └─ EditCheck[]  (validation rules — §10)
          └─ form_event_map        (which forms appear at which scheduled events)
```

- A **FormDefinition** is immutable once **published**; edits create a new version.
- The whole thing is JSON internally and exports cleanly to ODM-XML (Study/MetaDataVersion/
  FormDef/ItemGroupDef/ItemDef/CodeList) and Define-XML.

### 9.2 Clinical-data model (PHI; dedicated store, D2)

```
StudyDeployment            (binds a published ProtocolVersion to live data)
 └─ Site
     └─ Subject             (de-identified subject id; PHI minimised, see §11)
          └─ EventInstance  (an actual occurrence of a ScheduledEvent for a subject)
               └─ FormInstance   (status: blank → in-progress → complete → signed → locked)
                    └─ ItemData   (one captured value)
                         ├─ value (+ typed columns / coded value)
                         ├─ entered_by, entered_at
                         ├─ AuditEntry[]   (§9.3 — every create/change/delete)
                         ├─ Query[]        (§10/§12 — discrepancies)
                         └─ Signature[]    (§9.4)
```

- **FormInstance status** drives the lock/freeze/sign-off hierarchy
  (form → visit/event → subject → study).
- Every `ItemData` references the **exact FormDefinition version** it was captured
  under, so mid-study amendments never retroactively reinterpret existing data.

### 9.3 Audit trail (ALCOA+, Part 11 §11.10(e))

A dedicated, **append-only** `AuditEntry` per data-affecting action:

```
AuditEntry
  entity_ref      (which ItemData / FormInstance / Subject)
  action          (create | update | delete | sign | lock | unlock | query-*)
  old_value, new_value      (never overwrite — prior value preserved)
  reason_for_change         (required on update/delete after first save)
  actor (user id + role), actor_site
  timestamp (server, UTC, trusted)
  source        (EDC | ePRO | import | system-derived)
```

- **Computer-generated, time-stamped, secure, and non-obscuring** — changes never
  overwrite prior values; the full history is reconstructable. Append-only enforcement
  (no UPDATE/DELETE on the audit table; DB-level grants forbid it).
- A **reason for change** is mandatory for any edit after a value's first commit.

### 9.4 Electronic signatures (Part 11 §11.50/11.70/11.200)

```
Signature
  signed_entity   (FormInstance | EventInstance | Subject casebook)
  signer (user), meaning (e.g. "PI sign-off: data accurate & complete")
  signed_at (UTC), method (re-authentication: user id + password, MFA-capable)
  manifestation   (printed name + datetime + meaning rendered on the record)
  binding_hash    (links signature to the exact data state signed — §11.70)
```

- Non-biometric signatures use **two identification components** (e.g. credential +
  password) per §11.200; re-authentication at signing.
- Signature is **bound to the data state** at signing time; any later change invalidates
  the signature and is itself audited.

---

## 10. Validation / edit-check engine

Edit checks are part of the **definition** (versioned) and executed by the control plane
on submit (and previewed client-side for UX). Tiers:

1. **Field-level:** type, required, range, pattern, code-list membership, date sanity.
2. **Cross-field (same form):** e.g. `diastolic ≤ systolic`; "if pregnant = yes then sex
   = female".
3. **Cross-form / cross-visit:** e.g. visit date ≥ enrollment date; AE onset within
   study window.
4. **Derivations:** computed read-only items (e.g. BMI from height/weight).

- Rule representation: a small, **declarative, sandboxable** expression language (not
  arbitrary code) stored in the definition — auditable and safe to run server-side.
- A failed *hard* check blocks save; a failed *soft* check raises an automatic **query**
  (§12) but allows the value (with reason), matching real EDC behaviour.

---

## 11. Privacy, PHI minimisation & security

- **PHI isolation (D2):** subject data in a dedicated Postgres with its own credentials,
  encryption at rest, backup/retention, network policy, and residency region.
- **Minimise PHI:** subjects identified by a study-issued subject id; direct identifiers
  avoided in the eCRF where the protocol allows; any necessary identifiers live only in
  the clinical-data store.
- **No PHI to the LLM or RAG (D6):** the AI authoring path sees protocol/definition
  metadata only. The clinical-data store has no path to Bedrock or the library/RAG
  index. Subject data is never embedded.
- **Encryption:** TLS in transit everywhere; at-rest encryption on the clinical-data
  store and the attachments FS.
- **ePRO consent:** electronic consent captured (and audited) before a participant's
  first data entry; GDPR/HIPAA basis recorded.
- **Secrets:** clinical-data DB credentials via AWS Secrets Manager + External Secrets
  Operator (never plaintext), consistent with the platform.

---

## 12. Query / discrepancy management

```
Query: open → answered → closed   (or → re-opened)
  raised_by (auto-from-edit-check | data manager | monitor)
  target (ItemData), text, status, priority
  responses[] (CRC answers; data manager/monitor adjudicates)
  every transition audited
```

- Auto-queries from soft edit-check failures; manual queries from data managers/monitors
  during review and SDV.
- A form/visit cannot be **signed/locked** with open hard queries (configurable policy).

---

## 13. Lifecycle & versioning

- **Definition lifecycle:** `draft → in-review → published (immutable) → superseded`.
  Publishing freezes a version and registers it for deployment.
- **Mid-study amendments:** a protocol/form change creates a **new** FormDefinition
  version; existing data keeps its original version reference; a documented migration
  policy governs whether/which fields re-collect.
- **Data lifecycle:** `blank → in-progress → complete → signed → locked → (database
  lock at study close)`. Unlock requires elevated permission + reason + audit.

---

## 14. Data flow & integration

- **Authoring:** designer ↔ eCRF Renderer ↔ agent (`ecrf_design`) → published definition
  registry.
- **Deployment:** a published ProtocolVersion is bound to a `StudyDeployment` with sites
  and a visit schedule.
- **Capture:** Data Collector renders the published definition → user submits → agent
  validates + writes ItemData + AuditEntry (+ Signature) to the clinical-data store.
- **Export:** ODM-XML (definition + data + audit), Define-XML (metadata), CSV, and a
  CDASH→SDTM mapping path for submission tabulation. Exports are themselves audited and
  permission-gated.

---

## 15. Non-functional requirements

- **Integrity first:** no data-affecting path may bypass the audit writer.
- **Availability:** collection runtime is the patient/site-facing critical path; target
  high availability; degrade reads before writes.
- **Performance:** form render + submit interactive (<1s typical); export is async/batch.
- **Scalability:** Data Collector horizontally scalable; clinical-data store sized for
  per-study subject×visit×item volume.
- **Backup/DR:** clinical-data store PITR + tested restore; RPO/RTO per customer tier
  (inherits `architecture.md`'s open item).
- **Retention:** GCP essential-record retention (years post study close); audit trail
  enduring and available for the full period.

---

## 16. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Audit/ALCOA+ gaps discovered late | Audit writer + append-only store built first; every write path tested against it |
| PHI leaking to LLM/RAG | Hard architectural boundary (D6); clinical-data store has no Bedrock/RAG path; reviewed in design |
| ePRO auth/consent complexity | Settle participant identity model early (O1); start with site-mediated enrolment |
| Scope creep (randomization, coding, labs) | Explicit non-goals (§2); integration seams only |
| Edit-check rule engine becoming arbitrary code | Declarative, sandboxed expression language; no eval of free code |
| Formal validation (CSV) demanded by a pilot sooner than expected | Architecture is validatable by design (D3); CSV can be layered without rework |

---

## 17. Open questions (resolve during planning)

- **O1 — Participant (ePRO) identity:** dedicated Cognito pool vs magic-link/OTP vs
  site-issued codes. (§8)
- **O2 — Offline data entry:** do field sites need disconnected entry + sync? (deferred,
  but affects collector design if in scope)
- **O3 — Edit-check expression language:** adopt an existing safe expression lib vs a
  minimal in-house grammar.
- **O4 — Coding dictionaries:** when do MedDRA/WHODrug integrate (licensing + workflow)?
- **O5 — Clinical-data residency region(s):** per-customer region pinning for the
  dedicated store.
- **O6 — e-signature mechanism specifics:** credential re-auth vs MFA step-up; legal
  manifestation text per study.
- **O7 — Database/study lock + unblinding workflow** details.
- **O8 — Formal CSV trigger:** which pilot/customer milestone flips D3 from "aligned" to
  "validated".

---

## 18. Delivery roadmap (sketch — detailed plan is the next deliverable)

Indicative phases, smallest-shippable-first; the *next* document turns these into a
concrete WBS.

- **E0 — Definition model + registry:** JSON form-definition schema, versioning,
  publish lifecycle, ODM/Define-XML export. (No capture yet.)
- **E1 — Clinical-data store + capture core:** dedicated Postgres, study/site/subject/
  event/form-instance/item-data model, **audit writer first**, basic EDC submit via the
  agent API.
- **E2 — Edit-check engine + queries:** validation tiers + query workflow.
- **E3 — eCRF Renderer (authoring UI) + AI draft:** form builder + `ecrf_design`
  specialist (protocol → CRF draft).
- **E4 — Data Collector surfaces:** site EDC UI, then participant ePRO (with O1 settled)
  + consent.
- **E5 — Signatures, lock/freeze/sign-off hierarchy, exports.**
- **E6 — Hardening toward validation:** SDV tooling, full ALCOA+ review, CSV readiness.
- **E7 — Formal validation pack (✅ shipped 2026-05-30):** Part 11 §11.200 password
  re-authentication at signing; deployment-wide `StudyLock` (data_manager-gated) that
  blocks all writes / signatures / SDV while active; auto-generated IQ snapshot (versions,
  pinned deps, Cognito ID, audit-trigger detection); pytest-driven OQ over a 14-requirement
  Requirements Traceability Matrix tied to Part 11 / ICH E6 / ICH E2A / ALCOA+; PQ runbook
  PDF for customer-site execution. Admin downloads `iq.pdf`, `oq.pdf`, `pq.pdf`, and
  `bundle.zip` from `/api/admin/validation-pack/*`.
- **E7b — Full-surface lock gates (✅ shipped 2026-05-30):** AE / deviation / CAPA / query
  write-paths also refuse while the study is locked — 13 new gate calls across record /
  classify / code / mark-reported AE, raise / respond / close query, record / classify /
  close deviation (subject- + deployment-scoped), and add / complete CAPA. RTM gains
  LOCK-004 covering the new gates. CDISC derivation is deliberately allowed post-lock
  (lock-then-derive is the regulator-default flow).

---

## 19. Glossary

**CRF** Case Report Form · **eCRF** electronic CRF · **EDC** Electronic Data Capture ·
**ePRO** electronic Patient-Reported Outcome · **PI** Principal Investigator ·
**CRC** Clinical Research Coordinator · **CRA** Clinical Research Associate (monitor) ·
**SDV** Source Data Verification · **ALCOA+** data-integrity principles ·
**CDISC** Clinical Data Interchange Standards Consortium · **ODM** Operational Data Model
(XML) · **CDASH** Clinical Data Acquisition Standards Harmonization · **SDTM** Study Data
Tabulation Model · **Define-XML** dataset metadata standard · **CSV** Computer System
Validation (IQ/OQ/PQ) · **IWRS/RTSM** randomization & trial supply · **GCP** Good
Clinical Practice (ICH E6) · **PHI** Protected Health Information.
