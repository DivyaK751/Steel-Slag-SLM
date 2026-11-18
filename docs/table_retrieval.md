# Table retrieval experiments

## Round 1: inspect extraction before changing representation

Goal: retrieve evidence that connects a reported value to its material, property, unit and
experimental conditions. This is separate from the abandoned parsing-ceiling diagnostic.
Production parsing, manifests, chunking and indexes remain unchanged.

`inspect-tables` uses the existing Docling converter with OCR, table structure and picture
classification enabled, but converts only the physical PDF page selected for each paper.
It captures all detected tables on that page, not just the intended table.
The six selections in [the inspection config](../configs/experiments/table_inspection.yaml)
cover mixed units, nested headers, carbonation matrices, factor codes, reviews and image tables.

From the repository root, using the current uploaded PDF location:

```bash
HF_HUB_OFFLINE=1 uv run inspect-tables \
  --cases configs/experiments/table_inspection.yaml \
  --pdf-dir data/interim/pdf_parsing/raw/table_retrieval/pdfs \
  --out-dir data/interim/table_retrieval/inspection
```

No Google credentials, Gemini calls, Drive writes or index updates are involved. The offline
flag prevents model downloads; a missing cached model fails explicitly. Output files are
git-ignored. Existing reports are never overwritten: choose a new output directory for a rerun.
Original PDFs are read only. PDF names may include titles after their leading W-number ID;
missing or duplicate matches are errors.

Each `<paper_id>.json` contains the source filename and SHA-256, source page count,
Docling package versions, requested page/target, detected table count (the `tables` array)
and status. Each table retains:

- Native cells, header flags, row/column spans and cell bounding boxes.
- Table provenance with physical PDF pages and original coordinate origins.
- Caption, linked footnotes and Markdown.
- Nearest body text before/after, with its own provenance, for inspection only.

A `captured` status means tables were detected, **not** that they are correct or that the
intended table was found. Zero tables produces a saved `no_tables` report and a nonzero command
exit. Conversion failures raise explicitly without creating that paper's report; earlier papers'
reports remain available. No missing structure is repaired or inferred.

Review against the PDF: check the intended table is present, complete header paths, units,
row labels, merged cells, inequalities, missing values and correct value associations.
Page-only layout recognition may differ from full-paper conversion, and cross-page
continuations or definitions outside the page are intentionally not resolved. Confirm promising
cases with full-paper production output before claiming parser quality or retrieval gains.

Next round, after review: render deterministic contextual evidence only for structures the
inspection confirms usable. Keep original values and parent-table provenance; ambiguous
structures must be flagged rather than guessed. Retrieval benchmarking comes later.

### First local observations

The six-page offline run with Docling 2.140.0 / docling-core 2.101.1 detected seven tables.
All table provenance pages matched the requested physical page. These are spot checks,
not a full accuracy audit or retrieval benchmark:

| Paper / page | Observed extraction | Consequence for the next round |
|---|---|---|
| W4280519327 / 3 | SS CaO 38.96 and Cr 442.30 retained with grouped headers | Preserve separate oxide-percent and heavy-metal mg/kg header paths |
| W3130633964 / 13 | Nested strength/age headers and mix 9#, 0.5%, 45.14 retained; caption became a full-width header cell | Do not blindly prepend every header to every value; caption field alone is insufficient |
| W7168360315 / 7 | Temperature/time values retained, but particle group labels lack row spans; small-particle label is split across rows | Generic span expansion cannot recover every group; flag unresolved row context |
| W2789941819 / 4 | Tables 3 and 4 detected; Table 4 retains factor codes and strength headers | A-D meanings require explicit source definitions, not guessed code expansion |
| W3119080532 / 14 | Qualitative findings and multi-row property/comment cells retained | Keep condition/result/comment associations together |
| W2518789431 / 2 | OCR recovered A/CaO 45.2, but caption is empty and labels include `rnother`, `MO`, `A12O3` | Serialization cannot fix OCR; retain raw text and flag uncertain chemistry labels |

This supports a conservative deterministic renderer, not automatic correctness for every
table. Start with unambiguous rows and native header paths; preserve review findings as
relational text. Defer unresolved row groups, OCR repair and factor-definition linking.
No measured retrieval improvement is claimed.

## Round 2: conservative, review-only row evidence

