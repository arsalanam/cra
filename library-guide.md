# Your Research Library — User Guide

The **Library** is your assistant's local store of papers. Every paper it
finds or fetches for you is kept here, and you can add your own PDFs. Over
time it becomes a private, searchable evidence base that the assistant draws
on to ground its answers in papers *you've actually collected* — never
invented ones.

Open it from the **▥ Library** link in the sidebar, or go to `/library.html`.

---

## How the library fills up

**Automatically.** Whenever the assistant runs a literature search or pulls a
paper's full text during a workflow, that paper — its title, authors,
journal, abstract, and any full text — is saved to your library in the
background. You don't have to do anything; just use the assistant normally and
the library grows.

**By uploading PDFs.** For papers behind a paywall (e.g. Embase or Cochrane
articles your institution licenses), download the PDF and add it yourself.

---

## Uploading a PDF

1. Open the **Library** page.
2. Under **Upload a PDF**, choose your file.
3. Optionally set a **Title** and **DOI** (if you leave Title blank, the
   assistant uses the PDF's own metadata or the filename).
4. Click **Upload**.

The assistant extracts the text, splits it into sections (Introduction,
Methods, Results, …), and queues it for indexing. Within a few seconds it's
searchable. You'll see a confirmation with the page and passage counts.

**Good to know**
- **PDFs only**, up to 50 MB.
- **Text-based PDFs only.** Scanned/image-only PDFs are rejected because there's
  no text to extract (no OCR yet) — if you hit this, look for a text version.
- **Duplicates are handled.** Re-uploading the same file (or a paper already in
  your library by DOI) won't create a second copy — it just confirms it's
  already there.

---

## Browsing and searching

The **Publications** list shows everything in your library, newest first. Each
entry shows its source (`pubmed`, `europepmc`, or `upload`), journal and year,
identifiers (PMID/DOI), and badges for how many passages it has, whether
they're **embedded** (indexed for semantic search), and whether **full text**
is stored.

- **Search** by typing in the box — it matches titles and abstracts.
- **Click a title** to expand and read its stored passages (abstract +
  section chunks).
- **Delete** removes a publication and its passages from the library.

The stats bar at the top shows totals: publications, passages, percent
embedded, and how many have full text.

---

## How the library powers answers

Once papers are in your library, the assistant can search them semantically —
matching on *meaning*, not just keywords — and cite real passages.

- **In general Q&A:** ask things like *"what does my library say about SGLT2
  inhibitors in heart failure?"* The assistant searches your saved papers and
  answers from them, listing the specific papers (PMIDs/titles) in the
  **references** under its answer. To keep answers safe, it won't quote
  specific effect sizes or statistics in plain Q&A — for that, use the
  meta-analysis workflow.

- **In the meta-analysis workflow:** while you're shaping the PICO, the
  assistant can peek at your library for related evidence to help frame the
  question. This is for **context only** — the studies actually included in a
  meta-analysis always come from a fresh, traceable literature search, never
  from the library cache.

Every passage the assistant retrieves is real text from a paper you collected,
with its citation attached — consistent with the assistant's core rule of
never fabricating evidence.

---

## A few limits (today)

- **Indexing is near-real-time, not instant** — a freshly added paper becomes
  semantically searchable within seconds.
- **Off-topic searches still return the closest matches.** If nothing in your
  library is truly relevant, you'll get the nearest papers with low relevance —
  treat low-ranked hits with skepticism.
- **Deleting a paper** removes it from search immediately; the original file on
  disk is retained.
