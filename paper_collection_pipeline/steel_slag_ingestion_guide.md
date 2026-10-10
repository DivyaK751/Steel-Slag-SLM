# Steel Slag Data Ingestion Pipeline — Comprehensive Agent Guide

> **Purpose of this document:** Give a coding agent with zero prior context everything it needs to understand, implement, and maintain the steel-slag paper ingestion pipeline. Read this in full before writing any code.

---

## 1. Company mission & big picture

A small company is building a **Small Language Model (SLM)** trained entirely on published scientific literature about steel slag used in cement and concrete. The work has three sequential parts:

1. **Find relevant studies** on steel slag in cement/concrete (← THIS PIPELINE).
2. **Extract structured data** from those studies into a tabular dataset (inputs: oxide chemistry, particle size, processing conditions; outputs: strength, durability, carbon uptake).
3. **Train the SLM** on that dataset, then validate its predictions against the founder's lab results for similar slags.

**This pipeline is Part 1 only.** Its deliverable is a curated set of relevant papers (metadata manifest + downloaded PDFs) that feeds into Part 2. Nothing about data extraction, table-filling, or model training belongs here.

---

## 2. Domain knowledge (what makes a paper "relevant")

You don't need to be a materials scientist, but you need to understand two distinctions that drive every filtering decision.

### 2.1 Binder vs. aggregate — the core relevance split

In concrete:
- A **binder** (also called a **supplementary cementitious material / SCM**) is a reactive fine powder that chemically reacts with water and glues everything together. Cement is the primary binder; an SCM partially replaces it.
- An **aggregate** is the inert sand, gravel, or crushed rock that the binder holds in place. It provides bulk and structure but does not react chemically.

**This company studies steel slag as a binder/SCM** — meaning it cares about the slag's oxide chemistry (CaO, SiO₂, etc.), how finely it's ground (particle size / D50), how it's activated, and how much compressive strength it contributes as a reactive powder.

A paper using steel slag **as aggregate** treats it as cheap rock. It typically won't report the chemistry, particle size distribution, or activation data the downstream table needs. **Aggregate-only papers are excluded.**

A paper using steel slag as **both binder and aggregate** (dual-use) is borderline. The default rule: if the paper's **primary use** is as a binder and it reports binder-relevant data, include it. If the primary use is aggregate and binder data is incidental, exclude. If genuinely co-equal, mark as `Unclear` for human review.

### 2.2 Steel slag vs. blast-furnace slag — the material split

"Slag" in the cement literature refers to two completely different families:

- **Steel slag:** by-product of *steel*-making. Includes EAF (electric arc furnace), BOF (basic oxygen furnace), ladle (ladle furnace), stainless-steel, and converter/LD slags. These are **less understood** and are the company's target.
- **GGBFS / GGBS (ground granulated blast-furnace slag):** by-product of *iron*-making in a blast furnace. This is a **mature, well-characterized SCM** — a solved problem. **Not the target.**

The workbook README explicitly states: *"Do not treat GGBFS as the target SCM/feedstock."*

### 2.3 What makes a paper relevant (the founder's definition)

A paper is relevant if it discusses steel slag in the context of:
- **Cementitious applications** (using slag as SCM in cement/concrete)
- **Hydration** (the chemical reaction of slag with water)
- **Hydraulic binders** (slag-based binders that set with water)
- **Carbonation** (CO₂ uptake by slag, relevant to carbon capture)
- **Chemical, mineralogical, or morphological characterization** (XRF/XRD/SEM analysis of slag composition and structure)

Papers with **incomplete datasets are still useful** — a characterization paper with oxide percentages but no strength data still fills the chemistry tab.

### 2.4 What is NOT relevant

- Steel slag used **solely as aggregate** (inert filler, not reactive binder)
- Steel slag in **asphalt** or road construction
- Steel slag for **soil stabilization**
- Studies focused on **GGBFS/GGBS** (blast-furnace slag)
- Papers on unrelated applications (e.g., steel slag for water treatment, heavy-metal adsorption) unless they also report binder-relevant characterization data

