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
stores each paper under `_parsed/<paper_id>/`, with its parsed content in `parsed.json` and extracted images in an `images/` subfolder. The root `_parsed/index.json` registry records the Drive file and folder IDs for each paper. The six PDFs used in the exploratory notebook are not production inputs.

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

`build-index` embeds the text and tables of every paper in `_parsed/index.json` and saves the
search index to `_parsed/_embeddings/` in Drive. Nothing is written to your machine except the
embedding model's cache. Papers already in the index are skipped, so each run only adds new ones.

```bash
uv run build-index --max-papers 1       # index one new paper first
uv run build-index                      # index every remaining paper
uv run search-index "free lime volume stability" --top-k 5
uv run search-index "chemical composition" --kind table
uv run evaluate-retrieval               # score the labelled queries in configs/base.yaml
```

- Model, chunk sizes and save frequency: the `embedding:` section of `configs/base.yaml`.
  Changing the model means deleting `_parsed/_embeddings/` and rebuilding.
- Labelled queries: the `retrieval_eval:` section. A query counts as a hit when a top-k chunk
  from the given paper contains the expected text.
- Figures are indexed only through their captions; image contents are not analysed.
- Text and table results carry their section, not a page number. See
  [docs/adr/003-semantic-search-index.md](docs/adr/003-semantic-search-index.md).
- Code: `src/steel_slag_slm/rag/` (chunking, embeddings, vector store, search),
  `data/embedding_store.py` (Drive storage) and `pipelines/build_index.py`.
