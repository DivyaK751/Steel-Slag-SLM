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



## PDF parsing

The `parse-pdfs` command reads paper IDs and Drive links from
`capstone-project-511200.steel_slag.papers_trial`. It downloads source PDFs from the configured
Drive folder, parses text and tables with Docling and embedded image resources with PyMuPDF, then
stores one JSON document per paper, separate image files, and an `index.json` registry in the
`_parsed` subfolder. The six PDFs used in the exploratory notebook are not production inputs.

Configure `DRIVE_FOLDER_ID`, `GOOGLE_CLOUD_PROJECT`, and Google Drive/BigQuery credentials as
described in `.env.example`. The table and output names are in the `pdf_parsing` section of
`configs/base.yaml`.

Start with one unindexed paper and inspect its JSON, images, and index entry in Drive:

```bash
uv run parse-pdfs --max-papers 1
```

Inspect that paper’s JSON, images, and index entry in the Drive subfolder. If the output looks correct, process all remaining unindexed papers:

```bash
uv run parse-pdfs
```

The command skips papers already recorded in the index. If a run fails before updating the index, rerunning it can replace same-named partial output files.