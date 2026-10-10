# OpenAlex retrieval audit (experimental)

This folder is deliberately separate from `data_ingestion/`. The scripts here
compare candidate query profiles and create a small human-review sample; they do
not modify production retrieval settings or production outputs.

## Query profiles

- `binder_current`: the current production binder context terms.
- `binder_strict`: more specific cementitious/binder signals, without generic
  `concrete`, `mortar`, `cement`, or standalone `binder` terms.
- `carbonation`: a separate lane for carbonation and CO2-related work.
- `characterization`: a separate lane for material characterization work.

All profiles use the same configured steel-slag terms, language/year filters,
and blast-furnace exclusions, so this is a controlled comparison of application
terms. The exclusion behavior itself should be assessed separately because an
exclusion can also remove comparison papers.

## Run the audit

From the workspace root:

```bash
.venv/bin/python -m paper_collection_pipeline.experiments.retrieval_audit
```

The script requests the first, middle, and last accessible pages for each
profile, then samples five evenly spaced records from each page. It uses basic
page-number pagination only for this bounded experiment; OpenAlex limits this
mode to the first 10,000 results. The production pipeline continues to use
cursor pagination.

Outputs go to `experiments/outputs/`, never `data_ingestion/outputs/`:

- `retrieval_audit_sample.csv`: compact review sheet, with an empty
  `human_label` column and a `reviewer_notes` column.
- `retrieval_audit_summary.json`: API result counts and sampled page positions.

Fill `human_label` with exactly `Yes`, `No`, or `Unclear`. Follow the scope in
`steel_slag_ingestion_guide.md`: steel slag as a binder/SCM, useful carbonation,
or relevant characterization can qualify; aggregate-only, asphalt, soil, and
unrelated metal-recovery studies do not. Use `Unclear` rather than guessing.

Then summarize the labels:

```bash
.venv/bin/python -m paper_collection_pipeline.experiments.summarize_retrieval_audit
```

The report gives exploratory label shares by profile. The sample is stratified
across result ranks and profiles, not randomly sampled; those shares are not
unbiased estimates of total-query precision or recall. Use a separate frozen,
labeled calibration set for formal classifier metrics.