### 2.5 The critical "furnace" bug

The original notebook used `SKIP = ("furnace", "ggbfs", "ggbs")`. The bare word **"furnace" matches "Electric Arc Furnace" and "Basic Oxygen Furnace"** — the two highest-priority slag types. This silently drops the company's best papers. The correct exclusion terms are:
- `"blast furnace"` (the phrase, not the word)
- `GGBFS`
- `GGBS`
- `"ground granulated"`

Never exclude the bare word `furnace`.

---

## 3. The target table schema (what the pipeline ultimately feeds)

The downstream extraction step (Part 2) fills a workbook with 9 data tabs plus 3 reference tabs. Understanding the schema tells you what data makes a paper valuable — even though this pipeline doesn't fill the table, it must select papers that *can* fill it.

### 3.1 Tab overview

| Tab | Purpose | Key columns (what makes a paper high-value) |
|-----|---------|---------------------------------------------|
| 1 Scientific Literature | Paper metadata | Title, Authors, Year, DOI, Steel_Slag_Type, Study_Objective, Abstract_or_Key_Finding, Relevant_To_SCM |
| 2 Feedstock Chemistry | Oxide composition | **CaO%, SiO₂%, Al₂O₃%, Fe₂O₃%, MgO%**, SO₃%, Free_CaO%, LOI%, Analysis_Method |
| 3 Processing | How slag was processed | Processing_Method, Equipment, Temperature, Chemical_Additives, CO₂_Exposure |
| 4 Particle Size | Fineness of ground slag | **D10/D50/D90 (μm)**, Blaine (m²/kg), Grinding_Time, Equipment |
| 5 Activation | How slag was activated | **Activation_Type**, Activator_Dosage, Temperature, Liquid/Solid Ratio |
| 6 Concrete Performance | Strength results | **SCM_Replacement%, W/B Ratio, Compressive_Strength at 1/3/7/28/56/90 days (MPa)** |
| 7 Durability | Long-term performance | Expansion%, Sulfate/Chloride Resistance, Freeze-Thaw, ASR, Carbonation Resistance |
| 8 Carbon Uptake | CO₂ sequestration data | **CO₂_Uptake (kg/t or % by mass)**, Carbonation_Method, Pressure, Temperature |
| 9 Citations & Provenance | Audit trail | Fact_or_Data_Element, Reported_Value, Page/Table/Figure, Original_Source_Text, Confidence |

### 3.2 Column tiers (relevance proxy)

- **Tier 1 — highest-value columns** (what the SLM trains on): Oxide chemistry (CaO/SiO₂/Al₂O₃/Fe₂O₃/MgO), particle size (D50), activation type/dosage, compressive strength (especially 28d), CO₂ uptake. A paper that reports several of these is gold.
- **Tier 2 — qualifying context**: Processing method, durability results, slag type, study objective.
- **Tier 3 — bookkeeping** (pipeline-generated, not extracted): Source_ID, Material_ID, Experiment_ID, Data_Extraction_Status, Confidence, provenance fields.

### 3.3 Controlled vocabulary (constrain LLM outputs to these)

| Field | Allowed values |
|-------|---------------|
| Steel_Slag_Type | EAF, BOF, Ladle, Stainless Steel, Electric Arc Furnace Dust-derived, Other Steel Slag |
| Publication_Type | Journal Article, Conference Paper, Thesis/Dissertation, Patent, Government Report, Technical Report, Dataset, Standard, Other |
| Full_Text_Access | Open, Abstract Only, Restricted, Unknown |
| Relevant_To_SCM | Yes, No, Unclear |
| Processing_Method | Grinding, Milling, Screening, Sieving, Drying, Magnetic Separation, Classification, Carbonation, Other |
| Activation_Type | None, Mechanical, Chemical, Thermal, Carbonation, Combined |
| Analysis_Method | XRF, ICP-OES, ICP-MS, XRD, SEM-EDS, Other |
| Confidence | High, Medium, Low |

