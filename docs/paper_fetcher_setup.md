# Paper fetcher: setup and daily workflow

The paper fetcher is a background job that fills the team's Google Drive folder with steel
slag papers. Once it's set up, it runs every night on GitHub's servers with no laptop and no
manual steps.

## What happens every night

At 00:15 UTC, just after OpenAlex resets everyone's daily budget, GitHub Actions starts the
**Fetch papers** workflow. It runs once per team member, one after another, each with that
member's OpenAlex key. Each run:

1. **Reads the queue** (`_fetcher_queue_ids.txt` in the folder): OpenAlex IDs of relevant papers
   found earlier but not downloaded yet.
2. **Refills it when it's empty**: every search in `configs/base.yaml` reads its *next* 5 result
   pages (where each search left off is kept in `_fetcher_search_progress.txt`), filters them
   with the relevance rules from the team's paper feedback, and queues papers that aren't in the
   folder, in `Rejected`, or known to have no PDF. A search that reaches its last page starts
   over from page 1, which picks up newly published papers.
3. **Downloads queued papers**, trying the free publisher copy first and OpenAlex's paid copy
   ($0.01) only if that fails, and uploads each as `<OpenAlex ID> - <title>.pdf`.
4. **Stops** when that member's daily budget (about $1) is used up, saving what's left in the
   queue for the next member's run, which picks up where it stopped.

Papers with no downloadable PDF are written to `_fetcher_no_pdf_ids.txt` in the folder, so
later nights don't waste time retrying them. Leave all three `_fetcher_*.txt` files in place;
deleting the progress file just makes every search start again from page 1.

### BigQuery paper registry

Every paper in the folder also gets one row in the BigQuery table `steel_slag.papers`
(OpenAlex ID, title, authors, year, DOI, language, abstract, keywords, Drive link). Later steps,
such as relevance screening, read abstracts from there instead of opening Drive.

- Each fetch run adds a row for every paper it uploads.
- Before the fetch runs, a **sync-registry** job adds rows for any paper in the folder that has
  none yet: on its first run that is every paper already there, after that only papers added by
  hand or missed because a BigQuery write failed. It looks papers up on OpenAlex by ID, 100 per
  request, for a fraction of a cent.
- Both are skipped until the two BigQuery secrets in step 4 are set.

## One-time setup

Done once, by the person who **owns the Drive folder** (the job uploads as them), in about
15 minutes. Step 4 needs **admin** access to the GitHub repo (the repo owner).

The job signs in to Google **as the folder owner**, using a login saved once from their
laptop. Uploaded papers count against the owner's Drive storage (about 3–15 GB for 3,000
papers). Columbia LionMail accounts have no Shared Drives, so a robot "service account"
can't be used: it has no storage of its own (see [the alternative](#alternative-shared-drive--service-account)).

### Step 1: Collect the OpenAlex keys (each member)

Each member signs in at <https://openalex.org/settings/api>, copies their free API key, and
sends it privately to whoever does step 4. Fewer than five keys is fine; missing ones are
skipped.

### Step 2: Register the sign-in app in Google Cloud

Google only lets a registered app ask for a login.

1. Go to <https://console.cloud.google.com> and pick or create a project (top bar → project
   picker, e.g. `steel-slag`).
2. **APIs & Services → Library**, search **Google Drive API**, click **Enable**.
3. **Google Auth Platform → Branding** (older consoles: **APIs & Services → OAuth consent
   screen**): fill in an app name (e.g. `Steel Slag Fetcher`) and your email, then save.
