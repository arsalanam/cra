# RBAC Design

**Status:** design / pre-implementation. A comprehensive role-based access-control
model for the whole platform — the research *skills* and the eCRF/EDC *role
hierarchy* — replacing today's thin `admin` + `data_entry` scheme. Forward-looking;
describes what we will build, so implementation can be planned against it.

**Relationship to other docs:** extends `architecture.md` ("Cognito hosts identity;
the app owns authorisation; RBAC roles in Postgres keyed to the Cognito sub") and
`ecrf-design.md` §7 (which names the eCRF roles this design finally enforces). Closes
the gap between the `feature-guide.md` promise ("role-based access control scopes each
workflow per user") and what's implemented.

---

## 1. Why

Authorization today is three coarse guards (`current_user`, `require_admin`,
`require_data_entry`) over a flat, free-text, **global** `UserRole` table with only
`admin` / `data_entry` / `researcher` ever used. That can't express what the product
needs:

- **eCRF is multi-study, multi-site.** GCP requires separation of duties and site
  isolation: a coordinator at Site A must not see Site B's subjects; a PI signs off
  their study; a monitor reads + verifies but never enters data. Global roles can't say
  "CRC **at Site A of Study X**".
- **Many research skills.** The dispatcher routes to specialists (meta-analysis,
  search-strategy, SR-protocol, risk-of-bias, general Q&A, `ecrf_design`) with **no
  per-skill authorization** — any authenticated user can run any of them.
- **No object ownership.** Any user can read/modify any thread or watch.
- **No role administration.** Roles are granted once at invitation; there's no
  grant/revoke/list for existing users.

---

## 2. Gap summary (from the code review)

| # | Gap | Severity |
|---|---|---|
| 1 | No study/site scoping — `data_entry` acts on every site/study | Critical |
| 2 | eCRF role hierarchy (Designer/PI/CRC/Data Manager/Monitor/Auditor) not modeled | Critical |
| 3 | No per-skill authorization (any user → any specialist) | High |
| 4 | No object ownership (threads/watches shared; hardcoded `default-user`) | High |
| 5 | No grant/revoke/list-users surface; roles set once at invite | High |
| 6 | Free-text roles (typo-prone); quotas global, not per-user; no read-only Auditor | Medium |

---

## 3. Locked decisions

| # | Decision | Why |
|---|---|---|
| **D1** | **Scoped role assignments** — a grant is `(user, role, scope)` where scope ∈ `global` / `study:<id>` / `site:<id>`. Broader scope satisfies narrower checks (`global ⊃ study ⊃ site`). | Only way to express site/study isolation that GCP needs. |
| **D2** | **Permission-based enforcement** — a central role→permission matrix; endpoints + dispatcher check a *permission* (not a role string) via one `require_permission(perm, scope)` seam. | Scales to many roles; one place to reason about access; adding a role never touches endpoints. |
| **D3** | **Skills are gated by permission too** — each specialist needs a `skill.*` permission, enforced in the `/turn` dispatcher. | Delivers the "RBAC scopes each workflow per user" promise; one model for tools + eCRF. |

---

## 4. Model

### 4.1 Scope
```
Scope = global | study:<study_id> | site:<site_id>
```
- A `site` belongs to a `study`; the hierarchy is `global ⊃ study ⊃ site`.
- A permission check for a resource resolves the resource to its `(study_id, site_id)`
  and passes if the user holds the permission via a role assigned at **global**, at that
  **study**, or (for site-level resources) at that **site**.

### 4.2 Storage
Replace the flat `UserRole` with scope-carrying assignments (research-app DB):
```
RoleAssignment
  id, user_id (FK users)
  role         (enum-validated string)
  scope_type   ('global' | 'study' | 'site')
  scope_id     (NULL for global; study_id or site_id otherwise)
  granted_by, granted_at
  unique(user_id, role, scope_type, scope_id)
```
Migration: existing `UserRole` rows become `RoleAssignment(scope_type='global')`. (The
research DB already has the additive-migration mechanism; this is an additive change.)

> Note: eCRF **sites/studies** live in the clinical store; `scope_id` is a by-value
> reference (no cross-DB FK), consistent with how the clinical store already references
> research ids.

### 4.3 Role catalogue
**System / research (global scope):**
- `admin` — superuser; every permission, global.
- `researcher` — the evidence-work base role: all `skill.*` evidence skills, library
  read/write, own watches & threads.
- `auditor` — read-only across data + audit trail (no writes).

**eCRF (study- or site-scoped):**
- `study_designer` — author/publish forms, manage the study, deploy, `skill.ecrf_design`.
- `principal_investigator` (PI) — oversee a study/site; sign forms; casebook sign-off;
  resolve queries; read data.
- `coordinator` (CRC) — enter/edit data at assigned **site(s)**; open forms; respond to
  queries; issue ePRO tokens.
- `data_manager` — raise/close queries; lock/unlock; SDV oversight; exports (study scope).
- `monitor` (CRA) — read data + **SDV verify** + raise queries; **no** data entry.

**Participant** stays **out** of the role system — authenticated by the scoped ePRO
magic-link token (`ParticipantAccess`), already isolated to one subject.

### 4.4 Permission catalogue (illustrative)
```
skill.meta_analysis · skill.search_strategy · skill.sr_protocol ·
skill.risk_of_bias · skill.general_qa · skill.ecrf_design
library.read · library.write
watch.read · watch.manage
study.read · study.author · study.publish · study.create
deployment.manage
data.read · data.enter
query.raise · query.respond · query.close
sdv.verify
form.sign · form.unlock · casebook.signoff · subject.unlock
audit.read
user.manage · source.manage
```

### 4.5 Role → permission matrix (sketch; the implementation owns the source of truth)
| Permission group | admin | researcher | study_designer | PI | coordinator | data_manager | monitor | auditor |
|---|---|---|---|---|---|---|---|---|
| evidence `skill.*` | ✓ | ✓ | – | – | – | – | – | – |
| `skill.ecrf_design` | ✓ | – | ✓ | – | – | – | – | – |
| `library.*` | ✓ | ✓ | – | – | – | – | – | r |
| `study.author/publish/create` | ✓ | – | ✓ | – | – | – | – | – |
| `deployment.manage` | ✓ | – | ✓ | – | – | – | – | – |
| `data.enter` | ✓ | – | – | – | ✓ | – | – | – |
| `data.read` | ✓ | – | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `query.raise` | ✓ | – | – | – | – | ✓ | ✓ | – |
| `query.respond` | ✓ | – | – | ✓ | ✓ | ✓ | – | – |
| `query.close` | ✓ | – | – | ✓ | – | ✓ | – | – |
| `sdv.verify` | ✓ | – | – | – | – | – | ✓ | – |
| `form.sign` / `casebook.signoff` | ✓ | – | – | ✓ | – | – | – | – |
| `form.unlock` / `subject.unlock` | ✓ | – | – | – | – | ✓ | – | – |
| `audit.read` | ✓ | – | – | ✓ | – | ✓ | ✓ | ✓ |
| `user.manage` / `source.manage` | ✓ | – | – | – | – | – | – | – |

---

## 5. Enforcement

A single dependency seam replaces the ad-hoc guards:
```python
require_permission("data.enter", scope=from_form_instance)   # FastAPI dependency factory
```
- **Scope resolvers** map the request to `(study_id, site_id)`: e.g. a `form_instance_id`
  → its subject → site → study (clinical store); a `study_id` path param → that study; a
  `deployment_id` → its `research_study_id`. Resolution is centralised so endpoints stay
  declarative.
- The check: does the user hold a role granting `perm` at `global`, the resolved
  `study`, or (site-level) the resolved `site`?
- **Skills:** the `/turn` dispatcher resolves the chosen specialist → `skill.*`
  permission (global scope) and rejects before routing if absent.
- **Auth-disabled dev/test:** the existing short-circuit stays (default-user gets all),
  so tests and local dev are unaffected.
- **Back-compat:** `require_admin` → `require_permission("system.admin")`,
  `require_data_entry` → `require_permission("data.enter")` — the old aliases keep
  working while endpoints migrate.

---

## 6. Object ownership (complements RBAC)
Roles answer "what may this user do"; ownership answers "to which rows". Threads and
watches gain real per-user scoping (today both default to `default-user`): a user sees
only their own unless they hold an oversight permission. Enforced as an object-level
check alongside `require_permission`.

---

## 7. Role administration
New `admin`-gated surface (and a small UI on the existing settings page):
- `GET /api/admin/users` — list users + their role assignments.
- `POST /api/admin/users/{id}/roles` — grant `(role, scope)`.
- `DELETE /api/admin/users/{id}/roles/{assignment_id}` — revoke.
- Invitations extend to carry scoped roles (not just a flat list).
`/auth/me` returns the caller's effective roles+scopes so the frontend shows/hides
controls.

---

## 8. Per-user quotas
With identity fully wired, `services/quota.py` gains the `user_id` partition its own
comment anticipates; role/tier-based quota ceilings are a later refinement.

---

## 9. Phased implementation
- **RBAC-1 — foundation:** `RoleAssignment` (scoped) + permission catalogue + role→perm
  matrix + `require_permission` seam + scope resolvers; migrate existing roles to global;
  re-express `require_admin`/`require_data_entry` over permissions. No behaviour change
  yet beyond equivalence. Tests for the matrix + scope resolution.
- **RBAC-2 — eCRF hierarchy + scoping:** introduce the eCRF roles; enforce study/site
  scoping on `/api/ecrf` + `/api/edc` (resource→scope resolvers); split `admin`-escalated
  ops (sign/unlock) onto PI/data_manager; role-administration endpoints + settings UI.
- **RBAC-3 — skills + ownership + quotas:** gate specialists in the dispatcher; thread/
  watch object ownership; per-user quota partition.

---

## 10. Open questions
- **O1** — Org/tenant scope above study? (single-tenant today; a `global` scope suffices.)
- **O2** — Role↔Cognito-group sync, or app-managed only? (architecture.md flags "JIT vs
  explicit assignment" — app-managed assignments recommended.)
- **O3** — Does `researcher` see *all* studies' non-PHI metadata, or only assigned ones?
- **O4** — Read-vs-write split for `auditor` on the research side vs eCRF side.
- **O5** — Where do per-role quota tiers live (config vs DB)?

---

## 11. How this relates to other docs
- `architecture.md` — records the identity/authz split this elaborates.
- `ecrf-design.md` §7 — names the roles; this design is how they get enforced (D1/D2).
- `feature-guide.md` — the per-user-workflow-scoping claim becomes real in RBAC-1..3.
