# 004: Read questions with a small model, route them with code

**Status:** proposed, 2026-11-23

## Context

The assistant answers from three tools: the extraction tables (lookup), the slag catalogue
(similar slags) and the paper search index. Something has to decide, for each message, which
tools to run and with what input, and then hand their results to the fine-tuned answer model.
Small models are unreliable at planning tool calls, and every answer must be traceable to the
data it came from.

## Decision

- **A small open model reads; code decides.** The reader model (Qwen2.5-3B, served locally by
  Ollama) only fills a `QuestionForm`: message type, intents, the extraction-table columns asked
  for, filters on columns, oxides stated in the message, whether it is about the user's slag,
  missing details and a stand-alone search query. Fixed rules in `agents/router.py` turn the
  form into tool calls, so the same form always gives the same calls.
- **Columns come from the sheet definitions.** `form_columns()` lists every data column of
  sheets 1 to 8 from `data/extraction_tables.py`; a new column there can be asked about with no
  other change. The reader's output is forced to a JSON schema that lists only those columns and
  the catalogue's oxides, so it cannot invent a column.
- **Routing rules:**
  - chat or off-topic: no tools; the answer model replies briefly.
  - about the user's slag, or a composition typed in the message: similar slags, then lookup on
    those slags. Typed oxides override the stored profile for that message.
  - asks for values: lookup on the catalogue slags chosen by the slag-type and chemistry
    filters, keeping only rows that meet the result filters and report the asked-for columns.
  - asks why or how, or none of the above: paper search with the stand-alone query.
  - the tables return nothing: paper search, with a note saying so.
- **Evidence is one cited text** (`agents/evidence_package.py`): notes to keep in mind, how well
  the similar slags match, table values with their page or table, then paper passages. Passages
  are dropped from the end to fit a length limit; table values are never cut.
- **Follow-ups** are merged with the previous form: what the follow-up names replaces the
  previous value, everything else carries over.

## Consequences

- `uv run ask "question" --profile slag.json` runs the whole path and prints the form, the tools
  and the evidence. It needs Ollama with the reader model, BigQuery and the Drive search index.
- Lookup without a slag profile only reaches slags that are in the catalogue (slags with
  chemistry); values from papers that report no slag chemistry are found through paper search.
- The reader is not fine-tuned. Its accuracy on intents, columns and filters still has to be
  measured on a question set before deciding whether to fine-tune or use a larger model.
