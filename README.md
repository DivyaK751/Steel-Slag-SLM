# Steel-Slag-SLM

## Install

```bash
uv sync
cp .env.example .env   # then fill in your own values
uv run pytest
```

## Paper fetcher

A background job (GitHub Actions, every night) that searches [OpenAlex](https://openalex.org)
for open-access steel slag papers, keeps the relevant ones, skips anything already in the
shared Google Drive folder, and uploads the new PDFs there. It runs once per member's OpenAlex
key until each key's daily budget is used up, with no manual steps.

**Setup, launch, daily workflow and troubleshooting:
[docs/paper_fetcher_setup.md](docs/paper_fetcher_setup.md).**

- Search queries and relevance rules: `configs/base.yaml`
- Code: `src/steel_slag_slm/data/` (OpenAlex, Drive, relevance) and
  `src/steel_slag_slm/pipelines/fetch_papers.py`
- Schedule: `.github/workflows/fetch_papers.yml`

## Relevance screening

An ADK agent running Gemini on Vertex AI reads papers in the BigQuery `papers` table that have
no decision yet (`relevant IS NULL`). It judges each one from its title, abstract and keywords
using [prompts/screen_relevance.yaml](prompts/screen_relevance.yaml), then fills in `relevant`,
`relevance_score`, `relevance_confidence`, `relevance_reason` and `supporting_excerpt`.

```bash
gcloud auth application-default login         # once; uses your own Google account
# in .env: GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, BIGQUERY_PAPERS_TABLE (see .env.example)
uv run screen-papers --max-papers 5 --dry-run  # log decisions, write nothing
uv run screen-papers                           # screen everything still unscreened
uv run pytest -m integration                   # 3 obvious cases against real Gemini
```

- Model, batch size and concurrency: the `llm:` and `screening:` sections of `configs/base.yaml`
- A paper whose screening fails stays unscreened and is retried on the next run.
- `supporting_excerpt` is kept only if it really appears in the abstract (or the title).
- Code: `src/steel_slag_slm/agents/` (shared ADK helpers plus the screener),
  `data/bigquery_store.py` and `pipelines/screen_papers.py`. A new agent adds a prompt, an
  output schema, a small agent module and a pipeline, and reuses the rest.

## Missing abstracts

Some papers have no abstract on OpenAlex. `fill-abstracts` downloads each one's PDF from Drive,
sends its first 3 pages to Gemini with
[prompts/extract_abstract.yaml](prompts/extract_abstract.yaml), and writes the abstract back to
the `papers` table. It runs nightly after the fetch, and can be run by hand:

```bash
uv run fill-abstracts --max-papers 5 --dry-run  # log the abstracts, write nothing
uv run fill-abstracts                           # every paper still missing an abstract
```

`abstract_source` records where each abstract came from:

| Value | Meaning |
|---|---|
| `openalex` | From OpenAlex |
| `pdf` | From the PDF, and found in the pages' text (margin line numbers ignored) |
| `pdf_unverified` | From the PDF, but the pages are scanned or their text is garbled, so it couldn't be checked |
| `pdf_mismatch` | Gemini's text wasn't on the pages; nothing stored |
| `not_found` | No abstract in the first pages (e.g. retraction notes, editorials) |

A paper whose PDF can't be read, or whose Gemini call fails or takes over 2 minutes, stays empty
and is retried on the next run. Settings: the `abstract_extraction:` section of
`configs/base.yaml`.

## PDF parsing

`parse-pdfs` reads paper IDs and PDF Drive links from the configured BigQuery source table.
It uses one Docling conversion to extract text, tables and figure regions, then uses PyMuPDF
to render figure regions as PNGs. Parsing does not call Gemini or modify source PDFs.

Configure credentials and environment variables as described in [.env.example](.env.example).
The `pdf_parsing:` section of [configs/base.yaml](configs/base.yaml) specifies the source
table and default output folder. The default Drive output folder is `_parsed`.

### Published artifacts

Each paper has its own folder and an `images/` subfolder:

```text
_parsed/
├── index.json
└── <paper_id>/
    ├── parsed.json
    └── images/
        ├── <generation>_figure_01.png
        └── <generation>_figure_02.png
```

The version-four manifest contains Markdown, tables, provenance-bearing content blocks and
figure references. References include Drive file IDs, image checksums, pixel dimensions,
render DPI, classifications, captions and page/bounding-box placements.

Text/table placements preserve their coordinate origin. Figure and matched-caption boxes
use top-left coordinates. Captions recovered from the figure's PDF text have no exact caption
placement.

Each paper has one `parsed.json` containing its current text, tables, provenance and figure
references. The root `index.json` records that file's Drive ID.

On reparse, new figure images are uploaded before `parsed.json` is replaced, then the root
index is updated. Historical JSON snapshots are not retained. These writes are not
transactional: an index-update failure can leave stale index metadata. A normal retry detects
the generation mismatch and schedules a reparse to reconcile it.

Figure images retain generation-specific filenames. Previous image generations and partial
uploads are not automatically deleted.

Previously published `parsed_<generation>.json` files are not automatically migrated or
deleted. With no publisher running, identify the snapshot referenced by the root index,
remove its unindexed `parsed.json` duplicate, and rename the indexed snapshot to `parsed.json`.
Preserve the indexed file's Drive ID. Repeat for each existing paper before resuming publication.

Run only one parser process against an output folder at a time.

### Validate in a separate Drive folder first

```bash
uv run parse-pdfs --output-folder _parsed_integration_pilot --max-papers 1
uv run export-parsed \
  --output-folder _parsed_integration_pilot \
  --out-dir data/interim/integration_pilot \
  --max-papers 1
```

Inspect the exported manifest, images and source PDF before parsing more papers. Include
scanned pages, vector charts, multipart figures and rotated/cropped pages in the pilot.

### GitHub Actions pilot

Live Drive/BigQuery testing uses the repository's configured GitHub Actions secrets.
The parsing pilot workflow explicitly passes `--output-folder _parsed_v2`, keeping its
manifests, image assets and parsing index separate from production `_parsed`.

To run it, open Actions → Parse trial PDFs → Run workflow, select the integration branch,
and set `max_papers` to `1`. Inspect the resulting `_parsed_v2` folder in Drive before
increasing the batch size.

After parsing, `parse-pdfs` updates that folder's search index
(`_search_index/_parsed_v2/_embeddings/` in the shared folder); the workflow does not call Gemini. Local unit tests, linting and
type-checking do not require the cloud credentials.


### Production parsing and migration

```bash
uv run parse-pdfs --max-papers 5
```

Repeat this command to process additional pending papers. Once the pilot is approved, omit
the limit to process all remaining pending papers:

```bash
uv run parse-pdfs
```

Successful version-three entries are automatically reparsed. Current version-four entries
are reused when their source link and parser fingerprint match. The fingerprint includes
extraction settings, parser package versions and the pipeline revision.

Additional options:

- `--reparse`: force reparsing of successful current papers.
- `--retry-skipped`: reconsider papers deliberately marked as skipped.
- `--output-folder NAME`: publish to a different Drive output folder.
- `--paper-ids W123 W456`: select actual OpenAlex IDs from the source table; the pending-paper
  limit still applies. The GitHub pilot also accepts a space-separated `paper_ids` input.

Use `--reparse` if PDF bytes change behind the same Drive link; the normal reuse path does not
download the source to detect this change. Bump `PIPELINE_REVISION` when extraction code changes
alter the output.

Conversion failures are logged and leave the paper retryable. The CLI exits unsuccessfully
when parsing failures occur. Storage errors abort the run.

Parsing accepts figure-only papers, but the text search index cannot index a paper that
produces no text/table chunks. Figure pixels and Gemini readings are not automatically
embedded. After approved production migration, rebuild the existing text search index
explicitly; ordinary indexing does not refresh papers already indexed.

To parse particular papers from the source table, add `--paper-ids W123 W456` (the "Parse trial
PDFs" workflow has a matching `paper_ids` input). On macOS, prefix the command with
`OMP_NUM_THREADS=1`: Docling's PyTorch and faiss each bundle OpenMP, and running both in one
process segfaults there.

## Data extraction

`extract-data` sends each parsed paper's Markdown to Gemini and stores every value it reports
in nine BigQuery tables, one per data sheet of the CNS input workbook
(`steel_slag_extraction.sheet_01_scientific_literature` ... `sheet_09_citations_provenance`).
The model, prompt version and prices are in the `data_extraction:` section of
`configs/base.yaml`; the prompts are `prompts/extract_data*.yaml`.

```bash
uv run create-extraction-tables                 # once per project: create the nine tables
uv run extract-data --paper-ids W123 W456       # (re-)extract these papers
uv run extract-data --max-papers 5              # extract parsed papers that have no rows yet
uv run export-extraction                        # all nine tables -> reports/extraction/*.xlsx
```

On GitHub, the "Extract paper data" workflow takes a comma-separated list of paper IDs.

- A paper's old rows are replaced on every run. Each material's slag type is copied to all
  its rows; values that break a column rule (unit, allowed values, unknown material) are
  dropped and logged.
- Each fact's page is found from its excerpt, and its confidence drops from High to Medium
  when the stored value isn't printed as such (a unit conversion, for example).
- Every paper's token count and cost are logged (about $0.20 a paper with
  `gemini-3.8-flash`). The prompt (`extract_data_v9`) also gets each figure's Gemini reading
  from `parsed.json` (plotted values as a table, XRD phases, process steps), so results shown
  only in figures are extracted (confidence Low).
- Each distinct experimental condition (replacement level, curing or carbonation time,
  dosage...) is its own row; strengths at different ages stay in one row's age columns.
- Gemini answers in rows (IDs, location and quote once per row, then its values), short
  enough for a whole paper to fit its ~65k-token output limit (thinking included) in one
  call. An answer cut off at that limit is asked for again in two parts (sheets 2-4 and 6,
  then 5, 7 and 8).
- Code: `agents/data_extractor.py`, `data/extraction_rows.py` (rules and rows),
  `data/extraction_tables.py` (table definitions) and `pipelines/extract_data.py`.

### Improving the extraction prompt

Prompt versions are scored against 50 hand-extracted papers
([human_extracted_data/](human_extracted_data/)), split in
[configs/extraction_benchmark.yaml](configs/extraction_benchmark.yaml) into **dev** papers
to tune on and **test** papers to grade on. Benchmark runs never touch the production tables.

```bash
# 1. Write prompts/extract_data_v<N>.yaml and set data_extraction.prompt_name to it
uv run extract-data --benchmark dev             # new run in steel_slag_extraction_runs
uv run judge-extraction --run-id <run id>       # scores -> terminal, BigQuery, xlsx
uv run judge-extraction --run-id <run id> --adjudicate  # also check disputes vs the paper
# 2. Repeat on dev until it's better; then grade it once on test
uv run extract-data --benchmark test
uv run judge-extraction --run-id <run id>
uv run promote-extraction --run-id <run id>     # copies to production only if F1 is higher
uv run compare-extraction [--split dev]         # every run's scores side by side
```

`compare-extraction` prints one line per run and saves
`reports/extraction_eval/runs_comparison.xlsx` (sheet *Runs*: overall scores and cost;
*Sheets*: scores per run and sheet), rebuilt from BigQuery each time. Compare runs of the
same split only; they score different papers otherwise.

`judge-extraction` and `compare-extraction` report **value scores** first. For each paper, sheet and value column (numbers,
fixed-vocabulary labels and conditions such as times and pressures; not free text), the
reference's values and the extracted values are matched one to one, whatever rows they are
in, numbers within 5% ([value_metrics.py](src/steel_slag_slm/evaluation/value_metrics.py)).
Value precision is the share of extracted values in the reference, value recall the share of
reference values extracted. No model is involved, so they cost nothing, give the same result
every time and compare any two runs, whoever judged them.

Numbers in a unit column within 2% of each other count as the same value in the row and cell
scores too, so two readings of one plot (42.5 vs 43.0) agree.

`judge-extraction` (Gemini, `extraction_judge:` in `configs/base.yaml`) also pairs each sheet's
extracted rows with the reference rows; code decides empty and identical cells, and the judge
rules on the rest as match, partial (half credit) or mismatch. Per run, per sheet and per
column it reports:

| Metric | Meaning |
|---|---|
| fill rate | filled data cells / (rows × all data columns), extracted and reference |
| row recall | reference rows paired with an extracted row |
| precision | extracted values that agree with the reference |
| recall | reference values extracted correctly |
| F1 | of precision and recall; the score promotion compares |
| data accuracy | agreement where both sides filled the cell, notes columns left out (two right answers can note different details) |
| adjudicated precision | precision once values the paper supports count as right |

Provenance (sheet 9) gets fill rates only. `--adjudicate` sends each paper's extra and
mismatched values with its parsed text to the judge model, which also catches values the
reference missed. Scores are stored per cell, so `promote-extraction` compares the candidate
and the last promoted run on today's test papers, judged by the same judge model and prompt
version; re-judge the older run after changing either. Judging costs about $0.30 a paper
with `gemini-3.1-pro-preview`; repeated judging moves small sheets' scores by a few points.

The references follow the same rules as the prompt (numbers only in unit columns, one row
per condition), or Gemini is scored wrong where it is right.
`uv run python scripts/human_extraction/check_reference.py --uncleaned` lists cells to fix;
`reference_cleaned` in `human_extracted_data/status_tracker.csv` records which papers are done.

## Semantic search

Semantic search lets you ask a question in plain language and get back the passages from the
papers that answer it, each with its paper, section and page.

Each parsed paper is split into short passages: paragraphs, tables and figures. A figure passage
holds its caption and Gemini's reading of it (summary, plus the plotted values, XRD phases or
process steps), or just the caption until the figure has been read. An
embedding model turns each passage into numbers that capture its meaning, and the result is saved
as a search index in the shared Drive folder (`_search_index/<parsed folder>/_embeddings/`),
which anyone on the project can build or update.

