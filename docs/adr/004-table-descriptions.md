# 004: Describe each table with Gemini instead of indexing its rows

**Status:** accepted, 2026-11-24

## Context

Search finds tables far less often than text or figures (held-out answer@5: tables 0.53,
figures 0.71, text 0.83; see [retrieval experiments](../retrieval_experiments.md)). The
team's review of the misses found three causes:

- A table's cells rarely use the words of a question. Captions leave symbols and codes
  undefined (ψ for "CO2 mineralization potential", SSD for "steel slag powder"); the paper
  defines them elsewhere.
- Wide tables lose their right-hand columns: the reranker reads 512 tokens and cuts off the
  rest, and Docling's padded Markdown spends about a fifth of those tokens on spaces and
  `|---|` rows.
- One experiment (exp/approved-table-backfill) added a passage per table row. Near-identical
  rows of one table filled the top five results without adding information, and the
  extraction scores did not move (extraction reads only the Markdown, not those rows).

Gemini's figure readings, by contrast, raised figure retrieval markedly.

## Decision

- Gemini describes each table once, as it reads each figure. The `describe_table` prompt
  keeps `read_figure`'s rules (copy labels as printed, never guess) and changes only the
  objective. It reads the table's compact Markdown, the paper's title and section heading,
  the caption and header of the table an uncaptioned one continues across a page, and the
  paper's paragraphs that explain it: notes under the table, the paragraph before it,
  sentences citing its "Table N", and sentences defining its codes. It returns a summary,
  column meanings and units, samples, conditions, defined terms and search synonyms. An
  answer stating a number that no input prints is asked again once, naming those numbers.
  No table is ever left without a description, and no Gemini text that failed a check is
  ever stored (amended 2026-11-25, after the full run left 9 of 4,973 tables undescribed).
  When no attempt passes (unprinted numbers, malformed, too slow), Gemini's answers are
  dropped and the description holds only the paper's caption and headers, marked
  `"source": "paper"` (no `source` key means `"gemini"`, so code from before the field still
  reads those); indexing leaves a "paper" one out, so those tables are
  searched by their caption and rows alone. The numbers users act on always come from the
  paper's own rows, never from Gemini's wording. Only Gemini being unreachable leaves a
  table for the next run.
- Descriptions are stored in `parsed.json` under `table_descriptions`, each tied to its
  content block, the SHA-256 of that table's text and the prompt version. A description that no
  longer matches its table is rejected when written and skipped when indexed. Bumping the
  prompt version makes `describe-tables` describe every table again.
- Every table block with text gets a description, contents lists included.
  `describe-tables` (and the "Describe parsed tables" workflow) fills them in for papers
  parsed earlier, from their `parsed.json` alone: no PDF is downloaded or parsed again. Runs
  resume, stop at a budget or time limit, and never overwrite a `parsed.json` that changed
  during the run. `describe-tables --check` confirms that no table is left without one.
- A table chunk is its caption, its description, then its compact rows. Long tables split into
  row groups that repeat caption, description and header, so a table yields about as many
  chunks as before, not one per row. Each piece is kept within 384 reranker tokens, so no row
  is cut off. A "table" whose caption and header alone pass that budget is prose the parser
  misread (real ones take about a seventh of it); it becomes one passage cut to the budget,
  not a copy of that prose per row.
- Paper selection reads a table chunk without its description; the reranker and the answer
  model read the whole chunk (amended 2026-11-26). Descriptions repeat the same words
  ("chemical composition", "XRF", "CaO") across every paper's tables, so in embeddings
  and BM25 they pulled the top 5 papers toward the wrong papers' tables. Each chunk records
  its description lines, and embedding and keyword indexing skip them.
- The per-row passages are retired. Manifests that still hold them (`contextual_tables`) load
  without them, and any rewrite of such a manifest, including `describe-tables`, removes them.

## Consequences

- Rebuild the search index (`build-index --rebuild`) after a description run; `build-index`
  does not notice a rewritten `parsed.json` whose parse generation is unchanged.
- A reparse writes a new `parsed.json` without descriptions; rerun `describe-tables` afterwards.
- Descriptions cost one text-only Gemini call per table: an estimated $0.004 (about 2,300
  input and 600 output tokens at Flash prices), so roughly $40 for 10,000 tables. The default
  $20 cap per run means the corpus takes about two runs.
- Measured on the same 250 papers (2026-11-26), answer@10 / @20 hits over the 100 tuning and
  held-out questions: no descriptions 81 / 91, descriptions everywhere 83 / 90, descriptions
  for the reranker only 84 / 91. Descriptions everywhere dropped held-out figure questions
  from 1.00 to 0.71 at @20; reranker only keeps figures at the baseline on tuning. Held-out
  @20 is 1-2 questions lower with reranker only (0.81, against 0.88 and 0.84), within the
  noise of 32 questions.
