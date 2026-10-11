# Steel-Slag-SLM

## Install

```bash
uv sync
cp .env.example .env   # then fill in your own values
uv run pytest
```

## Paper fetcher

`fetch-papers` searches [OpenAlex](https://openalex.org) for open-access steel slag papers,
keeps the ones that match the team's relevance rules, and uploads their PDFs to the shared
Drive folder. It runs until the API key's daily budget (about $1, reset at midnight UTC) is
used up.

- **Relevance rules** live in `configs/base.yaml` under `relevance`, distilled from the
  "Feedback on papers" review: keep cementitious, CO2 carbonation, and characterization
  papers; skip asphalt, soil stabilization, slag-heap leaching, and blast furnace slag/GGBFS.
  Edit the term lists there; no code change needed.
- **Free first:** each PDF is tried from the publisher or repository first (free). OpenAlex's
  cached copy ($0.01) is only used as a fallback, so the budget stretches past ~100 papers.
- **No duplicates:** before each download the job checks the Drive folder for the paper's
  OpenAlex ID. Files are named `<OpenAlex ID> - <title>.pdf`.
- **Rejected papers:** move papers the team marks "No" into a `Rejected` subfolder of the
  shared folder instead of deleting them. The job checks that subfolder too and never
  downloads them again.

### Run it on your machine

```bash
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/cloud-platform
uv run fetch-papers --max-papers 5
```

### Daily background job (GitHub Actions)

`.github/workflows/fetch_papers.yml` runs every day at 00:15 UTC. It runs each member's key in
turn, so up to 5 × the daily budget is spent without two runs fetching the same paper. It
can also be started by hand from the repo's **Actions** tab.

One-time setup:

1. Create a Google Cloud service account, enable the Drive API, and download its JSON key.
2. Add the service account's email to the shared drive as a **Content manager**.
3. In GitHub → Settings → Secrets and variables → Actions, add:
   - `OPENALEX_API_KEY_1` … `OPENALEX_API_KEY_5`, one per member (missing ones are skipped)
   - `OPENALEX_CONTACT_EMAIL`
   - `DRIVE_FOLDER_ID`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` (the full key file contents)