`data.table_evidence.render_table_evidence` creates candidate passages, not verified facts.
It joins column-header paths to exact cell strings, keeps all columns of a complete row
together, and repeats caption/table-level text and linked footnotes. Full-width native
headers above narrower headers supply table context without being repeated for every field.
Explicit body row spans are expanded; empty cells are never filled from previous rows.
Source table references, provenance, zero-based row indices and native cell-list indices
remain attached for auditing.

Missing context or column-header paths, interleaved/crossing headers, incomplete body rows,
section cells and multi-column body cells produce explicit issues rather than guessed text.
Invalid geometry raises an error. Every output remains `unreviewed`, even with no issues:
these checks cannot detect all OCR mistakes, missing units or incomplete material definitions.
Nearby prose is not incorporated, factor codes are not expanded, and values are not normalized.
These passages are not yet token-budgeted chunks and are not added to any index.

Round 2 is on a stacked branch based on Round 1. Review its diff against `fix/parsed_table`;
do not combine both rounds into one oversized PR to main.

Run locally without reconverting PDFs or loading models:

```bash
uv run render-table-evidence \
  --inspection-dir data/interim/table_retrieval/inspection \
  --out-dir data/interim/table_retrieval/evidence
```

Outputs retain the original PDF hash plus the input inspection filename and SHA-256.
All JSON inputs are validated before writing. Empty/invalid inputs and existing outputs fail
explicitly; use a new output directory to rerun. Papers with zero candidate rows still receive
reports, with a nonzero command exit. Inspect every report's `issues` as well as `rows`.

The first saved run produced **30 candidate rows from five tables** across four papers.
Two tables were blocked: image chemistry lacked table context; carbonation lacked a header
for one column. Five incomplete body rows were omitted, including Table 4's summary rows.
The nonzero exit for these two blocked papers is expected, not a lost report.

Spot checks retained mix 9#, sodium sulfate 0.5%, and 28-day UCS 45.14 MPa in one passage;
the steel-slag chemistry passage kept CaO 38.96 under percent and Cr 442.30 under mg/kg.
All 30 saved row-to-cell mappings and parent inspection hashes were checked.
This tests representation fidelity only; usefulness, token fit and retrieval gains remain
unmeasured. Next: review candidates before implementing token-budgeted chunks.

Review hardening: blank native column headers now block rendering even when another header
level supplies text. Otherwise a lost age or property label could silently yield an incomplete
path. Intentionally blank headers are conservatively deferred for manual review.

## Round 3: tokenizer-budgeted whole rows

The local candidate chunker keeps **one complete rendered row per chunk**, rather than
cutting by characters. Oversize rows are explicitly omitted, never truncated or split away
from their mix, age or conditions. This deliberately conservative first variant can lose
coverage; keep the original tables as baseline evidence for the later comparison.

It reuses the production passage-formatting helpers. Counts include the filename-derived
title, embedding special tokens, reranker pair special tokens and an explicit query reserve.
The reserve excludes pair special tokens. The example reserves 64 reranker tokens per query;
future evaluation must check actual query lengths and reject or re-budget longer queries.
This is not a guarantee for unlimited queries. No embedding or reranker weights are loaded.

```bash
uv run budget-table-evidence \
  --inspection-dir data/interim/table_retrieval/inspection \
  --out-dir data/interim/table_retrieval/budgeted \
  --embedding-limit 512 --reranker-limit 512 --query-reserve 64
```

The command reads model names from the existing embedding configuration and requires their
tokenizers already cached locally. Missing assets fail explicitly. Only public tokenizer
assets were downloaded for this experiment; source PDFs and passages are never uploaded.
Reports fingerprint tokenizer configurations, library versions and original inspections.
Filename stems are provisional title metadata, not registry-verified titles; evaluation must
use identical, verified titles in both variants and re-count if they change.

Outputs remain unreviewed and local. No production parser, index or retrieval default changes.
Existing output files are refused; blocked papers still get reports and cause a nonzero exit.
This round is stacked on `exp/table-evidence-rendering`, not a combined PR to main.

Measured six-paper result: all 30 candidate rows fit, with **60-232 embedding tokens** and
**127-306 reranker pair tokens**, including an actual 64-token boundary query. No additional
rows were dropped by budgeting. All source row mappings, unchanged text and parent hashes
were checked after saving. Two previously blocked tables remain blocked. These measurements
show token fit and fidelity, not improved top-k retrieval; the 50-paper comparison is next.

## Round 4a: shared full-document corpus

