# Data ingestion pipeline

This package contains the production retrieval and delivery code. Query audits and
other experiments live separately in `paper_collection_pipeline/experiments/`.

## Outputs

Production files are written under `data_ingestion/outputs/`:

- `candidates_raw.csv`: OpenAlex records from retrieval.
- `candidates_clean.csv` and `no_abstract.csv`: deduplicated records split by abstract availability.
- `classified.csv`: input to the delivery step after a classifier has added a `decision` column.
- `relevant_papers.csv`, `unclear_papers.csv`, and `needs_manual_access.csv`: final manifests.
- `pdfs/W<OpenAlex numeric ID>.pdf`: locally downloaded PDFs, using the legacy filename convention.

The PDF naming convention is retained from the legacy script: the OpenAlex work
ID is the filename, e.g. `W3177678890.pdf`. It is not based on the title, so
filenames remain stable and duplicate checks are deterministic.

## Google Shared Drive destination

Optional Drive sync targets the legacy shared-drive folder
`Documents/Extracted Research Papers`. Set its folder ID outside the repository
using `STEEL_SLAG_DRIVE_FOLDER_ID`; the ID is intentionally not hard-coded in
the public source. Upload checks the folder contents first and skips filenames
already present. Shared-drive API flags are enabled on both listing and upload.

Google Drive is optional. Install its dependencies only if Drive upload is
needed:

```bash
.venv/bin/python -m pip install -r paper_collection_pipeline/data_ingestion/requirements-drive.txt
```

In Colab, the optional uploader invokes Google user authentication. Locally, set
up Google Application Default Credentials with Drive access before upload. Never
put Google credentials or OpenAlex API keys in source code or tracked files.

## Run delivery

After a classifier has written `outputs/classified.csv`, create the accepted and
review manifests and download accepted open-access PDFs locally:

```bash
.venv/bin/python -m paper_collection_pipeline.data_ingestion.deliver_papers
```

To also upload valid local PDFs to the legacy Shared Drive destination, explicitly
add `--upload-to-drive`:

```bash
.venv/bin/python -m paper_collection_pipeline.data_ingestion.deliver_papers --upload-to-drive
```

The delivery step uses only papers with `decision == "Yes"` for PDF downloads.
It tries OpenAlex `open_access.oa_url` and `primary_location.pdf_url`, validates
the `%PDF-` signature, and puts papers with no usable PDF in
`needs_manual_access.csv`. `Unclear` papers go to `unclear_papers.csv`. The
uploader itself can be run separately against the local PDF directory with
`python -m paper_collection_pipeline.data_ingestion.drive_upload`.

The LLM provider call and batch-classification stage are still a separate
implementation task; the existing `classify.py` currently builds and validates
classifier prompts/results but does not make provider API calls.
