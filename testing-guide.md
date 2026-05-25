# Testing Guide

How to exercise the Clinical Research Assistant end-to-end — both the **automated
suite** (fast confidence) and **manual walkthroughs** of every feature, with
ready-to-paste sample inputs.

---

## 0. Run the app

```bash
# One-time
cp deploy/compose/.env.example deploy/compose/.env     # fill AWS profile, TAVILY, NCBI, Cognito
docker build -t research-assistant-sandbox:latest ./sandbox   # sandbox image (for meta-analysis)

# Bring up the stack (agent + research Postgres + clinical Postgres)
docker compose -f deploy/compose/docker-compose.yml up -d --build
docker compose -f deploy/compose/docker-compose.yml logs -f agent
```

Open **http://localhost:8000**. If auth is configured, log in via Cognito.

**First admin / data-entry access** (needed for admin + eCRF authoring):
```bash
docker compose -f deploy/compose/docker-compose.yml exec agent \
    cra create-admin you@example.com --skip-cognito
```
Log out/in so the invitation is consumed. (`admin` also satisfies the
`data_entry` role used by EDC capture.)

> **Tip — API-only steps from the browser.** A few capture steps (deploying a
> study, issuing an ePRO token) have no button yet. While logged in, open the
> browser **DevTools console** and use `fetch(...)` — your session cookie rides
> along automatically. Examples are given inline below.

---

## 1. Automated test suite (fastest confidence)

No Docker or AWS needed — runs against in-memory SQLite with mocked models.

```bash
uv run pytest -q          # ~324 tests
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src
```
All green = the logic (specialists, RAG, eCRF/EDC capture, edit-checks,
queries, signatures, audit) is intact. The manual walkthroughs below confirm
the live UI + Bedrock + both databases.

---

## 2. Meta-analysis workflow (with sample data)

The headline evidence-synthesis flow. Sample numbers below are fabricated to
pool cleanly — the actual PMIDs returned don't matter.

1. New thread → ask:
   > *"Does adding a proton pump inhibitor to dual antiplatelet therapy reduce upper GI bleeding in patients who recently underwent PCI for acute coronary syndrome?"*
2. Answer the clarification (RCTs only, age ≥ 18, …) → **confirm the PICO**.
3. Tick **5 studies** → **Extract data from selected (5)**. Most rows will say
   *needs data* ("event counts not reported in abstract").
4. Enter these into the 5 rows, effect measure **OR**:

   | Row | events (PPI) | n (PPI) | events (no PPI) | n (no PPI) |
   |---|---|---|---|---|
   | 1 | 22 | 1880 | 51 | 1881 |
   | 2 | 8  | 300  | 16 | 300  |
   | 3 | 15 | 600  | 22 | 600  |
   | 4 | 5  | 225  | 11 | 225  |
   | 5 | 10 | 425  | 18 | 425  |

5. **5/5 rows complete** → **Run meta-analysis**.
6. **Expected:** forest plot renders inline — **Pooled OR ≈ 0.50, 95% CI
   ≈ 0.36–0.69, I² ≈ 0%**, "favours intervention" (green).

*Optional second outcome (MACE) to test multi-outcome rendering* — pools to
OR ≈ 1.05, CI crossing 1 ("no significant difference"):

| Row | events (PPI) | n | events (no PPI) | n |
|---|---|---|---|---|
| 1 | 95 | 1880 | 88 | 1881 |
| 2 | 14 | 300 | 12 | 300 |
| 3 | 28 | 600 | 26 | 600 |
| 4 | 9 | 225 | 8 | 225 |
| 5 | 19 | 425 | 17 | 425 |

---

## 3. Other evidence workflows (quick checks)

- **Search strategy** (`/search` or "build a search strategy for…") → returns a
  Boolean query per database + broaden/tighten suggestions.
- **SR protocol** (`/protocol`) → PRISMA-P–shaped sections.
- **Risk of bias** (`/rob`) → domain-by-domain judgements (you confirm each).
- **General Q&A** — ask *"what does my library say about SGLT2 inhibitors in
  heart failure?"* → grounded answer with citations in the references footer.
  (It will refuse to quote effect sizes/PMIDs in prose — by design.)

---

## 4. Library + RAG (▥ Library)

1. Run a `search_papers` (via a meta-analysis or search-strategy turn) — results
   are cached automatically. Open **Library**; you'll see the cached papers.
2. **Upload a PDF** of a paper (text-based, not scanned) → it ingests, chunks,
   and embeds within seconds. Re-uploading the same file dedupes.
3. **Search** by keyword; click a row to see its passages and embedded status.
4. The stats bar shows publications / passages / % embedded / full-text counts.

---

## 5. Living-review watches (▤ Watches)

1. From a finalised search strategy, save it as a **watch** with a cadence.
2. **Run now** to trigger immediately; the run diffs against the baseline and
   triages new hits. A material hit raises a **notification** (bell icon).

---

## 6. eCRF / EDC end-to-end ⭐

The clinical-data-collection subsystem. Full loop: **author → publish → deploy →
capture (staff EDC + participant ePRO) → sign/lock → verify → audit**.

### 6a. Author a form (⊞ Form Builder)