A search runs in two stages. First, meaning match and keyword (BM25) match find the 5 papers
most likely to hold the answer. Then a reranker model reads the question with every passage of
those papers, each with its paper's title, and orders them. Tables also carry their paper's title
in the index, since a table on its own is mostly numbers.

```bash
uv run build-index                      # add new and re-parsed papers to the index
uv run build-index --rebuild            # rebuild everything, e.g. after changing how papers are split
uv run search-index "free lime volume stability"
uv run build-index --parsed-folder _parsed_approved   # use another parsed folder (any command)

# Score search on a fixed test set, over that set's own papers (run on a GPU, e.g. Colab)
uv run evaluate-retrieval --eval-set configs/retrieval_eval_heldout.yaml \
    --papers-dir papers/ --titles titles.json
```

- Each parsed folder has its own index, under `_search_index/<parsed folder name>/`. The parsed
  folder itself is only read, so its owner does not need to share edit access.
- A paper parsed again since it was indexed (a new parse generation) has its old passages
  replaced on the next `build-index`.

- `parse-pdfs` updates the search index of the folder it parsed into once parsing is done, so
  newly parsed and re-parsed papers become searchable without a separate step. Pass
  `--skip-index` to leave the index alone.
- The reranker is slow on a CPU (it scores about 400 passages per question); on a T4 GPU a
  search takes about 7 seconds.