### 3.4 Key design principles from the workbook

- **Source_ID links records across tabs.** One paper → one Source_ID → potentially many Material_IDs and Experiment_IDs.
- **Do not infer values.** Use blank/NA where a paper doesn't report a field.
- **Record units exactly.** Don't normalize silently.
- **Preserve source excerpts** so every extracted fact is traceable to literature.

---

## 4. Pipeline architecture (4 stages)

### The governing principle

> **OpenAlex decides the *material*; the LLM decides the *application*.**

- "Is this about a steel slag (EAF/BOF/ladle/stainless), and not blast-furnace slag?" → keyword-shaped → **OpenAlex**.
- "Is the slag used as a reactive binder/SCM, or as inert aggregate / in asphalt / for soil?" → semantic → **LLM**.

The LLM handles *only* the judgment that keywords can't make. Everything else is handled by OpenAlex queries and deterministic code.

### Stage 1 — Broad retrieval from OpenAlex (maximize recall)

**Goal:** Get every paper that could plausibly be about steel slag as a cementitious material, using structured API filters. Favor recall — better to include a questionable paper than miss a good one.

**Positive search terms** (OR'd, searched over title + abstract):
```
steel slag, steelmaking slag, BOF slag, basic oxygen furnace slag,
EAF slag, electric arc furnace slag, converter slag, LD slag,
ladle slag, ladle furnace slag, stainless steel slag, steel furnace slag
```

**Exclusion terms** (material-level only):
```
"blast furnace", GGBFS, GGBS, "ground granulated"
```

**What is deliberately NOT excluded at this stage:** `aggregate`, `asphalt`, `soil`. These are *application* words — the LLM's job. Excluding them server-side would wrongly drop dual-use papers and papers that merely mention these in passing.

**API specifics:**
- Endpoint: `https://api.openalex.org/works`
- Use `title_and_abstract.search` for the positive terms (not just `search`, which ranks but doesn't filter).
- Use `cursor=*` pagination, following `meta.next_cursor` until `null`. `per_page` max is 200.
- Use `select=` to pull only needed fields: `id, doi, title, publication_year, type, language, authorships, primary_location, open_access, abstract_inverted_index, topics, cited_by_count`.
- **Do NOT use `has_content.pdf:true`** — this discards abstract-only papers that are perfectly relevant. PDF resolution happens later, only for accepted papers.
- Filter `language:en` to restrict to English.
- Publication type: **unfiltered** (the controlled vocab allows journal articles, theses, reports, patents — don't lose them).
- Year floor: configurable, default none (cast wide).
- Authenticate with `api_key` parameter if available (higher rate limits).
- Respect rate limits: OpenAlex allows ~10 req/s for authenticated, ~1 req/s for unauthenticated. Implement backoff.
- **Verify the exact boolean/negation syntax against the live OpenAlex API docs before implementing.** The filter grammar has changed over time; don't hard-code an operator from memory. Check: https://docs.openalex.org

**Output:** `candidates_raw.csv` — one row per work, all selected fields, abstract still in inverted-index form.

### Stage 2 — Deterministic assembly & hygiene (plumbing, not filtering)

**Goal:** Transform the raw API output into clean, deduplicated records with readable abstracts. This is NOT a relevance filter — it's a format-conversion step that exists because of three OpenAlex response-format limitations:

1. **Abstract reconstruction.** OpenAlex does not return readable abstract text. It returns an `abstract_inverted_index`: a JSON object mapping each word to a list of positions in the abstract (e.g., `{"Steel": [0], "slag": [1, 15], "is": [2], ...}`). You must invert this back to ordered text. Algorithm: create an array sized to max position + 1, place each word at its positions, join with spaces.

2. **Deduplication.** Multiple synonym queries + full pagination can return the same paper multiple times. Dedup by OpenAlex ID first, then by DOI for cross-ID duplicates.

3. **No-abstract routing.** Some records have no `abstract_inverted_index` (null or empty). The LLM can't judge a blank abstract. Route these to a side-list (`no_abstract.csv`) rather than silently dropping them — they represent invisible recall loss otherwise. The founder can eyeball these later.

**Output:** `candidates_clean.csv` (records with reconstructed abstracts, deduped) + `no_abstract.csv` (side-list).

### Stage 3 — LLM relevance classifier (the semantic judgment)

**Goal:** For each candidate, determine whether the paper studies steel slag as a binder/SCM (relevant) or as aggregate/asphalt/soil/other (not relevant).

**Input:** title + reconstructed abstract from `candidates_clean.csv`.

**Prompt design principles:**
- **Lean prompt.** State the founder's definition plainly. No elaborate chain-of-thought scaffolding.
- **No few-shot examples by default.** The calibration set (Stage 0) measures the classifier; examples only get added later *if* measurement proves they help. The user is risk-averse about prompt complexity.
- **Three outcomes, not two.** `Yes` / `No` / `Unclear`. The LLM must never silently decide borderline cases. `Unclear` papers go to a review list.
- **Required supporting excerpt.** The model must quote a verbatim phrase from the abstract that supports its decision. This is a cheap anti-hallucination guardrail and maps directly to the provenance tab later.
- **Constrained output.** Use structured output (JSON mode or function calling) to enforce the schema.

**Output schema per paper:**
```json
{
  "decision": "Yes | No | Unclear",
  "confidence": "High | Medium | Low",
  "primary_application": "binder/SCM | aggregate | asphalt | soil | characterization | carbonation | other",
  "slag_type": "EAF | BOF | Ladle | Stainless Steel | Other Steel Slag | Not specified",
  "rationale": "One sentence explaining the decision.",
  "supporting_excerpt": "Verbatim quote from the abstract."
}
```

**Dual-use rule (encoded in prompt):** If the paper uses slag primarily as a binder and reports binder-relevant data → `Yes`. If primarily as aggregate → `No`. If genuinely co-equal or ambiguous → `Unclear`.

**Cost management:**
- Process in batches, cache results keyed by OpenAlex ID so re-runs don't re-pay.
- Use the cheapest model that achieves acceptable precision/recall on the calibration set (start with a mid-tier model; upgrade only if measurement demands it).
- Log token usage per run.

**Output:** `classified.csv` — all columns from `candidates_clean.csv` plus the verdict columns.

### Stage 4 — Produce the deliverable bundle

**Goal:** For accepted papers (`decision == Yes`), resolve and download open-access PDFs. Package the final manifest.

**PDF resolution:**
- Use the `open_access.oa_url` and/or `primary_location.pdf_url` fields from OpenAlex metadata.
- For papers with an OA URL, download the PDF to `pdfs/{openalex_id}.pdf`.
- For papers without OA access (paywalled), write the row to `needs_manual_access.csv` with DOI and any available URL so the founder can resolve access manually.
- Verify downloaded files are actually PDFs (check magic bytes / Content-Type), not HTML login pages.

**Output (the deliverable):**
- `relevant_papers.csv` — the manifest: metadata + decision + rationale + excerpt for all `Yes` papers.
- `pdfs/` — downloaded open-access PDFs, named by OpenAlex ID.
- `needs_manual_access.csv` — paywalled papers the founder must access manually.
- `unclear_papers.csv` — `Unclear` decisions for optional founder review.

---

## 5. Stage 0 — Calibration set (separate notebook)

**Purpose:** A frozen, labeled set of ~40–60 papers used to *measure* pipeline precision and recall. It is not part of the live pipeline; it is a test harness.

**Composition — deliberately include hard cases:**
- ~15–20 clear positives (binder/SCM studies with oxide data, strength results, characterization)
- ~15–20 clear negatives (aggregate, asphalt, soil, GGBFS, water treatment)
- ~10 borderline cases (dual-use, characterization-only, comparison studies that mention GGBFS)
- Include the specific example the founder flagged: *"Accelerated carbonation and performance of concrete made with steel slag as binding materials and aggregates"* → labeled `No` (aggregate per founder's decision)

**Labeling process:** The agent can draft proposed labels with rationales from the definition. The founder confirms/corrects (~30–60 min). Labels are: `Yes`, `No`, `Unclear`.

**Usage:** Run the Stage 3 classifier over this set. Report precision, recall, F1 for the `Yes` class. Any prompt or keyword change must be validated here before deploying to the full pipeline.

**This lives in its own notebook (`00_calibration.ipynb`) and is run independently.**

---

## 6. Code architecture

```
steel_slag_ingestion/
├── config.py                  # ALL tunable parameters in one place
├── openalex_client.py         # Stage 1: query building, pagination, field selection
├── classify.py                # Stage 3: prompt construction, LLM call, output parsing
├── io_utils.py                # Stages 2 & 4: abstract reconstruction, dedup, CSV I/O,
│                              #   PDF download, validation, logging
├── 00_calibration.ipynb       # Stage 0: build/freeze gold set, run classifier, report P/R/F1
├── 01_pipeline.ipynb          # Stages 1→4: each cell reads a CSV, writes the next CSV
└── outputs/
    ├── candidates_raw.csv     # Stage 1 output
    ├── candidates_clean.csv   # Stage 2 output
    ├── no_abstract.csv        # Stage 2 side-list
    ├── classified.csv         # Stage 3 output (all decisions)
    ├── relevant_papers.csv    # Stage 4 output (Yes only) ← DELIVERABLE
    ├── unclear_papers.csv     # Stage 4 output (Unclear only)
    ├── needs_manual_access.csv# Stage 4 output (paywalled Yes papers)
    └── pdfs/                  # Stage 4 output (downloaded PDFs) ← DELIVERABLE
```

### 6.1 `config.py` — all knobs in one file

```python
# --- Stage 1: OpenAlex retrieval ---
POSITIVE_SEARCH_TERMS = [
    "steel slag", "steelmaking slag",
    "BOF slag", "basic oxygen furnace slag",
    "EAF slag", "electric arc furnace slag",
    "converter slag", "LD slag",
    "ladle slag", "ladle furnace slag",
    "stainless steel slag", "steel furnace slag",
]

EXCLUSION_TERMS = [
    "blast furnace",   # phrase, NOT bare "furnace"
    "GGBFS",
    "GGBS",
    "ground granulated",
]

LANGUAGE_FILTER = "en"
YEAR_FLOOR = None  # e.g., 2000; None = no floor
OPENALEX_PER_PAGE = 200
OPENALEX_API_KEY = None  # set from env or secrets

SELECT_FIELDS = [
    "id", "doi", "title", "publication_year", "type", "language",
    "authorships", "primary_location", "open_access",
    "abstract_inverted_index", "topics", "cited_by_count",
]

# --- Stage 3: LLM classifier ---
LLM_MODEL = "..."  # model identifier
LLM_TEMPERATURE = 0.0  # deterministic for reproducibility
CONFIDENCE_THRESHOLD = None  # optional: auto-accept High, review Medium/Low
CACHE_LLM_RESULTS = True

# --- Stage 4: PDF download ---
PDF_DOWNLOAD_TIMEOUT = 30  # seconds
VERIFY_PDF_MAGIC_BYTES = True

# --- Paths ---
OUTPUT_DIR = "outputs"
```

### 6.2 `openalex_client.py` — retrieval logic

Key functions:
- `build_filter_string(config)` → constructs the OpenAlex filter parameter. **IMPORTANT:** verify the exact boolean/negation syntax against the live API docs before implementing. The filter grammar uses pipes for OR, commas for AND, and `!` for negation, but this has changed over time.
- `fetch_all_candidates(config)` → cursor-paginates through the full result set. Returns list of raw work records. Implements rate-limit backoff.
- `save_candidates_raw(records, path)` → writes `candidates_raw.csv`.

### 6.3 `io_utils.py` — data plumbing

Key functions:
- `reconstruct_abstract(inverted_index: dict) -> str` → inverts the position map to ordered text. Handle edge cases: null input, empty dict, missing positions.
- `dedup_records(records) -> records` → dedup by OpenAlex ID, then DOI.
- `route_no_abstract(records) -> (with_abstract, without_abstract)` → split.
- `download_pdf(url, path, timeout)` → download with verification (check Content-Type and magic bytes `%PDF`).
- `save_csv(records, path)` / `load_csv(path)` → standard I/O with consistent encoding.

### 6.4 `classify.py` — LLM classifier

Key functions:
- `build_prompt(title, abstract, config) -> str` → constructs the classification prompt. The prompt must include:
  - The founder's relevance definition (Section 2.3 of this document)
  - The material/application boundary rule
  - The dual-use rule
  - The instruction to abstain (`Unclear`) when the abstract doesn't give enough signal
  - The requirement for a verbatim supporting excerpt
  - The output schema
- `classify_paper(title, abstract, config) -> dict` → calls the LLM, parses structured output, validates against schema.
- `classify_batch(records, config) -> records` → processes a list with caching and logging.

---

## 7. Important implementation notes

### 7.1 OpenAlex abstract reconstruction algorithm

```python
def reconstruct_abstract(inverted_index: dict) -> str | None:
    """
    OpenAlex stores abstracts as {word: [position, ...], ...}.
    Reconstruct by placing each word at its positions in an array.
    """
    if not inverted_index:
        return None
    # Find the maximum position to size the array
    max_pos = max(pos for positions in inverted_index.values() for pos in positions)
    abstract_words = [""] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            abstract_words[pos] = word
    return " ".join(abstract_words)
```

### 7.2 OpenAlex cursor pagination pattern

```python
def fetch_all_candidates(config):
    all_works = []
    cursor = "*"
    while cursor:
        params = {
            "filter": build_filter_string(config),
            "select": ",".join(config.SELECT_FIELDS),
            "per_page": config.OPENALEX_PER_PAGE,
            "cursor": cursor,
        }
        if config.OPENALEX_API_KEY:
            params["api_key"] = config.OPENALEX_API_KEY
        response = requests.get("https://api.openalex.org/works", params=params)
        response.raise_for_status()
        data = response.json()
        all_works.extend(data["results"])
        cursor = data["meta"].get("next_cursor")
        # Rate limiting
        time.sleep(0.1)  # adjust based on auth status
    return all_works
```

### 7.3 PDF verification

Downloaded "PDFs" are sometimes HTML login/paywall pages. Always verify:
```python
def is_valid_pdf(content: bytes) -> bool:
    return content[:5] == b"%PDF-"
```

### 7.4 What the original notebook got wrong (don't repeat these)

| Bug | Impact | Fix |
|-----|--------|-----|
| `SKIP = ("furnace", ...)` | Drops EAF and BOF papers | Exclude `"blast furnace"`, not `"furnace"` |
| Title-only search | Misses papers where "steel slag" is in abstract but not title | Use `title_and_abstract.search` |
| `per_page=200`, no pagination | Only sees first 200 results | Cursor-paginate the full set |
| `has_content.pdf:true` | Drops paywalled papers that are perfectly relevant | Remove; resolve PDFs only for accepted papers |
| Downloads PDFs before filtering | Wastes bandwidth on irrelevant papers | Download only after LLM acceptance |
| No dedup | Same paper from multiple queries | Dedup by OpenAlex ID + DOI |
| No structured exclusion logic | Aggregate/asphalt/soil papers pass through | LLM classifier with structured output |

### 7.5 Idempotency and caching

- Every stage reads a file and writes a file. Re-running a stage with the same input produces the same output.
- LLM results should be cached by OpenAlex ID so re-runs don't re-pay for already-classified papers.
- PDF downloads should skip files that already exist on disk.

### 7.6 Logging

Log the following per run:
- Stage 1: total candidates retrieved, query count, pagination pages
- Stage 2: duplicates removed, records with/without abstracts
- Stage 3: decision distribution (Yes/No/Unclear), token usage, cost estimate
- Stage 4: PDFs downloaded, PDFs failed/invalid, paywalled count

---

## 8. The deliverable (what ships this week)

The pipeline's output is:

1. **`relevant_papers.csv`** — a manifest of all accepted papers with metadata, the LLM's decision rationale, and the supporting excerpt from each abstract.
2. **`pdfs/`** — the downloaded open-access PDFs for accepted papers.
3. **`needs_manual_access.csv`** — paywalled papers that passed the relevance filter but need manual access resolution.
4. **`unclear_papers.csv`** — borderline papers for optional founder review.

This bundle is the input to Part 2 (RAG + data extraction into the workbook). Nothing about filling the table, training models, or building review loops belongs in this pipeline.

---

## 9. Environment notes

### 9.1 The original notebook ran in Google Colab

The original exploration notebook (`OpenAlexAPI_test.ipynb`) used:
- `google.colab.auth` and `google.colab.userdata` for authentication
- Google Drive API to upload PDFs directly to a shared drive folder
- `OPENALEX_API_KEY` stored in Colab secrets
- Target folder: the team's `Documents/Extracted Research Papers` shared-drive folder; configure its ID locally with `STEEL_SLAG_DRIVE_FOLDER_ID` rather than committing it to source.

The new pipeline should be environment-agnostic (works locally, in Colab, or in any Python environment). Google Drive upload can be a separate optional step, not baked into the core pipeline.

### 9.2 Dependencies

Core: `requests`, `pandas` (or plain csv), `json`, `time`
LLM: whatever SDK the chosen model requires (e.g., `openai`, `anthropic`)
Optional: `openpyxl` (if reading the workbook programmatically), `google-api-python-client` (if uploading to Drive)

---

## 10. Open configuration choices (defaults assumed)

These have sensible defaults encoded in the architecture but are configurable:

1. **Dual-use papers:** Default = LLM judges primary application. Toggle: could hard-exclude any paper mentioning "aggregate" at the OpenAlex level (higher precision, some recall loss).
2. **Blast-furnace comparison papers:** Default = server-side exclusion of "blast furnace" phrase. Risk: drops a steel-slag paper that merely *compares against* GGBFS. Toggle: could move this to the LLM.
3. **Recall vs. precision bias:** Default = favor recall (keep Unclear papers, don't over-filter). The founder confirmed LLM filtering is approved.
4. **Year floor:** Default = none. Could set to e.g., 2000 if older literature is less useful.
5. **LLM model choice:** Start with a capable mid-tier model. Upgrade/downgrade based on calibration results.

---

## 11. Glossary

| Term | Meaning |
|------|---------|
| SCM | Supplementary Cementitious Material — a powder that partially replaces cement as a binder |
| EAF | Electric Arc Furnace — a type of steel-making furnace |
| BOF | Basic Oxygen Furnace — a type of steel-making furnace |
| LD slag | Linz-Donawitz slag — another name for BOF slag |
| GGBFS / GGBS | Ground Granulated Blast-Furnace Slag — NOT the target material |
| D50 | Median particle diameter in micrometers |
| Blaine | Specific surface area (m²/kg) — a measure of powder fineness |
| XRF | X-ray Fluorescence — method for measuring oxide composition |
| XRD | X-ray Diffraction — method for identifying mineral phases |
| SEM-EDS | Scanning Electron Microscopy with Energy-Dispersive Spectroscopy |
| LOI | Loss on Ignition — weight lost when heated, indicates volatile content |
| W/B ratio | Water-to-binder ratio in a concrete mix |
| Compressive strength (MPa) | How much load a concrete sample withstands before failing, in megapascals |
| OA | Open Access — freely downloadable without a journal subscription |
