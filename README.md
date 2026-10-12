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
