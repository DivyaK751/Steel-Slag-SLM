# Steel-Slag-SLM — paper collection

This repository collects literature for the steel-slag cement/concrete project.
OpenAlex retrieves candidate works using steel-slag terms plus binder-context
terms; deterministic code reconstructs abstracts and removes duplicate OpenAlex
IDs/DOIs; a planned LLM stage will distinguish binder/SCM studies from
aggregate-only, asphalt, soil, and unrelated studies. Only accepted papers are
eligible for PDF download.

PDFs are stored locally as `paper_collection_pipeline/data_ingestion/outputs/pdfs/W<OpenAlex-ID>.pdf`.
Optional Google Drive sync targets the team's `Documents/Extracted Research
Papers` shared folder, using the same filenames and skipping names already in
that folder. Set its folder ID locally with `STEEL_SLAG_DRIVE_FOLDER_ID`.

## Set up and run

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r paper_collection_pipeline/data_ingestion/requirements.txt
cp .env.example .env
```

Edit the untracked `.env` locally. Put the OpenAlex key and shared-drive folder
ID there if needed; do not put credentials in source, command history, or GitHub.
`.env` is ignored by Git and loaded by the pipeline at startup.

Run offline tests:

```bash
.venv/bin/python -m unittest discover -s paper_collection_pipeline/data_ingestion/tests -v
```

Run a one-page retrieval preview, then full retrieval and candidate preparation:

```bash
.venv/bin/python -m paper_collection_pipeline.data_ingestion.run_retrieval --max-pages 1
.venv/bin/python -m paper_collection_pipeline.data_ingestion.run_retrieval
.venv/bin/python -m paper_collection_pipeline.data_ingestion.prepare_candidates
```

Review the CSVs under `paper_collection_pipeline/data_ingestion/outputs/`.
Generated outputs and downloaded papers are intentionally excluded from Git.

## Current implementation status

OpenAlex retrieval, candidate cleanup, classifier prompt/output validation, and
PDF delivery/optional Drive sync are implemented. The actual LLM provider call
and batch-classification stage is **not yet implemented**; delivery expects a
`classified.csv` containing a `decision` column. Therefore, this is a working
retrieval foundation, not yet an end-to-end automated classifier pipeline.

## Duplicate handling for a team

Candidate preparation removes duplicate OpenAlex IDs and normalized DOIs within
one run. PDFs use the stable OpenAlex-ID filename, and the shared-drive uploader
skips names already in the destination, so sequential runs by teammates avoid
re-uploading the same work. Google Drive does not enforce unique filenames;
simultaneous uploads can race. Until a shared transactional registry is added,
coordinate Drive uploads so only one teammate syncs at a time.

For local final delivery after classification:

```bash
.venv/bin/python -m paper_collection_pipeline.data_ingestion.deliver_papers
```

Drive uploads are opt-in. Install the optional dependencies from
`paper_collection_pipeline/data_ingestion/requirements-drive.txt`, configure
Google authentication locally or in Colab, then run:

```bash
.venv/bin/python -m paper_collection_pipeline.data_ingestion.deliver_papers --upload-to-drive
```

See [data-ingestion documentation](paper_collection_pipeline/data_ingestion/README.md)
for stage outputs, duplicate behavior, and Drive details.