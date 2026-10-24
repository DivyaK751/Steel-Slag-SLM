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
- Store the index in Drive too, in `_parsed/_embeddings/`:
  - `chunks.json` holds the model name, dimension, every chunk's paper ID, kind, section, text,
    position, pages and linked figure captions, and the name of the current index file.
  - `index-<unique id>.faiss` holds the FAISS vectors, row for row with `chunks.json`.
  - Each save writes a new index file, then `chunks.json`, then deletes the older index files.
    A run that stops midway leaves the store pointing at a complete earlier index.
- Index the text and tables the parser produced. Figures enter the index through their captions
  (`Fig. N ...`), and each caption is attached to the text chunks that mention that figure.
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
  model's query prefix.
- Use `BAAI/bge-small-en-v1.5` through sentence-transformers. It runs on a CPU and needs no paid
  API. Vectors are unit length, so the FAISS inner-product index gives cosine similarity.
- Refuse to search an index built with a different model, because its vectors are not comparable.

## Consequences

- Page numbers come from each paper's content blocks, which carry a page and bounding box per
  text or table item. Each chunk records the pages it sits on, and search results show them.
- The sentence-transformers library caches the model weights in the user's Hugging Face cache on
  the machine that runs it (about 130 MB). That cache holds model files, not paper data.
- The whole index is loaded into memory for each search. A flat index is exact and simple, and
  it is fine at the size of this project.
- Papers are added incrementally: a run indexes only papers that are not yet in `chunks.json`.
  `build-index --rebuild` re-chunks and re-embeds every paper. The parse workflow runs
  `build-index` after parsing, so new papers are indexed automatically.
- Retrieval quality is measured with a fixed test set (`configs/retrieval_eval.yaml`), and
  `evaluate-retrieval` reports Hit@K, Recall@5/20, MRR and nDCG@10. The set is written
  answer-first: 22 questions whose answers were taken from one paper's text (W2068066265) before
  any search, each stated in exactly one place and searched only within that paper. On 2026-10-24 the answer ranked first for 18 of 22 (Hit@5 0.95, MRR 0.88). The pages on all 22 answer chunks were checked against the PDF and matched.
