# 004: Read questions with a small model, route them with code

**Status:** proposed, 2026-11-23

## Context

The assistant answers from three tools: the extraction tables (lookup), the slag catalogue
(similar slags) and the paper search index. Something has to decide, for each message, which
tools to run and with what input, and then hand their results to the fine-tuned answer model.
Small models are unreliable at planning tool calls, and every answer must be traceable to the
data it came from.

## Decision

- **A small open model reads; code decides.** The reader model (qwen2.5:7b, served locally by
  Ollama) only fills a `QuestionForm`: message type, whether the answer is values only, the
  extraction-table columns asked for, filters on columns, oxides stated in the message, whether
  it is about the user's slag, missing details and a stand-alone search query. Fixed rules in
  `agents/router.py` turn the form into tool calls, so the same form always gives the same calls.
  Each field states one fact about the message, and each tool has one trigger; there is no
  separate list of intents that could contradict the fields.
- **Columns come from the sheet definitions.** `form_columns()` lists every data column of
  sheets 1 to 8 from `data/extraction_tables.py`; a new column there can be asked about with no
  other change. The reader's output is forced to a JSON schema that lists only those columns and
  the catalogue's oxides, so it cannot invent a column.
- **Text values come from the data.** Columns with a fixed vocabulary list it. Other short text
  columns (at most `max_listed_values` distinct values) list the values the BigQuery tables
  hold now, read once per session, most common first; longer free-text columns show "(text)".
  After reading, a text filter's value is spelled as the data spells it, ignoring case; a value
  that matches nothing is looked for inside the values (`contains`) instead.
- **Routing rules:**
  - chat or off-topic: no tools; the answer model replies briefly.
  - about the user's slag, or a composition typed in the message: similar slags, then lookup on
    those slags. Typed oxides override the stored profile for that message.
  - names columns or result filters: lookup on the catalogue slags chosen by the slag-type and
    chemistry filters, keeping only rows that meet the result filters and report the
    asked-for columns. A why question whose only filters are slag type or chemistry skips
    lookup, which would otherwise return every value of those slags.
    A reasoning question with no filters at all also skips lookup: nothing would narrow it,
    so it would read every catalogue slag.
  - asks for an age with no column of its own (14-day strength): the nearest age's column,
    with a note, and paper search too.
  - the reader gives no valid form after one retry (the retry is told what was wrong):
    paper search with the message as typed, with a note.
  - not values-only (the default: reasoning, advice, comparison...), or none of the above:
    paper search with the stand-alone query. A wrong guess adds evidence instead of losing it.
  - an "=" filter on a chemistry oxide is a composition the user states: it moves to
    chemistry, so similar slags are found.
- **Examples are picked per message.** `prompts/read_question_examples.yaml` holds worked
  forms for every kind of message; the few closest in meaning (same embedder as paper search)
  are shown with each message. Examples are written for kinds of failure, never copied from
  an evaluation set.
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
- The reader is not fine-tuned. Its accuracy on message types, columns and filters still has to be
  measured on a question set before deciding whether to fine-tune or use a larger model.
