# Paper fetcher: setup and daily workflow

The paper fetcher is a background job that fills the team's Google Drive folder with steel
slag papers. Once it's set up, it runs every night on GitHub's servers with no laptop and no
manual steps.

## What happens every night

At 00:15 UTC, just after OpenAlex resets everyone's daily budget, GitHub Actions starts the
**Fetch papers** workflow. It runs once per team member, one after another, each with that
member's OpenAlex key. Each run:

1. **Lists the Drive folder** (and its `Rejected` subfolder) to see which papers we already have.
2. **Searches OpenAlex** with the queries in `configs/base.yaml`.
3. **Filters** each result with the relevance rules from the team's paper feedback (keep
   cementitious / CO2 / characterization, skip asphalt / soil / leaching / GGBFS).
4. **Skips duplicates**: papers already in the folder, in `Rejected`, or known to have no PDF.
   It re-checks Drive right before each download, in case another run just added the paper.
5. **Downloads the PDF**, trying the free publisher copy first and OpenAlex's paid copy
   ($0.01) only if that fails.
6. **Uploads it** to the folder as `<OpenAlex ID> - <title>.pdf`.
7. **Stops** when that member's daily budget (about $1) is used up, then the next member's run
   starts.

Papers with no downloadable PDF are written to `_fetcher_no_pdf_ids.txt` in the folder, so
later nights don't waste time retrying them. Leave that file in place.

## One-time setup

Done once, by one person, in about 30 minutes. Steps 2 and 3 need the Drive folder to be in a
**Shared Drive** (a service account has no storage of its own, so it can't upload into
someone's "My Drive"). Step 4 needs **admin** access to the GitHub repo (the repo owner).

### Step 1: Collect the OpenAlex keys (each member)

Each member signs in at <https://openalex.org/settings/api>, copies their free API key, and
sends it privately to whoever does step 4. Fewer than five keys is fine; missing ones are
skipped.

### Step 2: Create a service account (the job's Google login)

The job runs on GitHub's servers, where nobody can click "Sign in with Google", so it signs
in as a **service account**: a robot Google account that belongs to the team.

1. Go to <https://console.cloud.google.com> and create a project (top bar → project picker →
   **New project**, e.g. `steel-slag`).
2. **APIs & Services → Library**, search **Google Drive API**, click **Enable**.
3. **IAM & Admin → Service Accounts → Create service account**. Name it `paper-fetcher`, click
   **Create and continue**, then **Done** (skip the optional role steps).
4. Click the new service account → **Keys → Add key → Create new key → JSON → Create**. A
   `.json` file downloads. Treat it like a password: never commit it or post it in chat.
5. Copy the service account's email, e.g. `paper-fetcher@steel-slag.iam.gserviceaccount.com`.

### Step 3: Give the service account access to the Drive folder

1. In Google Drive, open the Shared Drive that holds the papers folder.
2. Click the Shared Drive name → **Manage members**, paste the service account's email,
   choose **Content manager**, uncheck "Notify people", click **Share**.
3. Inside the papers folder, create a subfolder named exactly `Rejected`.
4. Copy the folder ID: the part of the folder's URL after `/folders/`.

### Step 4: Add the secrets to GitHub

On GitHub, open the repo → **Settings → Secrets and variables → Actions → New repository
secret**, and add each of these:

| Secret name | Value |
|---|---|
| `OPENALEX_API_KEY_1` … `OPENALEX_API_KEY_5` | One member's OpenAlex key each |
| `OPENALEX_CONTACT_EMAIL` | A team email; OpenAlex and publishers ask for a contact |
| `DRIVE_FOLDER_ID` | The folder ID from step 3 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The **entire contents** of the JSON key from step 2 |

Secrets are encrypted and never shown in logs, even though the repo is public.

Alternatively, with the GitHub CLI (`brew install gh`, then `gh auth login`), from the repo
folder. Each command prompts for the value:

```bash
gh secret set OPENALEX_API_KEY_1
```

