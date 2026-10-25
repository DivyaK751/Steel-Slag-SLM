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

The `parse-pdfs` command reads paper IDs and Drive links from
`capstone-project-511200.steel_slag.screening_evaluation`. It downloads source PDFs from the configured
Drive folder, parses text and tables with Docling and embedded image resources with PyMuPDF, then
stores each paper under `_parsed/<paper_id>/`, with its parsed content in `parsed.json` and extracted images in an `images/` subfolder. The `parsed.json` includes per-item text/table page and bounding-box provenance, while keeping the existing Markdown and table fields. The root `_parsed/index.json` registry records the Drive file and folder IDs for each paper. The six PDFs used in the exploratory notebook are not production inputs.

Configure `DRIVE_FOLDER_ID`, `GOOGLE_CLOUD_PROJECT`, and Google Drive/BigQuery credentials as
described in `.env.example`. The table and output names are in the `pdf_parsing` section of
`configs/base.yaml`.

Start with one unindexed paper and inspect its JSON, images, and index entry in Drive:

```bash
uv run parse-pdfs --max-papers 1
```

Inspect that paper’s JSON, images, and index entry in the `_parsed` Drive subfolder. If the output looks correct, process all remaining unindexed papers:

```bash
uv run parse-pdfs
```

The command skips papers already recorded in the index. If a run fails before updating the index, rerunning it can replace same-named partial output files.

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

- The parse workflow runs `build-index` after parsing, so new papers are added automatically.
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

Each paper gets a folder named after its PDF, with `figure_01.png`, `figure_02.png`, ... and a
`figures.json` listing each figure's number, caption, page, position and type.

How it works:

1. Docling's layout model finds figure regions, including vector plots that aren't image files,
   and labels each one (`line_chart`, `bar_chart`, `flow_chart`, `photograph`, ...).
2. Cleanup matches each figure to the "Fig. N" caption below, beside or above it, splits regions
   that hold two stacked figures, and drops logos, icons and small uncaptioned images.
3. PyMuPDF renders each region from the page, so crops are sharp at any size.

Settings: the `figure_extraction:` section of `configs/base.yaml`. A figure whose caption is
printed inside the image keeps an empty caption and no figure number.