The 50 benchmark PDFs were resolved locally: 857 physical pages in total. Selected-page
inspection is not a full-corpus benchmark. Before comparing representations, capture complete
native documents so both variants derive from the **same conversion**, not different parser
runs. Production parsing and cloud artifacts remain untouched.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
uv run capture-table-corpus \
  --benchmark configs/extraction_benchmark.yaml \
  --pdf-dir data/interim/pdf_parsing/raw/table_retrieval/pdfs \
  --out-dir data/interim/table_retrieval/full_documents
```

Add `--paper-ids W4280519327` for the one-paper smoke test. Omitting that flag selects both
benchmark splits; this captures source documents, not training examples or answer labels.
One successful full-document snapshot is stored per paper. Every physical page must be present.
Reports retain source hashes, Docling versions, a stable options fingerprint and the native
document, including tables, text, captions, footnotes and picture metadata.

Identical existing snapshots are validated and reused without conversion. Changed sources,
parser settings, incomplete conversions or corrupt reports fail explicitly; use a new directory
for an intentional parser change. A write interrupted mid-file is not reusable. Earlier completed
papers survive errors. All requested filename matches are checked before conversion starts.
Use the same cached models/environment throughout a corpus run: this fingerprint does not pin
all transitive library versions or model revisions. No Gemini reading or cloud writes occur.

The full-paper smoke test captured all 15 pages and six tables of W4280519327. Its steel-slag
CaO 38.96% and Cr 442.30 mg/kg associations remained intact. The conservative renderer and
budgeter yielded 40 candidate rows from four tables, with two other tables explicitly blocked.
Docling warned about dense vector separators on page 11 and used its reading-order fallback;
successful conversion is not an accuracy certificate. Cross-process cache reuse was verified
after fixing unordered-set serialization in the options fingerprint. Three papers have been
fully captured so far (W4280519327, W4221096775 and W2014482815); 47 conversions remain.

Next, finish the snapshots, derive baseline chunks with the original formatting and compare
against the same corpus augmented by candidate rows. Keep original tables when rows are blocked.
Keep titles, non-table content, figure policy and retrieval models identical. This capture does
not reproduce production Gemini figure readings; either share existing readings across both
variants or explicitly evaluate text/tables without those readings. Enforce actual query token
lengths before scoring. Report source coverage and value-condition correctness alongside top-k
retrieval; the existing answer-string metric alone cannot establish the latter.

### Implications for fine-tuning

Parsing/OCR, retrieval and answer generation are separate failure stages. Fine-tuning cannot
recover omitted table cells or evidence absent from the model context. Do not train on
unreviewed candidate passages as if they were ground truth. Keep paper-level train/test separation,
teach grounded citations and abstention, and test age, mix, unit and material confusions.
GGBFS must remain distinct from steel slag; elemental and oxide reporting bases are not
interchangeable. AI summaries and semantic repairs remain deferred until deterministic
retrieval has been evaluated.

## Round 4b: complete capture and structural coverage audit

Resume `capture-table-corpus` with the full benchmark selection above. Once it finishes,
verify **every** expected source and snapshot before preparing the paired retrieval inputs:

```bash
uv run audit-captured-tables \
  --benchmark configs/extraction_benchmark.yaml \
  --pdf-dir data/interim/pdf_parsing/raw/table_retrieval/pdfs \
  --captures-dir data/interim/table_retrieval/full_documents \
  --out data/interim/table_retrieval/corpus_coverage.json
```

The audit checks source hashes, paper identities, page counts, complete native documents
and consistent recorded parser versions/settings. It fingerprints each snapshot, then records
per-table candidate row indices and explicit renderer issues. Missing snapshots or invalid
native geometry fail without saving a partial audit. No models are loaded, sources modified,
or existing reports overwritten. Blocked tables remain in the report rather than disappearing.

## Round 5: paired retrieval evaluation

The **baseline** uses the existing Markdown/text block export and chunker. The **augmented**
index keeps every baseline chunk and adds one contextual passage per eligible native table
row. A passage includes the caption or explicit table context, header paths, original values
and units, linked notes where available, and physical PDF page(s). It does not infer a missing
mix, curing condition or unit. The report saves exact indexed row text with native table
references, cell indices and token counts. Blocked or oversized candidates are recorded;
their Markdown remains searchable. Rows are candidates, not verified extractions.

From the repository root, run the one-paper check (use a new output filename on repeat runs):

```bash
CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
uv run evaluate-contextual-tables \
  --benchmark configs/extraction_benchmark.yaml \
  --questions configs/retrieval_eval_heldout.yaml \
  --captures-dir data/interim/table_retrieval/full_documents \
  --paper-ids W4280519327 \
  --out data/interim/table_retrieval/one_paper_repeat.json
