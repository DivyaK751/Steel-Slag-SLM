# Plan: Similar Slags and Lookup tools

Two tools that answer "my slag" questions from the BigQuery extraction tables.

- **Tool 1 – Find similar slags:** given the user's slag profile, return the 5 closest slags from the literature.
- **Tool 2 – Lookup:** given those slags, fetch what was measured on them (processing, activation, strength, durability, carbon uptake), with citations.

Our code runs both tools before the answer model; the model never writes SQL.

Similarity rules come from Keith (CNS):
- Compare slags of the **same type** (EAF with EAF).
- A slag is similar if its oxides fall **within the type's typical ranges** (see the appendix) and its **major mineral phases** match. No need to be narrower than that.
- 3–4 major oxides are enough if they add up to **~90% of the total**.
- Flag **Cr₂O₃ or V above 0.5%** (leaching risk; also worth extracting at > $10k/t).
- Flag **any oxide outside its type's typical range**.

Tick each step off as it is done.

---

## Phase 0 – Reference data (hand-written, once)

- [x] **0.1 Typical ranges per slag type.** Keith's tables (appendix) are in `configs/slag_reference.yaml`: min, max and typical average (where given) of each oxide, plus the major mineral phases, for EAF, BOF and LMF. Load them with `config.load_slag_reference()`.
  - Our data calls LMF **"Ladle"**, so store it under that name.
  - Example: `cao_pct: {min: 22, max: 60, avg_min: 35, avg_max: 40}`

- [x] **0.2 Mineral phase dictionary.** Papers name the same phase in different ways; `phase_names` in the same file maps every name to one standard name.

  | Standard name | Other names to match |
  |---|---|
  | Dicalcium silicate | larnite, belite, C2S, β-C2S, γ-C2S, Ca2SiO4 |
  | Alite | C3S, Ca3SiO5, tricalcium silicate |
  | Wüstite | wustite, FeO phase, RO phase, magnesiowüstite |
  | Brownmillerite | C4AF, Ca2(Al,Fe)2O5 |
  | Srebrodolskite | C2F, Ca2Fe2O5 |
  | Free lime | f-CaO, free CaO |
  | Mayenite | C12A7, Ca12Al14O33 |
  | Tricalcium aluminate | C3A, Ca3Al2O6 |
  | Merwinite, Gehlenite, Åkermanite, Periclase, Spinel, Portlandite, Oldhamite, Fluorite | their formulas and common spellings |

---

## Phase 1 – Slag catalogue (one clean row per slag)

A command (e.g. `build-slag-catalogue`) reads `sheet_02_feedstock_chemistry`, cleans every row, and saves the result to a new BigQuery table `steel_slag_extraction.slag_catalogue`. The app only ever reads this table.

- [x] **1.1 Load the chemistry rows.** `SELECT * FROM sheet_02_feedstock_chemistry` (95 rows today, ~1,500 at 950 papers; small enough to load in one go).

- [x] **1.2 Key and type.** Each slag is identified by `source_id + material_id + experiment_id` and keeps its `steel_slag_type`.
  - Always use the pair `source_id + material_id`, never `material_id` alone: IDs restart at `MAT-0001` in every paper.

- [x] **1.3 Parse oxide values.** Values are stored as text.

  | Stored value | Becomes | Why |
  |---|---|---|
  | `"42.1"`, `"4.88 wt%"` | 42.1, 4.88 | plain number; units dropped |
  | `"41.2 ± 5.0"` | 41.2 | mean ± spread: the mean |
  | `"442 mg/kg"` | 0.0442 | mg/kg converted to wt% |
  | `"0.42-0.705"` | 0.56 | narrow range (≤ 2 points wide): midpoint |
  | `"20-50"` | empty | wide range: a midpoint would be made up |
  | `">5"`, `"NA"`, blank | empty | not a usable number |
  | `"0.0"` | 0 | a real zero, not missing |