- `evaluate-retrieval` asks a fixed set of questions with known answers and reports how often
  the answer is in the top 5, 10 and 20 results. With `--papers-dir` it searches only the papers
  the set lists (`corpus:` in its YAML), from local `<paper id>.json` files; without it, it
  searches the saved index. The test sets and their latest scores are in
  [docs/retrieval_experiments.md](docs/retrieval_experiments.md).
- Settings are in the `embedding:` section of `configs/base.yaml`. Design notes are in
  [docs/adr/003-semantic-search-index.md](docs/adr/003-semantic-search-index.md).

## Figure extraction (local)

`extract-figures` pulls the figures (plots, charts, process diagrams, micrographs) out of PDFs on
your machine. Nothing is read from or written to Drive yet.

```bash
uv run extract-figures path/to/W2114885577.pdf     # one paper
uv run extract-figures path/to/pdf_folder/          # every PDF in a folder
uv run extract-figures papers/ --out-dir /tmp/figs  # somewhere other than data/interim/figures
```

Each paper gets a folder named after its PDF, emptied first on every run so it only holds that
run's output:

- `figure_01.png`, `figure_02.png`, ...: the figures.
- `figures.json`: each figure's number, caption, page, position, type and PDF text.
- `annotated.pdf`: the paper with boxes drawn on it: thin red for every region Docling
  detected (with its type), blue for caption blocks, thick green for the figures kept
  ("Fig. N · type"). Open it to see what was found, merged or missed.