```

The first run may download public model weights from Hugging Face; inference is local.
Add `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` after the models are cached to require an
offline run. Omit `CUDA_VISIBLE_DEVICES=""` to allow a supported CUDA GPU.

For the full evaluation, finish the resumable capture and run the coverage audit above.
Then run the same evaluation command **without `--paper-ids`** and with a distinct `--out`
(for example `data/interim/table_retrieval/benchmark_50.json`). This uses the 50
benchmark papers, not the historical distractor list in the question config. With the
question file above, 25 questions qualify (19 table and 6 text); selecting
`configs/retrieval_eval_human50.yaml` instead scores 52 questions (39 table and 13 text).
Questions requiring figure evidence or evidence from an unselected paper are excluded and
reported. Both arms use the same filename-derived titles and omit Gemini figure readings.
Baseline vectors are computed only once. Capture resumes per paper, but retrieval does not
checkpoint; run the small check before starting the full CPU run.

Compare table and text metrics separately. Evidence@5 checks source/kind and a caption or
text label. Answer@5 checks for an answer string from an evidence paper. Evidence+value@5
requires both proxies in the same chunk; it still cannot prove the number belongs to the
requested row, property, age or unit. Repeated rows from a table can lift the label hit
rate while crowding out other evidence. Review saved top-five passages for these errors
before changing production, and do not compare this corpus directly with historical scores
from the larger distractor corpus.

### Measured CPU pilot

The W4280519327 pilot (question `x18`, steel-slag CaO) took 8 min 18 s with two
CPU threads and cached weights. Both arms put the answer-bearing Table 1 evidence at
rank 1 and hit all three top-five proxies. The baseline ranked its original Markdown
chunk first; the augmented arm ranked the contextual steel-slag row first and kept
the Markdown at rank 2. There were 104 baseline chunks and 40 added rows from four of
six detected tables. In the augmented arm all five top results came from Table 1.
This demonstrates a more explicit top passage, not a measured gain in top-five recall.

Budget roughly 2-10 hours to capture the remaining 47 PDFs on comparable hardware;
three observed papers took about 1.5, 2.7 and 12.7 minutes each. For retrieval, the
25-question run could take roughly 8-20 hours on the same CPU setting. Each question
can rerank chunks from five shortlisted papers instead of one, twice. These are
planning estimates, not measured full-run times; OCR load, chunk count and passage
length can move them considerably. Prefer CUDA for the full reranking run if available.

## GitHub Actions canary: publish and index contextual rows

The local benchmark JSON is a report, not a production input. On a ref with the
canary wiring, **Actions → Parse trial PDFs → Run workflow** accepts
`contextual_tables=true`, an explicit Drive-backed `paper_ids` value (start with one
eligible ID), and `max_papers=1`. Keep `contextual_tables=false` for the existing flow.

In canary mode, the job disables Gemini figure reading, runs Docling once per PDF,
and publishes the original Markdown/tables/figure metadata plus optional
`contextual_tables` in `parsed.json` under `_parsed_contextual_tables_v1`. The indexer
reads the same manifest, appends fitting rows with independent chunk positions, and
writes the corresponding separate index. Original tables remain intact. Geometry
and rendering issues are preserved in the manifest; token-budget exclusions are
logged by indexing. The row budget reserves 64 reranker query tokens, so keep
canary queries within that reserve when checking results.

Check that the job parsed the requested ID, that **Index summary** counts contextual
rows, and that search returns a known value with the right mix, property, unit and
PDF page. A successful parse with zero contextual rows is possible; it is not evidence
that row augmentation worked for that paper. Query the same canary index with:

```bash
uv run search-index \
  --parsed-folder _parsed_contextual_tables_v1 \
  "What CaO content was reported for the steel slag?"
```

The existing **Extract paper data** job uses the configured default parsed folder and
sends document Markdown (and figures when its prompt requests them) to its Gemini
extractor. It neither reads the canary folder nor sends contextual rows to Gemini.
An extraction-quality comparison would need a separate, non-production run that
explicitly uses the new representation. Do not equate retrieval results with changes
to extraction quality or spend.

These are coverage counts over **detected** tables, before token budgeting or manual review.
They cannot measure how many source tables Docling missed, OCR accuracy, retrieval gains or
correct value-condition associations. Tables with some candidate rows may still have blocked
rows. Review the issue list, not just the headline table count.