- [x] **1.4 Read extra oxides from `trace_elements`.** Split the text into `name: value` pairs and keep only the oxides we need.
  - Example: `"Fe: 1.2%; FeO: 0.0%; S: 0.3%; Cr2O3: 0.0%; Zn: 0.003%"` → keep `FeO = 0`, `Cr2O3 = 0`; skip Fe, S and Zn.
  - Skip bare elements, **except Cr and V** (converted to Cr₂O₃ and V₂O₅ for the heavy-metal flag) and **total iron** (`T.Fe`, used in 1.5).
  - Any main oxide (e.g. `MnO: 3.8 wt%`) fills its column when the column is empty.
  - Apply the range rule from 1.3: `"FeOx: 30-50%"` is skipped.
  - There are no Cr₂O₃ or V columns in the table, so `trace_elements` and `notes` are the only source for them.

- [x] **1.5 Total iron.** Combine iron into one number, because steel slag holds iron in two forms and Keith's ranges treat them as one.
  - Rule: `total_fe2o3 = Fe2O3 + FeO × 1.11`
  - Example: Fe₂O₃ 5 + FeO 20 → 5 + 22.2 = **27.2**
  - Only Fe₂O₃ reported → use it as is.
  - Already combined (`FeO + Fe2O3`, `FeOx`, `T-Fe (FeO, Fe2O3)`) → use it as is.
  - Only total iron as the element (`T.Fe: 21.1%`) → × 1.43 (slightly high: it includes metallic iron).
  - **Watch out:** if the notes say Fe₂O₃ is *total* iron ("T.Fe as Fe2O3"), don't add FeO; that would count iron twice.

- [x] **1.6 Mineral phases.** Search `notes` (and `trace_elements`) with the phase dictionary from 0.2; store the standard names found, in order.
  - Example: `"Mineral components include C2S, C3S, C4AF, RO phase and f-CaO"` → `["Dicalcium silicate", "Alite", "Brownmillerite", "Wüstite", "Free lime"]`
  - First, count how many rows end up with at least one phase; this decides open decision B.

- [x] **1.7 Coverage.** Add up the major oxides the slag reports, and divide by `Total_Oxides_pct` if the paper gives one, otherwise by 100.
  - Example: CaO 38 + total Fe₂O₃ 27 + SiO₂ 16 + MgO 7 + Al₂O₃ 4 = **92%** → comparable.
  - Example: only CaO 38 + SiO₂ 16 = **54%** → not comparable (too incomplete to match on).
  - `is_comparable = coverage ≥ 90%`

- [x] **1.8 Flags.**
  - Cr₂O₃ or V₂O₅ above 0.5% → `"Cr2O3 0.8% > 0.5%: leaching risk / extraction value"`
  - Oxide outside its type's range → `"MgO 15% above typical BOF range 3–10%"`

- [x] **1.9 Save the catalogue** (`uv run build-slag-catalogue`) to `steel_slag_extraction.slag_catalogue`, replacing the old one.

  | Column | Example |
  |---|---|
  | `source_id`, `material_id`, `experiment_id` | W2014482815, MAT-0001, EXP-0002 |
  | `steel_slag_type` | BOF |
  | `cao_pct`, `sio2_pct`, `al2o3_pct`, `mgo_pct`, `mno_pct`, ... | cleaned numbers or empty |
  | `total_fe2o3_pct` | 27.2 |
  | `cr2o3_pct`, `v2o5_pct` | 0.8, empty |
  | `feo_pct`, `free_cao_pct`, `free_mgo_pct` | 4.88, 4.31, empty |
  | `mineral_phases` | ["Dicalcium silicate", "Wüstite", "Srebrodolskite"] |
  | `oxide_coverage_pct`, `is_comparable` | 92, true |
  | `flags` | ["Cr2O3 0.8% > 0.5%"] |
  | `notes` | original notes text, passed to the answer model later |
  | `built_at` | when the row was built |

- [x] **1.10 Rebuild automatically.** Run the catalogue build at the end of `promote-extraction`, so it never falls behind the extraction tables.

