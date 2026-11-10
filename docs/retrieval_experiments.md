# Retrieval experiments

What was tried to make semantic search return the passage that answers a question, on what
data, and what each attempt scored. Experiments are listed in the order they were run. The
decision they led to is recorded in [ADR 003](adr/003-semantic-search-index.md).

**Where things stand (2026-11-10):** the best setup, F1, puts a passage containing the answer in
the top 5 for 78% of the tuning questions (85% in the top 10), up from 15% with the original
search. On held-out questions it scores 62% (72% in the top 10), so part of the tuning gain fit
the tuning questions. The target is 85%. Tables remain the weak spot; adding the sentences
that mention each table did not help (stage 7).

In the assistant, the extraction tables are queried first and the papers are searched only
when the tables do not hold the answer. Search therefore mostly serves values the extraction
missed and questions the tables cannot answer at all (mechanisms, phases, observations,
recommendations). On 31 such questions F1 put the answer in the top 5 every time (stage 8).

## Data

| Item | What it is |
| --- | --- |
| Parsed papers | `parsed.json` files from the approved-papers parsing run (974 papers, 963 parsed), including Gemini's figure readings |
| Tuning test set | [`configs/retrieval_eval_human50.yaml`](../configs/retrieval_eval_human50.yaml): 68 questions on 47 papers of the 50-paper human-extraction sample |
| Held-out test set | [`configs/retrieval_eval_heldout.yaml`](../configs/retrieval_eval_heldout.yaml): 32 questions on other facts of those papers, never used for tuning |
| Corpus A (tuning) | the 47 test papers + 100 random parsed papers as distractors: 147 papers, 12,606 chunks |
| Corpus B (held-out) | the 25 held-out papers + 100 *different* random parsed papers: 125 papers |
| Chunking | text chunks of up to 800 characters, tables split above 1,500 characters, one chunk per figure (caption + Gemini's reading) |

The test questions come from sheet 9 ("Citations & Provenance") of
`human_extracted_data/human_extracted_data.xlsx`: each question asks for one value the workbook
records, names the study it comes from, and lists the paper and the table, figure or text where
the value is printed. Every label was checked against the real chunks of its paper.

## Metrics

All are per question, then averaged.

| Metric | A question counts when |
| --- | --- |
| **paper@5** | a top-5 result comes from the question's paper |
| **answer@5** (main score) | a top-5 result comes from that paper and contains the answer value as printed (e.g. "10.6", as a whole number), whatever kind of chunk it is |
| answer@10, answer@20 | the same within the top 10 or 20 |
| exact label@5 (hit@5) | a top-5 result is the exact chunk the label names, e.g. "Table 4" of that paper |
| by kind | answer@5 split by where the answer lives: table (39 questions), figure (16), text (13) |

answer@5 can be no higher than paper@5. It counts a passage that contains the value anywhere,
which is what the chatbot needs, and it is approximate: a short value ("49") may appear for an
unrelated reason, and a value printed differently ("10,6") is missed.

## Stage 0: one paper, 22 questions (2026-10-24)

The first test set (`configs/retrieval_eval.yaml`): 22 questions answered from one paper,
W2068066265 (Yi et al., 2012), searched within that paper only.

| Setup | Hit@5 | Hit@20 | MRR |
| --- | --- | --- | --- |
| bge-small-en-v1.5 + FAISS | 0.95 | 1.00 | 0.88 |

Searching one paper is easy; this said little about search across the corpus.

## Stage 1: figure chunks (2026-11-10)

Parsing began storing Gemini's reading of every figure. Figures had been indexed by caption only;
each figure became one chunk holding its caption and reading (summary, plotted values, XRD
phases or process steps).

Checked on 15 random parsed papers: 171 figures became 209 figure chunks (large plots split by
rows), 205 with a reading and 4 caption-only. The first version made duplicate caption-only
chunks when the parser repeated a caption; that was fixed before the experiments below.

## Stage 2: building the scoring

1. **Exact labels only.** The first scoring accepted only the chunk a label named ("Table 4").
   With BM25 the right paper was in the top 5 for 92% of questions, but the exact chunk for only
   13%: the answer is often also in the abstract or results text, which was marked wrong.
2. **Answer values added.** Each question got the value the workbook reports, and any chunk of the
   paper containing it counts (answer@5). 8 of 76 questions were dropped because their value
   appears nowhere in the parsed text (figure values read by eye that the parse does not hold),
   leaving 68.

## Stage 3: embedding model, hybrid search and reranker

Corpus A, Colab T4 GPU. "hybrid" fuses the bge-small ranking with a BM25 keyword ranking by
reciprocal rank fusion; "rerank" re-scores the top 50 with a cross-encoder (bge-reranker-base).

Run 1, 76 questions, exact-label scoring:

| Setup | hit@5 | hit@20 | MRR | table | figure | text | time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bge-small | 0.08 | 0.16 | 0.06 | 0.00 | 0.00 | 0.46 | 40s |
| bge-m3 | 0.13 | 0.29 | 0.08 | 0.05 | 0.04 | 0.54 | 225s |
| hybrid | 0.11 | 0.22 | 0.09 | 0.00 | 0.04 | 0.54 | 6s |
| hybrid + rerank | 0.16 | 0.32 | 0.11 | 0.00 | 0.13 | 0.69 | 121s |

Run 2, 68 questions, answer scoring:

| Setup | paper@5 | answer@5 | answer@20 | table | figure | text | time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bge-small (original) | 0.85 | 0.15 | 0.29 | 0.08 | 0.06 | 0.46 | 40s |
| bge-m3 | 0.87 | 0.19 | 0.35 | 0.10 | 0.12 | 0.54 | 207s |
| hybrid | 0.96 | 0.19 | 0.35 | 0.08 | 0.19 | 0.54 | 5s |
| hybrid + rerank | 0.87 | 0.31 | 0.43 | 0.15 | 0.38 | 0.69 | 101s |

**Findings.** The reranker helped most. bge-m3 gained little for five times the embedding time,
so the embedding model was kept. Tables were found almost never by any setup.

## Stage 4: why tables are missed (BM25, 68 questions, corpus A)

Run on a laptop with BM25 alone, since it takes seconds.

| Change to the indexed text | paper@5 | answer@5 | answer@20 | table answer@5 |
| --- | --- | --- | --- | --- |
| none | 0.94 | 0.22 | 0.38 | 0.08 |
| tables rewritten as sentences ("CaO: 10.6; SiO2: 15.5") | 0.94 | 0.22 | 0.40 | 0.08 |
| **paper title in front of table chunks** | **0.99** | **0.35** | **0.57** | **0.33** |
| title on tables + tables as sentences | 0.96 | 0.29 | 0.54 | 0.23 |
| title in front of every chunk | 1.00 | 0.34 | 0.56 | 0.15 |

A table holds numbers and abbreviations, not the study's words that the question uses, so it
ranked below every text chunk of its own paper (e.g. 5,039th or 11,195th). The paper title gives
it those words.

Searching only the top papers' chunks (two-stage), still with BM25:

| Setup | paper@5 | answer@5 | answer@20 |
| --- | --- | --- | --- |
| titles on tables, all papers | 0.99 | 0.35 | 0.57 |
| top 5 papers only | 0.99 | 0.35 | 0.60 |
| top 3 papers only | 0.96 | 0.34 | 0.62 |
| top 1 paper only | 0.85 | 0.31 | 0.65 |
| top 1 paper, title words dropped from the question (no titles on tables) | 0.87 | 0.47 | 0.59 |
| top 3 papers, title words dropped | 0.66 | 0.29 | 0.56 |
| top 5 papers, title words dropped | 0.53 | 0.22 | 0.46 |

Restricting to the top papers brought the answer into the top 20 more often but not into the top
5: BM25 ranks the right paper's chunks in the wrong order. Dropping the study's words from the
question helped inside a single paper but mixed papers up when there were several.

## Stage 5: combining the fixes

Corpus A, 68 questions, Colab T4. "top N papers" = hybrid search picks N papers and the reranker
scores every chunk of them.

Run 3:

| | Setup | paper@5 | answer@5 | answer@20 | table | figure | text | time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | hybrid + rerank(base) | 0.87 | 0.31 | 0.43 | 0.15 | 0.38 | 0.69 | 37s |
| B | top 3 papers + rerank(base) | 0.90 | 0.34 | 0.50 | 0.21 | 0.31 | 0.77 | 92s |
| C | hybrid + rerank(v2-m3) | 0.75 | 0.34 | 0.49 | 0.18 | 0.38 | 0.77 | 111s |
| D | top 3 papers + rerank(v2-m3) | 0.85 | 0.44 | 0.68 | 0.28 | 0.50 | 0.85 | 276s |
| E | titles on tables + hybrid + rerank(v2-m3) | 0.85 | 0.62 | 0.79 | 0.69 | 0.31 | 0.77 | 61s |
| F | titles on tables + top 3 papers + rerank(v2-m3) | 0.93 | 0.74 | 0.90 | 0.77 | 0.56 | 0.85 | 275s |

Run 4, top 5 papers, all with titles on table chunks and rerank(v2-m3):

| | Setup | paper@5 | answer@5 | answer@20 | table | figure | text | time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F | as in run 3, top 5 papers | 0.90 | 0.65 | 0.88 | 0.67 | 0.44 | 0.85 | 477s |
| **F1** | **F + the reranker reads the paper title on every chunk** | **0.99** | **0.78** | **0.91** | **0.74** | **0.69** | **1.00** | 467s |
| F2 | F1 + titles on figure chunks in search | 0.97 | 0.75 | 0.90 | 0.72 | 0.62 | 1.00 | 523s |
| F3 | F2 + reranker ranking blended with the hybrid ranking | 0.96 | 0.68 | 0.88 | 0.69 | 0.69 | 0.62 | 0s |

**Findings.**

- The stronger reranker (bge-reranker-v2-m3) helped only once it was kept to the top papers (C
  vs D): on its own it ranked look-alike passages from other studies first.
- Titles on table chunks were the largest single gain (D to F: tables 0.28 to 0.77).
- Giving the reranker the paper title on every chunk let it tell studies apart: paper@5 rose from
  0.90 to 0.99 and answer@5 from 0.65 to 0.78 (F to F1).
- With 3 papers, F scored 0.74; with 5, 0.65 and took longer. The team chose 5 papers.

F1's 15 misses: 11 were in the right paper, where the reranker preferred a paragraph or the
abstract over the table or figure holding the number; 5 had the answer at rank 6 to 9.

## Stage 6: checks of F1

F1 was run again on the tuning set and once on the held-out set (corpus B), Colab T4:

| Set | paper@5 | answer@5 | answer@10 | answer@20 | table | figure | text | time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| tuning (68 questions) | 0.99 | 0.78 | 0.85 | 0.91 | 0.74 (39) | 0.69 (16) | 1.00 (13) | 563s |
| held-out (32 questions) | 0.88 | 0.62 | 0.72 | 0.84 | 0.53 (19) | 0.71 (7) | 0.83 (6) | 306s |

The tuning score repeated exactly. The held-out score is 0.16 lower: choosing among about 30
setups on the same questions inflated the tuning score, and the held-out number is the better
estimate for new questions of this kind. With 32 questions, one question moves it by 0.03.

**The 15 tuning-set misses, read by hand:**

| Kind of miss | Questions | What happened |
| --- | --- | --- |
| Relevant passage returned without the workbook's exact value | h17, h23, h29, h41 | e.g. h29's top result states the 28-day strengths of the same mixes ("decreased from 34.1 to 2.2 MPa") |
| Wrong paper | h16, h24, h47 | generic-sounding questions (ladle slag composition, concrete strength) matched other studies |
| Right paper, wrong passage | h05, h06, h11, h44, h56, h73, h76 | the paper's text or conclusions outranked the table or figure holding the value |
| Parse or language | h04 | Japanese paper; Japanese text describing Table 1 ranked above the table |

Counting h29 and h41 as hits, the tuning score would be about 0.81.

## Stage 7: table chunks carry the sentences that mention them (dropped)

Most remaining misses are tables losing to text in the right paper. As a fix, each table chunk
also held the paper's sentences that mention it by number ("Table 4 lists the measured
strength…"), up to 500 characters, placed before the rows.

| Tuning set | paper@5 | answer@5 | answer@10 | answer@20 | table | figure | text |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F1 | 0.99 | 0.78 | 0.85 | 0.91 | 0.74 | 0.69 | 1.00 |
| F1 + table mentions | 0.99 | 0.75 | 0.82 | 0.90 | 0.69 | 0.69 | 1.00 |

It made tables slightly worse, so it was dropped. The likely reason: the reranker reads only the
first 512 tokens, and title, section and the added sentences pushed the table's rows, where the
values are, past that limit more often.

## Stage 8: questions only the papers can answer

The extraction tables hold values, so questions about mechanisms, mineral phases, hydration
products, microstructure and authors' recommendations always reach search.
[`configs/retrieval_eval_qualitative.yaml`](../configs/retrieval_eval_qualitative.yaml) holds 31
such questions on 27 of the sample papers, written answer-first from their parsed text: each
answer is a phrase copied from the sentence that states it ("calcite and gaylussite", "high
alkalinity of SS"), and a chunk of the paper containing the phrase counts. Every phrase was
checked against the paper's chunks.

| Set | paper@5 | answer@5 | answer@10 | answer@20 | time |
| --- | --- | --- | --- | --- | --- |
| F1, qualitative (31 questions, corpus A) | 1.00 | 1.00 | 1.00 | 1.00 | 269s |

Every answer was in the top 5. The set is easier than real use: the questions were written from
the sentences that answer them, so they share key terms with those sentences, and all answers
are in prose, where F1 is strongest. It does show that F1 finds explanations and observations
reliably when a question names the study and the concept.

A second planned set: values people found that the extraction misses. It waits for the updated
extraction (which adds figure values) to run on the 50 sample papers; BigQuery currently holds
extraction output for 5 of them.

## Chosen setup: F1

1. Index text, table and figure chunks with bge-small-en-v1.5; table chunks carry the paper title.
2. Rank by hybrid search (bge-small + BM25, reciprocal rank fusion) and keep the top 5 papers.
3. Rerank every chunk of those papers with bge-reranker-v2-m3, each chunk preceded by its paper
   title.

On the T4 it takes about 7 seconds per question, almost all of it reranking every chunk of 5
papers (about 400 per question); on a laptop CPU it would be much slower.

## Caveats

- **Tuned on its own test.** About 30 setups were compared on the same 68 questions, so the best
  score flatters the setup: on held-out questions F1 scores 0.62, not 0.78.
- **Question style.** The questions were written from the workbook, each naming a study and asking
  for one value. General questions across papers and follow-up questions are not tested.
- **Ground truth.** The workbook was filled in with Claude's help (`Extraction_Method` says
  "Manual reading (Claude)"), so it is independent of our Gemini pipeline but not purely human.
- **Corpus size.** 147 papers, not the full 963; more distractors will likely lower the scores.
- **Small groups.** 13 text and 16 figure questions, so one question moves those scores by 0.06
  to 0.08.
