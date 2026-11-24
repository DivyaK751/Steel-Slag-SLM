# Table descriptions: who pays for Gemini

The **Describe parsed tables** workflow signs in to Drive with the team's saved login, like
every other workflow, but bills its Gemini calls to **one member's own Google Cloud
project**, never the team's. Each run picks that member in its `billed_to` input and reads
two of their secrets:

| Secret | Holds |
|---|---|
| `GOOGLE_CLOUD_PROJECT_<NAME>` | the ID of a project linked to that member's billing account |
| `GCP_SERVICE_ACCOUNT_JSON_<NAME>` | a key of a service account in that project allowed to call Vertex AI |

`<NAME>` is the member's name as in `OPENALEX_API_KEY_<NAME>`: `YASSINE`, `DIVYA`, `LAKSHYA`,
`ANISHA` or `KANE`. The run stops before any Gemini call if either secret is missing, if the
project is the team's, or if the key belongs to another project.

## One-time setup (the paying member, about 15 minutes)

Run these from the repo folder on your laptop. You need `gcloud` and `gh`, both signed in.

### 1. Sign in

```bash
gcloud auth login      # opens a browser: use the Google account that holds your credit
gh auth status         # should show you logged in to github.com
```

### 2. Find your billing account and pick a project

```bash
gcloud billing accounts list
```

Copy the `ACCOUNT_ID` (looks like `0X0X0X-0X0X0X-0X0X0X`) of the open account that holds your
credit. The Cloud Console shows the same ID, with the credit's balance, under **Billing →
Credits**.

Use a project of your own just for this, so its bill shows only these calls. List yours:

```bash
gcloud projects list
```

Reuse one from the `PROJECT_ID` column, or make a new one. A new ID needs 6–30 lowercase
letters, digits or hyphens, starts with a letter and must be unused anywhere on Google Cloud:

```bash
gcloud projects create <new-project-id>
```

If creating fails with a permission error, your school account can't create projects; reuse
one from the list instead.

### 3. Set the values once for this terminal

```bash
MEMBER=KANE                                   # your name as in OPENALEX_API_KEY_<NAME>
PROJECT_ID=<PROJECT_ID from step 2>
BILLING_ACCOUNT_ID=<ACCOUNT_ID from step 2>
SERVICE_ACCOUNT=table-describer@${PROJECT_ID}.iam.gserviceaccount.com
KEY_FILE=~/keys/table_describer_${PROJECT_ID}.json
```

### 4. Link billing, enable Vertex AI and make the service account

```bash
gcloud billing projects link "$PROJECT_ID" --billing-account "$BILLING_ACCOUNT_ID"
gcloud services enable aiplatform.googleapis.com --project "$PROJECT_ID"
gcloud iam service-accounts create table-describer --project "$PROJECT_ID" \
  --display-name "Describe parsed tables (GitHub Actions)"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${SERVICE_ACCOUNT}" --role roles/aiplatform.user
```

Check: `gcloud billing projects describe "$PROJECT_ID"` shows `billingEnabled: true`.

### 5. Store the key as your GitHub secrets

```bash
mkdir -p ~/keys && chmod 700 ~/keys
gcloud iam service-accounts keys create "$KEY_FILE" --iam-account "$SERVICE_ACCOUNT"
gh secret set "GCP_SERVICE_ACCOUNT_JSON_${MEMBER}" < "$KEY_FILE"
gh secret set "GOOGLE_CLOUD_PROJECT_${MEMBER}" --body "$PROJECT_ID"
rm "$KEY_FILE"         # GitHub keeps it; make a new key if you ever need one again
gh secret list | grep "_${MEMBER}"
```

If `keys create` says key creation is not allowed, your school's organization blocks
service-account keys: use a project outside it (one under a personal Google account with
your credit), then repeat steps 3–5. If `gh secret set` is refused, send the key privately to
the repo owner to add, as with the OpenAlex keys.

### 6. Set a budget alert

In the Cloud Console: **Billing → Budgets & alerts → Create budget**. Scope it to your
project, set the amount (for example $50) and keep the 50%, 90% and 100% alerts. Alerts email
you; they don't stop spending. The workflow's `max_gemini_usd` stops it at its own estimate.

## Each run

**Actions → Describe parsed tables → Run workflow** on `main`, with `billed_to` set to the
paying member. Left blank, `paper_ids` and `max_papers` mean every parsed paper, and the
workflow keeps starting new runs (up to `runs_left`) until every table is described. The job
summary's first line says whose project paid. After the first run, check **Billing →
Reports** filtered to your project: a Vertex AI charge appears within a day.