- [ ] **1.11 Check by eye.** Open the table in the BigQuery console and compare 10 rows against their papers.

---

## Phase 2 – Tool 1: `find_similar_slags(profile)`

Code: `rag/similar_slags.py`. Matching rules are in `similar_slags:` of `configs/base.yaml`.

**Input:** the user's slag profile.
Example: `{type: "EAF", cao_pct: 38, fe2o3_pct: 25, feo_pct: 3, sio2_pct: 16, mgo_pct: 8, al2o3_pct: 6, mineral_phases: ["Wüstite", "Dicalcium silicate", "Brownmillerite"], cooling: "air-cooled"}`

- [x] **2.1 Load the catalogue** once when the app starts and keep it in memory.

- [x] **2.2 Clean the profile** with the same rules as Phase 1 (total iron, phase names), so both sides are on the same basis.
  - Example: Fe₂O₃ 25 + FeO 3 → total Fe₂O₃ 28.3

- [x] **2.3 Check the user's own slag** and return the flags with the result (this also helps answer "what is my slag made of").
  - Heavy metals: `"Cr2O3 1.2% > 0.5%"`
  - Thin data: `"Your oxides cover 60%; matches will be less reliable"`

- [x] **2.4 Same-type shortlist.** Keep catalogue slags with the same type and `is_comparable = true`.

- [x] **2.5 Range check.** Count the shortlist slag's oxides outside the type's typical ranges. Per Keith, they stay in with their flags and rank lower; only cross-type slags are dropped for being out of range.
  - Example (EAF): CaO 41 ✔ (22–60), total Fe₂O₃ 30 ✔ (10–40), SiO₂ 14 ✔ (6–34) → keep.

- [x] **2.6 Cross-type exception.** A slag of another type (including "Other Steel Slag") is added only if its oxides fall within the user's type ranges **and** its top 3 mineral phases match the user's. Needs phase data on both sides. See open decisions A and C.

- [x] **2.7 Order the shortlist.**
  1. More shared mineral phases first.
  2. Ties broken by oxide distance: for each shared oxide, the difference divided by how much that oxide usually varies within the type; then combined into one score (smaller = closer).

- [x] **2.8 Return the top 5.** Each with: key, type, oxides, phases, a match label, its flags and its notes.
  - Example match label: `"Same type (EAF), all oxides in range, 3 shared phases"`

- [x] **2.9 Nothing found.** Return an empty list and the reason, e.g. `"No comparable EAF slags with phase data"`. The question then falls back to paper search.

- [x] **2.10 Test with Keith's typical slags.** Use the midpoints of his EAF, BOF and LMF ranges as three test profiles and check that the top 5 make sense.

---

## Phase 3 – Tool 2: `lookup(similar_slags)`

Code: `rag/slag_lookup.py` (`lookup_slags`, `render_evidence`); reads in `data/extraction_lookup.py`.
Each row keeps its workbook sheet name, original column headers and values, plus the page of each value.

**Input:** the 5 slag keys from Tool 1.

