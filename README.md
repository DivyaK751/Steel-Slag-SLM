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

### Google Drive access

Having access to the folder yourself is not enough: the script has to sign in to Google as
someone who can edit the folder. There are two ways, and `.env` sets exactly one of them.

| Who signs in | Used for | `.env` setting |
|---|---|---|
| **You**, with your own Google account | Running it on your machine | `GOOGLE_OAUTH_CLIENT_FILE` |
| A **service account** (a robot Google account) | The daily GitHub Actions job | `GOOGLE_APPLICATION_CREDENTIALS` |

#### Your own account (local runs)

Google only lets registered apps ask for a login, so **one person registers the app once**
and shares the file with the team:

1. Go to <https://console.cloud.google.com>, create a project (e.g. `steel-slag`), then open
   **APIs & Services → Library** and enable **Google Drive API**.
2. **APIs & Services → OAuth consent screen**: choose **Internal** if it's offered (only
   people at the university can sign in). If only **External** is offered, pick it and add
   all five teammates' emails under **Test users**.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**, application type
   **Desktop app**. Download the JSON file.
4. Share that JSON with the team privately (e.g. in the shared Drive). Never commit it.

**Each member** then does:

1. Save the JSON as `~/keys/steel_slag_oauth_client.json`.
2. In `.env`, set `GOOGLE_OAUTH_CLIENT_FILE=~/keys/steel_slag_oauth_client.json` and leave
   `GOOGLE_APPLICATION_CREDENTIALS` commented out.
3. Run `uv run fetch-papers --max-papers 3`. A browser tab opens: sign in with the Google
   account that can edit the folder and click **Allow**.

The login is saved in `~/.config/steel_slag_slm/google_token.json`, so later runs don't open
the browser. To sign in as someone else, delete that file. With an **External** app in
testing mode, Google expires the login after 7 days; delete the file and sign in again.

#### Service account (daily background job)

A scheduled job can't click **Allow** in a browser, so it signs in as a service account:

1. In the same Google Cloud project: **IAM & Admin → Service Accounts → Create service
   account** (e.g. `paper-fetcher`). Skip the optional role steps.
2. Open it → **Keys → Add key → Create new key → JSON**. Keep the file out of the repo.
3. In Drive, open the shared drive → **Manage members**, add the service account's email
   (`paper-fetcher@<project>.iam.gserviceaccount.com`) as **Content manager**.

The folder must be in a **Shared Drive**: a service account has no storage of its own, so
uploads into someone's "My Drive" fail.

### Run it on your machine

```bash
uv run fetch-papers --max-papers 3   # small trial run
uv run fetch-papers                  # run until today's budget is used up
```

### Daily background job (GitHub Actions)

`.github/workflows/fetch_papers.yml` runs every day at 00:15 UTC. It runs each member's key in
turn, so up to 5 × the daily budget is spent without two runs fetching the same paper. It
can also be started by hand from the repo's **Actions** tab. GitHub only runs scheduled jobs
from `main`, so it starts once this is merged.

One-time setup, after creating the service account above: in GitHub → **Settings → Secrets
and variables → Actions**, add:

- `OPENALEX_API_KEY_1` … `OPENALEX_API_KEY_5`, one per member (missing ones are skipped)
- `OPENALEX_CONTACT_EMAIL`
- `DRIVE_FOLDER_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON` (the full contents of the service account's JSON key)
