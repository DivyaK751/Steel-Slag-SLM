# 003: Semantic search index over parsed papers, stored in Drive

**Status:** accepted, 2026-10-21

## Context

`parse-pdfs` writes each paper's text and tables to `parsed.json` under `_parsed/<paper_id>/` in
the shared Drive folder (see [ADR 002](002-pdf-parsing-tooling.md)). The team needs to retrieve
passages from those papers by meaning, for any number of papers, without relying on any one
machine's local files.

## Decision

- Read `parsed.json` only from Drive, using the `_parsed/index.json` registry. Nothing is read
  from or written to a local data folder.
- Store the index in Drive too, in `<shared folder>/_search_index/<parsed folder>/_embeddings/`
  (the project folder everyone can write to, so anyone can build or update it; until
  2026-11-10 it sat in the parsed folder itself):
  - `chunks.json` holds the model name, dimension, every chunk's paper ID, kind, section, text,
    position, pages and linked figure captions, and the name of the current index file.
  - `index-<unique id>.faiss` holds the FAISS vectors, row for row with `chunks.json`.
  - Each save writes a new index file, then `chunks.json`, then deletes the older index files.
    A run that stops midway leaves the store pointing at a complete earlier index.
- Index the text, tables and figures the parser produced. Each figure is one chunk: its caption,
  then Gemini's reading (summary, plus the plotted values marked approximate, the XRD phases or
  the process steps). A figure not read yet is its caption alone. A long reading is split by
  rows, each piece repeating the caption, summary and column header. The figure chunk sits where
  its caption block is in the text; figures whose caption is not in the text flow come after the
  paper's other chunks. Each caption is also attached to the text chunks that mention that
  figure.
- Chunk from the paper's content blocks when present, using the labels: `caption` marks figure
  captions, and page furniture (`page_header`, `page_footer`) is skipped. Papers without blocks
  fall back to their Markdown.
- A `section_header` starts a section only if it is numbered ("3.2 Utilization…") or a standard
  name (Abstract, Introduction, Results, References…). Other headers, such as a title on the
  cover page, are body text, so text is not labelled with a title. Section titles are capped at
  150 characters.
- Publisher and repository notices ("accepted manuscript", repository footers), contents lists,
  short author and affiliation lines on the first pages, and repeated text are dropped.
- `build-index` logs every paper it leaves out and exits with an error when a parsed paper
  could not be read or produced no chunks. Papers the parser skipped on purpose do not fail the run.
- Chunk each section's paragraphs into passages of about `chunk_max_chars`, breaking only at
  sentence ends. A sentence left unfinished at a chunk or page break moves to the next chunk, and
  paragraphs interrupted by a page break or a table are joined back up. Each table is one chunk, split by row groups only when it is longer than
  `table_max_chars`; every piece repeats the table caption and header.
- Drop image placeholders and lines that repeat three or more times (running headers and footers).
- Embed the section heading, the chunk's text and its linked figure captions. Queries get the
  model's query prefix. Each chunk carries its paper's title (from the PDFs' Drive
  descriptions); table chunks are embedded and keyword-indexed with it in front, because a table
  is numbers and abbreviations that otherwise share almost no words with a question.
- Use `BAAI/bge-small-en-v1.5` through sentence-transformers. It runs on a CPU and needs no paid
  API. Vectors are unit length, so the FAISS inner-product index gives cosine similarity.
- Refuse to search an index built with a different model, because its vectors are not comparable.
- Search in two stages (2026-11-10):
  1. Rank chunks by embedding similarity and by BM25 keyword score (top 100 each), fuse the two
     rankings by reciprocal rank fusion, and keep the papers of the best chunks (`top_papers`, 5).
  2. Have the `BAAI/bge-reranker-v2-m3` cross-encoder read the question with every chunk of those
     papers, each preceded by its paper's title, and return the chunks in its order.

  Stage 1 only has to find the right papers, which it does well; the reranker, which reads
  question and passage together, picks the passage inside them. The alternatives tried and the
  scores behind this choice are in [retrieval_experiments.md](../retrieval_experiments.md).

## Consequences

- Page numbers come from each paper's content blocks, which carry a page and bounding box per
  text or table item. Each chunk records the pages it sits on, and search results show them.
- The sentence-transformers library caches the model weights in the user's Hugging Face cache on
  the machine that runs it (about 130 MB). That cache holds model files, not paper data.
- The whole index is loaded into memory for each search. A flat index is exact and simple, and
  it is fine at the size of this project.
- Papers are added incrementally: a run indexes only papers that are not yet in `chunks.json`.
  A paper whose parse generation changed since it was indexed is re-chunked and re-embedded.
  `build-index --rebuild` re-chunks and re-embeds every paper. `parse-pdfs` updates the index
  of the folder it parsed into once parsing is done, on a laptop or in the workflow, so new
  and re-parsed papers are indexed automatically.
- The reranker scores every chunk of 5 papers (about 400 per question): about 7 seconds per
  question on a T4 GPU, and much slower on a CPU. Where the assistant runs it is still open.
- An index built before titles were added gives the reranker no titles; run
  `build-index --rebuild` once.
- Retrieval quality is measured with fixed test sets, each searched over a fixed list of papers
  (its test papers plus 100 random parsed papers) so scores stay comparable as the corpus grows:
  `configs/retrieval_eval_human50.yaml` (68 questions from the human-extracted workbook, used to
  choose the setup), `retrieval_eval_heldout.yaml` (32 questions from other workbook facts) and
  `retrieval_eval_qualitative.yaml` (31 mechanism and phase questions the extraction tables cannot
  answer). `evaluate-retrieval --eval-set … --papers-dir … --titles …` indexes the listed papers in
  memory and reports paper@5 and answer@5/10/20. On 2026-11-10 the answer was in the top 5 for
  0.78, 0.62 and 1.00 of them; the held-out number is the one to expect for new value questions.
- The first test set (`configs/retrieval_eval.yaml`, 22 questions about one paper, searched
  within that paper) is kept: on 2026-10-24 it scored Hit@5 0.95 and MRR 0.88.
