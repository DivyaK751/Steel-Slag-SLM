# 001: Screen paper relevance with a Google ADK agent on Vertex AI Gemini

**Status:** accepted, 2026-10-16

## Context
The fetcher's keyword rules (`data/relevance.py`) let through papers the team later rejects.
Before full-text extraction we need a per-paper Yes/No/Unclear decision with a reason and a
quote, stored next to the paper's metadata in BigQuery.

## Decision
- Use Google ADK (`LlmAgent` with a pydantic `output_schema`) so future agents (extraction,
  tool-using agents) share one framework and set of helpers (`agents/runner.py`).
- Call Gemini Flash (`gemini-3.8-flash`, set in `configs/base.yaml`) through Vertex AI,
  billed to Lakshya's GCP project. Flash is enough for title and abstract screening.
- Read unscreened rows and write decisions with one parameterized `MERGE` per batch, which
  needs no staging table and only touches the decision columns.
- Store the score's confidence band computed in code, and drop any excerpt that isn't found
  verbatim in the abstract, rather than trusting the model's output for either.

## Consequences
- Each run needs ADC credentials with Vertex AI User on the billing project and write access
  to the papers table.
- Changing the prompt means bumping its `version`. Earlier decisions are not re-screened
  automatically.