1. Open **Form Builder**. **Create study** "SGLT2 in HFpEF".
2. Either **paste a protocol** into "Draft from protocol" → **Draft CRFs**
   (calls the model; review the JSON drafts), **or** paste this form definition
   directly into the editor:
   ```json
   {
     "name": "vitals",
     "title": "Vital Signs",
     "sections": [
       { "id": "vs", "title": "Vitals", "items": [
         { "id": "age", "label": "Age (years)", "data_type": "integer", "cdash_var": "AGE", "required": true,
           "edit_checks": [{ "id": "age_range", "severity": "hard",
             "expression": "is_blank(age) or (age >= 18 and age < 120)",
             "message": "Age must be 18-119" }] },
         { "id": "sbp", "label": "Systolic BP (mmHg)", "data_type": "integer",
           "edit_checks": [{ "id": "sbp_high", "severity": "soft",
             "expression": "is_blank(sbp) or sbp <= 200",
             "message": "Systolic BP unusually high — please confirm" }] },
         { "id": "sex", "label": "Sex", "data_type": "single_select", "code_list_ref": "cl_sex" }
       ] }
     ],
     "code_lists": [
       { "id": "cl_sex", "name": "Sex", "items": [
         { "code": "M", "label": "Male" }, { "code": "F", "label": "Female" } ] }
     ]
   }
   ```
3. **Save draft** → **Publish**. Try **Export ODM** (downloads CDISC ODM-XML).
4. **Versioning check:** **New version** → edit → publish; the prior version
   flips to *superseded*. Editing a *published* form is blocked (must version).

### 6b. Deploy the study (browser console — API-only step)

```js
// In DevTools console, while logged in. Returns the deployment id.
await (await fetch('/api/ecrf/studies')).json()          // find your study's id
await (await fetch('/api/edc/deployments', {method:'POST',
  headers:{'Content-Type':'application/json'},
  body: JSON.stringify({research_study_id: 'PASTE_STUDY_ID'})})).json()
```

### 6c. Capture data as site staff (✚ Data Capture)

1. Open **Data Capture** → pick your **deployment** → **+ Site** ("Site A") →
   add a **subject** ("S-001").
2. **Open form for subject** → the form renders from its definition.
3. **Hard check:** enter Age = `200` → **Save** → blocked inline ("Age must be
   18-119"), nothing saved.
4. Fix Age = `45`; enter SBP = `250` → **Save** → saves, and a **soft query**
   appears in the Queries panel. Lower SBP to `180`, Save → the auto-query
   **auto-closes**.
5. **Mark complete** + Save → status `complete`. (Required fields enforced.)
6. **Queries panel:** raise a manual query, respond, close.

### 6d. e-Signature + lock (Data Capture)

1. With the form `complete`, **Sign** it (meaning e.g. "PI sign-off") → status
   `signed`, edit-locked.
2. Try to edit → **blocked (409)**.
3. **Unlock** (admin; give a reason) → the signature is voided, the form
   reopens, and both actions are audited.

### 6e. Subject casebook sign-off (E6)

```js
// Sign off the whole subject (all forms must be complete/signed) — locks the casebook
await (await fetch('/api/edc/subjects/SUBJECT_ID/sign', {method:'POST',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify({meaning:'PI casebook sign-off'})})).json()
```
All the subject's forms go to `locked`; editing any is blocked. Unlock via
`POST /api/edc/subjects/{id}/unlock {reason}` (admin).

### 6f. Source-data verification (E6)

```js
await (await fetch('/api/edc/form-instances/FI_ID/verify', {method:'POST',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify({item_ids:['age','sbp']})})).json()
```

### 6g. Participant ePRO (magic-link)

1. Mark a form as participant-facing by adding `"epro": true` to its definition
   (author + publish it, then redeploy).
2. Issue a token for a subject (browser console):
   ```js
   await (await fetch('/api/edc/subjects/SUBJECT_ID/epro-access', {method:'POST'})).json()
   // -> { token, epro_path: "/epro.html?token=..." }
   ```
3. Open the `epro_path` (ideally in a private window — no login needed). You'll
   see a **consent gate** → consent → fill the ePRO form → **Save & submit**.
   Hard checks apply here too.
4. Back in Data Capture, open that form instance's **audit** — the participant's
   entries show `source = epro`.

### 6h. Tamper-proof audit (E6, DB-enforced)

Every action above is in the append-only audit trail. The database itself
rejects tampering:
```bash
docker compose -f deploy/compose/docker-compose.yml exec clinical-postgres \
  psql -U cra -d cra_clinical -c \
  "UPDATE audit_entries SET action='x';"
# -> ERROR: audit_entries is append-only; UPDATE/DELETE is not permitted
```

---

## 7. What to watch for

- **Anti-hallucination:** general Q&A and meta-analysis never invent PMIDs or
  effect sizes; every citation traces to a real `search_papers` call.
- **PHI isolation:** subject data lives only in the clinical Postgres
  (`cra_clinical`), never in the research DB and never sent to the model.
- **Reproducibility:** re-open any thread — PICO, studies, extraction, code, and
  plot are all persisted.