How it works:

1. Docling's layout model finds figure regions, including vector plots that aren't image files,
   and labels each one (`line_chart`, `bar_chart`, `flow_chart`, `photograph`, ...).
2. Cleanup matches each figure to the "Fig. N" caption below, beside or above it, splits regions
   that hold two stacked figures, and drops logos, icons and small uncaptioned images.
3. PyMuPDF renders each region from the page, so crops are sharp at any size.
For callers that already have a converted Docling document,
`detect_figures_in_document(document)` reuses the figure and caption detection without another PDF conversion. The standalone command continues to convert PDFs through `detect_figures(pdf_bytes, converter)`.


Settings: the `figure_extraction:` section of `configs/base.yaml`.

Internally, matched figures retain their caption's page and bounding box in
`Figure.caption_block`. Captions recovered from a figure's PDF text layer have text and a
number but no exact caption box. Raster-only captions cannot be recovered from that text
layer. Internal-caption recovery runs before filtering, so a recovered caption can retain a legitimate smaller figure.

`RenderedFigure` holds a crop's PNG bytes, pixel dimensions, render DPI, region text and source figure metadata for publication. These internal records do not change the current
local output layout or JSON format.

`render_figures(pdf_bytes, layout, settings)` returns these records without converting the PDF or writing files. Both standalone extraction and unified parsing reuse this function.
The standalone command still owns its local output directory and annotated PDF.

### Unified parsing API

