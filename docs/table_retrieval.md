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
