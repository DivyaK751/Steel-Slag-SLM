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
  - `chunks.json` holds the model name, dimension, every chunk's paper ID, kind, section, text
    and position, and the name of the current index file.
  - `index-<unique id>.faiss` holds the FAISS vectors, row for row with `chunks.json`.
  - Each save writes a new index file, then `chunks.json`, then deletes the older index files.
    A run that stops midway leaves the store pointing at a complete earlier index.
- Index only the text the parser produced. Image resources are not analysed, so figures enter the
  index only as their caption paragraphs (`Fig. N ...`), marked as `figure_caption`.
- Chunk each section's paragraphs into passages of at most `chunk_max_chars`, never splitting a
  paragraph. Each table is one chunk, split by row groups only when it is longer than
  `table_max_chars`; every piece repeats the table caption and header.
- Drop image placeholders and lines that repeat three or more times (running headers and footers).
- Embed the section heading with each chunk's text. Queries get the model's query prefix.
- Use `BAAI/bge-small-en-v1.5` through sentence-transformers. It runs on a CPU and needs no paid
  API. Vectors are unit length, so the FAISS inner-product index gives cosine similarity.
- Refuse to search an index built with a different model, because its vectors are not comparable.

## Consequences

- Page references are not stored for text or tables. `parsed.json` keeps page numbers only for
  image placements, and the parser discards Docling's per-item page provenance. Chunks carry the
  section instead. Adding page numbers means changing the parser and re-parsing every paper,
  which is a separate decision.
- The sentence-transformers library caches the model weights in the user's Hugging Face cache on
  the machine that runs it (about 130 MB). That cache holds model files, not paper data.
- The whole index is loaded into memory for each search. A flat index is exact and simple, and
  it is fine at the size of this project. Move to an approximate index if the store grows past
  what one machine can hold.
- Papers are added incrementally: a run indexes only papers that are not yet in `chunks.json`.
  Changing the chunking or the model means rebuilding the index from scratch.
- Retrieval quality is measured by `evaluate-retrieval` against labelled queries in
  `configs/base.yaml`. Those queries currently cover one paper, so they are a smoke test until
  more papers are indexed.
