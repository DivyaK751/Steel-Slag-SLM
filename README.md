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

The pilot workflow does not build embeddings or call Gemini. Local unit tests, linting and
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

Use `--reparse` if PDF bytes change behind the same Drive link; the normal reuse path does not
download the source to detect this change. Bump `PIPELINE_REVISION` when extraction code changes
alter the output.

Conversion failures are logged and leave the paper retryable. The CLI exits unsuccessfully
when parsing failures occur. Storage errors abort the run.

Parsing accepts figure-only papers, but the text search index cannot index a paper that
produces no text/table chunks. Figure pixels and Gemini readings are not automatically
embedded. After approved production migration, rebuild the existing text search index
explicitly; ordinary indexing does not refresh papers already indexed.

## Semantic search

Semantic search lets you ask a question in plain language and get back the passages from the
papers that answer it, each with its paper, section and page.

Each parsed paper is split into short passages (paragraphs, tables and figure captions). An
embedding model turns each passage into numbers that capture its meaning, and the result is saved
as a search index in Drive (`_parsed/_embeddings/`). A search finds the passages closest in meaning
to the question.

```bash
uv run build-index                      # add newly parsed papers to the index
uv run build-index --rebuild            # rebuild everything, e.g. after changing how papers are split
uv run search-index "free lime volume stability"
uv run evaluate-retrieval               # score search quality on a fixed test
```

- The `_parsed_v2` pilot workflow only parses; it does not build embeddings. Run `build-index`
  separately for the configured production folder after an approved migration.
- `evaluate-retrieval` asks 22 fixed questions about one paper, whose answers were found by
  reading the paper, and reports how high the right passage ranks.
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

Neither `parse-pdfs` nor `export-parsed` automatically runs figure reading.