```bash
gh secret set GOOGLE_SERVICE_ACCOUNT_JSON < ~/keys/steel_slag_service_account.json
```

### Step 5: Launch it

GitHub only runs scheduled jobs from `main`, so the job starts once this branch is merged:

1. Open a pull request for the branch and get it approved and merged.
2. Do a first run right away instead of waiting for midnight: repo → **Actions** → **Fetch
   papers** → **Run workflow** → type `3` in the "max papers" box → **Run workflow**.
3. When it finishes (green check), open a run's log and look for lines like
   `Added (free) W2068066265: An Overview of Utilization of Steel Slag`, and check that the
   PDFs appear in the Drive folder.

With the CLI instead:

```bash
gh workflow run fetch_papers.yml -f max_papers=3
```

```bash
gh run watch
```

From then on it runs every night by itself.

## Typical workflow once it's running

Nothing has to be done for the job to keep going. The team's routine is:

| When | Who | What |
|---|---|---|
| Every night | GitHub | Adds up to ~100+ papers per member key to the Drive folder |
| Whenever | Anyone | Skim new papers in the folder |
| For papers that aren't useful | Anyone | **Move** them into `Rejected` (don't delete them, or the job will download them again) |
| Weekly (optional) | Anyone | Glance at **Actions → Fetch papers** for red ❌ runs |
| When we have enough papers | Repo admin | **Actions → Fetch papers → ⋯ → Disable workflow** |

## Changing what gets fetched

Everything is in `configs/base.yaml`; no code changes needed:

- `fetch.queries`: the OpenAlex searches. Keep phrases in quotes, e.g. `'"ladle slag" cement'`.
- `relevance.exclude_title_terms`: titles containing these are skipped (e.g. `asphalt`).
- `relevance.include_terms` / `include_title_terms`: what makes a paper worth keeping.

Change it on a branch and open a PR like any other code change. The next night's run uses it.

## Running it by hand (optional)

Only needed for testing changes. Each member signs in with their **own** Google account, so
no key file is passed around.

**Once, by one person:** in the same Google Cloud project, open **APIs & Services → OAuth
consent screen** and choose **Internal** if it's offered. If only **External** is offered,
pick it and add the teammates' emails as **Test users**. Then go to **APIs & Services →
Credentials → Create credentials → OAuth client ID → Desktop app**, download the JSON, and
share it with the team privately.

**Each member**, from the repo folder:

```bash
uv sync
```

```bash
cp .env.example .env
```

Edit `.env`: set your `OPENALEX_API_KEY`, `OPENALEX_CONTACT_EMAIL`, `DRIVE_FOLDER_ID`, and
`GOOGLE_OAUTH_CLIENT_FILE` (the path where you saved the OAuth client JSON). Then:

```bash
uv run fetch-papers --max-papers 3
```

A browser tab opens the first time; sign in with the Google account that can edit the folder
and click **Allow**. Later runs reuse the saved login. With an **External** app, Google ends
the login after 7 days; delete `~/.config/steel_slag_slm/google_token.json` and run again.

Running by hand while the nightly job runs is safe: the re-check before each download
prevents duplicates.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Run log says `No OPENALEX_API_KEY_N secret set; skipping` | That member's key isn't in GitHub secrets (step 4). Harmless. |
| `403` / `insufficient permissions` from Drive | The service account isn't a member of the Shared Drive, or isn't a **Content manager** (step 3). |
| `storageQuotaExceeded` | The folder is in someone's "My Drive", not a Shared Drive. Move it into a Shared Drive. |
| `Environment variable ... is not set` | A secret is missing or misspelled (step 4). |
| Run says `Stopping: ... budget` right away | That key's budget for today is already used up, e.g. by a manual run. It resets at midnight UTC. |
| The scheduled run stopped happening | GitHub pauses schedules in public repos after 60 days without commits. Re-enable it under **Actions → Fetch papers**. |
| Google Cloud won't let you create a project or key | The university may restrict it. Ask IT, or create the project with a personal Google account; only the Shared Drive membership has to be on the university account. |