- [x] **3.1 Fetch the slags' rows from sheets 03–08** (processing, particle size, activation, concrete performance, durability, carbon uptake): one fixed, parameterised query per sheet, filtered on the `(source_id, material_id)` pairs, and on `experiment_id` too when that sheet has rows with the slag's experiment (otherwise all of the material's rows: papers often number chemistry and tests as different experiments). All columns are fetched; 5 slags give roughly 50–100 rows.

- [x] **3.2 Fetch citations.** Title, authors, year and DOI from `sheet_01`; page, table or figure for each value from `sheet_09`.

- [x] **3.3 Trim.** Drop empty and `NA` cells, and bookkeeping columns (`extracted_at`, `extraction_model`). Most cells in any row are blank, and the answer model has a small context window.

- [x] **3.4 Keep mix conditions with performance numbers.** A strength value means little without its replacement %, water/binder ratio and activation. Each row is kept whole, so its strength values sit next to its replacement %, w/b ratio and mix ID.
  - Example: `"20% replacement, w/b 0.5, no activation: 28-day strength 41 MPa"`

- [x] **3.5 Summarise per column:** range, number of studies, papers with pages.
  - Example: `"28-day strength at 20–30% replacement: 32–48 MPa, 3 studies [Smith 2021 p.6; Lee 2019 Table 4; Rossi 2022 Fig. 3]"`
  - Values stored as ranges keep their range.

- [x] **3.6 Package the evidence** for the answer model, one section per topic: Chemistry and mineralogy, Processing, Activation, Strength, Durability, Carbon uptake. Every item carries its citation.
  - Empty sections say so: `"No carbon-uptake data for these slags."` This stops the model inventing values.

- [x] **3.7 Test end to end:** profile → Tool 1 → Tool 2 → evidence text, for the three test profiles from 2.10.

---

## Open decisions (confirm before Phase 2)

- [x] **A. Other types.** *Keith: everything not EAF/BOF/LMF (stainless, dust-derived, unlabelled) is "Other" and stays in the data; chemistry and phases bridge the gap. Built as: one "Other" group; Keith: a slag of another group needs all oxides within the user's type ranges and ≥ 3 shared phases, and always ranks below same-group slags. Built (`max_oxides_out_of_range_cross_type: 0`).* Should "Other Steel Slag" (26 of 95 rows today) and "Stainless Steel" go through the cross-type exception (2.6), or be left out?
- [x] **B. No phase data.** If a slag has no phases recorded, does it still match on oxides alone, ranked below slags with phases? *Keith: yes. Built.*
- [ ] **C. "Top 3 phases".** Papers rarely give phase amounts. Use the first 3 listed, or "at least 3 phases in common"? *Built as: at least 3 in common (`min_shared_phases_cross_type`).*
- [ ] **D. Small data.** With 50 papers, EAF has 14 rows and Ladle 13. Build now and let it improve as more papers are extracted? *Built as: yes.*
- [x] **E. Strict ranges.** If the range check (2.5) leaves too few slags, allow one oxide slightly outside its range? *Keith: flag every out-of-range value on published slags (not the user's), but keep them in matching. Built: same-group slags are never dropped; fewer out-of-range oxides ranks higher among slags sharing the same number of phases.*

---

## Appendix – Keith's typical ranges (wt%)

**EAF** (scrap melting, oxidizing)

| Oxide | Range |
|---|---|
| CaO | 22–60 (avg. 35–40) |
| FeO / Fe₂O₃ | 10–40 (avg. 25–30) |
| SiO₂ | 6–34 (avg. 15) |
| MgO | 3–13 |
| Al₂O₃ | 3–14 |
| MnO | 2–8 |
| Cr₂O₃, P₂O₅ | trace–3 |

Phases: Wüstite and magnesiowüstite; larnite/belite; brownmillerite; gehlenite and åkermanite; spinel; merwinite and periclase.

**BOF** (hot metal refining, strongly oxidizing)

| Oxide | Range |
|---|---|
| CaO | 40–60 |
| FeO / Fe₂O₃ | 15–35 |
| SiO₂ | 8–20 |
| MgO | 3–10 |
| MnO | 2–8 |
| Al₂O₃ | 1–5 |
| P₂O₅ | 1–3 |

Phases: Larnite; alite; wüstite; srebrodolskite and brownmillerite; free lime and portlandite; periclase.

**LMF / Ladle** (secondary refining, deoxidation and desulfurization)

| Oxide | Range |
|---|---|
| CaO | 45–60 |
| Al₂O₃ | 15–35 (Al-killed) |
| SiO₂ | 3–25 (higher in Si-killed) |
| MgO | 5–12 |
| FeO / Fe₂O₃ | < 2–3 |
| MnO | < 1–2 |
| CaF₂ / S | 1–5 |

Phases: Mayenite; tricalcium aluminate; β- and γ-dicalcium silicate; periclase; merwinite and gehlenite; oldhamite and fluorite.