`build_docling_converter(include_figures=True)` enables figure classification while retaining
OCR and table structure. `parse_pdf_with_figures(pdf_bytes, converter, settings)` uses one
Docling conversion for text, tables and figure crops, returning crops in
`ParsedDocument.figures`.

`parse-pdfs` now uses the integrated parser. It publishes version-four combined manifests
containing Markdown, tables, provenance-bearing content blocks and figure image references.
The legacy `parse_pdf` API remains available but is not used by Drive publication.

Each paper retains its existing folder and images/ subfolder. A single `parsed.json` holds
the current result and is replaced on reparse; the root index references its Drive ID.
Images keep generation-specific names, and previous image generations are not automatically
deleted. JSON snapshots are not retained.

Extraction-setting or parser-version changes trigger reparsing. Use --reparse when a source
PDF changes behind the same Drive link, and --retry-skipped to reconsider deliberately
skipped papers. Run only one publisher against an output root at a time.

### Export unified parses for local review

`export-parsed` downloads the current manifest referenced by the Drive index and verifies
each PNG's checksum and pixel dimensions. It does not parse PDFs or call Gemini.

Export one paper from the production output folder:

```bash
uv run export-parsed --out-dir data/interim/unified_parses --max-papers 1
```

Export every successfully parsed paper:

```bash
uv run export-parsed --out-dir data/interim/unified_parses
```

To select papers explicitly, pass `--paper-ids` followed by actual OpenAlex IDs present in
the parsed index. Do not use placeholder IDs.

Local output has this layout:

```text
data/interim/unified_parses/
└── <paper_id>/
    ├── parsed.json
    └── images/
        ├── <generation>_figure_01.png
        └── <generation>_figure_02.png
```

The local manifest is replaced only after all referenced images are downloaded and verified.
Existing image generations and readings are retained. The exporter requires version-four
Drive manifests; migrate older parses before exporting them.

## Reading figures with Gemini (local)

`read-figures` sends local figure PNGs to Gemini on Vertex AI. This incurs model usage charges.
It accepts both unified `parsed.json` manifests and legacy standalone `figures.json` outputs.

Configure Vertex AI credentials and the environment variables described in
[.env.example](.env.example). If using your own account through Application Default
Credentials, authenticate first:

```bash
gcloud auth application-default login
```

### Read figures exported from unified parsing

Export first, then read one paper:

```bash
uv run export-parsed --out-dir data/interim/unified_parses --max-papers 1
uv run read-figures --figures-dir data/interim/unified_parses --max-papers 1
```

After inspecting the readings and reported cost, read all papers in that local directory:

```bash
uv run read-figures --figures-dir data/interim/unified_parses
```

Each reading is saved beside its source image:

```text
data/interim/unified_parses/
└── <paper_id>/
    ├── parsed.json
    └── images/
        ├── <generation>_figure_01.png
        └── <generation>_figure_01.json
```

Unified reading results include the source figure ID and image checksum. A new parse
generation uses different image filenames, so its readings do not collide with those of
previous crops. The reader verifies checksums before reading unified images.

### Legacy standalone workflow

Standalone extraction remains available for local PDFs:

```bash
uv run extract-figures data/raw/test_pdfs/
uv run read-figures --max-papers 1
```

Standalone extraction empties each paper's output folder before writing its new artifacts.
Do not point it at a directory containing unified exports or readings you want to preserve.

Both extraction and reading support `--paper-ids` followed by actual IDs. For standalone
extraction, the PDF filename stem must match the selected ID.

### Results and checks

Readings describe plot axes, series and approximate values; XRD phases; process-diagram steps;
or photo summaries. Automatic checks compare reported labels against the figure's PDF text
and check numerical values against reported axes. These checks are not proof of scientific
accuracy; raster-only figures may have no PDF text for label verification.

The command reports usage cost and a full-corpus cost projection. Inspect sample outputs
before scaling up.

Model, concurrency and pricing settings are in the `figure_reading:` section of
[configs/base.yaml](configs/base.yaml). The prompt is
[prompts/read_figure.yaml](prompts/read_figure.yaml).

`parse-pdfs` reads figures with Gemini by default and stores each reading with its figure
in `parsed.json`, next to the text, tables and page locations. Papers parsed before this
only get their unread figures re-rendered and read; Docling doesn't run again. A figure
Gemini can't read is saved unread and retried by the next run. Reading stops by itself
once the run's estimated cost reaches `figure_reading.max_run_usd` (override with
`--max-gemini-usd`) or after many failures in a row; parsing carries on, and rerunning
the same command continues. Use `--no-figure-reading` to parse without Gemini.