4. **Audience**: if the project is under **columbia.edu** (shown on the console's welcome page),
   choose **Internal**: only Columbia accounts can sign in, there's no "unverified app"
   warning, and the login doesn't expire. Otherwise choose **External** and click **Publish
   app** so the status says **In production**; in "Testing" status Google ends the login
   after 7 days and the nightly job stops.
5. **Clients → Create client → Desktop app** (older consoles: **Credentials → Create
   credentials → OAuth client ID → Desktop app**), click **Create**, then **Download JSON**.
6. Save it as `~/keys/steel_slag_oauth_client.json`:

```bash
mkdir -p ~/keys && mv ~/Downloads/client_secret_*.json ~/keys/steel_slag_oauth_client.json
```

### Step 3: Sign in once and save the login

From the repo folder, on the folder owner's laptop:

```bash
uv sync
```

```bash
cp .env.example .env
```

Edit `.env`: set `OPENALEX_API_KEY` (yours), `OPENALEX_CONTACT_EMAIL`, `DRIVE_FOLDER_ID` (the
part of the folder's URL after `/folders/`), and keep
`GOOGLE_OAUTH_CLIENT_FILE=~/keys/steel_slag_oauth_client.json`. Then run one paper:

```bash
uv run fetch-papers --max-papers 1
```

A browser tab opens. Sign in with the **account that owns the folder** (e.g. your LionMail).
With an **External** app Google warns "Google hasn't verified this app": click **Advanced → Go
to Steel Slag Fetcher (unsafe)** (it's your own app). Then click **Continue / Allow**. The run should end with a
`Summary:` line and one new PDF in the folder.

The login is now saved in `~/.config/steel_slag_slm/google_token.json`. **This file gives full
access to your Drive**: never commit it or paste it in chat.

Also create a subfolder named exactly `Rejected` inside the papers folder, if it isn't there.

### Step 4: Add the secrets to GitHub

On GitHub, open the repo → **Settings → Secrets and variables → Actions → New repository
secret**, and add each of these:

| Secret name | Value |
|---|---|
| `OPENALEX_API_KEY_YASSINE`, `OPENALEX_API_KEY_DIVYA`, `OPENALEX_API_KEY_LAKSHYA`, `OPENALEX_API_KEY_ANISHA`, `OPENALEX_API_KEY_KANE` | Each member's own OpenAlex key |
| `OPENALEX_CONTACT_EMAIL` | A contact email; OpenAlex and publishers ask for one |
| `DRIVE_FOLDER_ID` | The folder ID, just the ID: no URL, no quotes |
| `GOOGLE_OAUTH_TOKEN_JSON` | The **entire contents** of `~/.config/steel_slag_slm/google_token.json` from step 3 |
| `GOOGLE_CLOUD_PROJECT` | The Google Cloud project that holds BigQuery, e.g. `capstone-project-511200` |
| `GCP_SERVICE_ACCOUNT_JSON` | The **entire contents** of a JSON key for a service account in that project with the *BigQuery Data Editor* and *BigQuery Job User* roles |

When `GOOGLE_OAUTH_TOKEN_JSON` is set, the job uses it and ignores
`GOOGLE_SERVICE_ACCOUNT_JSON`. Secrets are encrypted and hidden in logs, even though the repo
is public.

With the GitHub CLI instead (`brew install gh`, then `gh auth login`), from the repo folder.
The first prompts for the value; the second reads the file directly:

```bash
gh secret set OPENALEX_API_KEY_YASSINE
```

```bash
gh secret set GOOGLE_OAUTH_TOKEN_JSON < ~/.config/steel_slag_slm/google_token.json
```

### Step 5: Launch it

GitHub only runs scheduled jobs from `main`, so changes to the workflow take effect once
merged.

1. Do a first run right away instead of waiting for midnight: repo → **Actions** → **Fetch
   papers** → **Run workflow** → type `3` in the "max papers" box → **Run workflow**.
2. When it finishes (green check), open a run's log and look for lines like
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

### Alternative: Shared Drive + service account

If the folder lives in a **Shared Drive** (not available on LionMail), a robot service
account can upload instead of a person, and nobody's storage is used:

1. In Google Cloud: **IAM & Admin → Service Accounts → Create service account**, then
   **Keys → Add key → Create new key → JSON**.
2. In Drive: the Shared Drive's name → **Manage members** → add the service account's email
   as **Content manager**.
3. Put the key file's entire contents in the `GOOGLE_SERVICE_ACCOUNT_JSON` secret and delete
   `GOOGLE_OAUTH_TOKEN_JSON`.

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
  A newly added search is read from page 1 at the start of the next run, even if the queue
  still has papers in it. A removed search is dropped from the progress file. Rewording a
  search counts as removing it and adding a new one, so it starts again from page 1.
- `relevance.exclude_title_terms`: titles containing these are skipped (e.g. `asphalt`).
- `relevance.include_terms` / `include_title_terms`: what makes a paper worth keeping.

Change it on a branch and open a PR like any other code change. The next night's run uses it.

## Running it by hand (optional)

Only needed for testing changes. Any member can do steps 2–3's commands on their own laptop
with the team's OAuth client JSON (shared privately) and sign in with their **own** Google
account that can edit the folder; their saved login stays on their laptop and isn't needed on
GitHub. A manual run uses that member's OpenAlex budget for the day. Running by hand while
the nightly job runs is safe: the re-check before each download prevents duplicates.

To fill the BigQuery registry by hand, set `GOOGLE_CLOUD_PROJECT` in `.env`, sign in with
`gcloud auth application-default login`, and run `uv run sync-registry`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Run log says `No OPENALEX_API_KEY_<NAME> secret set; skipping` | That member's key isn't in GitHub secrets (step 4). Harmless. |
| `404 File not found` from Drive | The account the job signs in as can't see the folder, or `DRIVE_FOLDER_ID` has extra text (URL, quotes). Re-enter just the ID. |
| `storageQuotaExceeded` / "Service Accounts do not have storage quota" | The job is using the service account on a My Drive folder. Set `GOOGLE_OAUTH_TOKEN_JSON` (steps 3–4). |
| `The saved Google login ... is missing or no longer valid` | The login expired or was revoked (or the app was left in "Testing"). Redo step 3 (delete `~/.config/steel_slag_slm/google_token.json` first) and update the `GOOGLE_OAUTH_TOKEN_JSON` secret. |
| "Access blocked" / "admin restricted" when signing in | Columbia blocks the app for LionMail. Ask CUIT to allow it, or move the folder to a personal Google account that owns it. |
| `Environment variable ... is not set` | A secret is missing or misspelled (step 4). |
| Run says `Stopping: ... budget` right away | That key's budget for today is already used up, e.g. by a manual run. It resets at midnight UTC. |
| The scheduled run stopped happening | GitHub pauses schedules in public repos after 60 days without commits. Re-enable it under **Actions → Fetch papers**. |
| Google Cloud won't let you create a project | Create it with a personal Google account; the login in step 3 can still be the LionMail account that owns the folder. |
