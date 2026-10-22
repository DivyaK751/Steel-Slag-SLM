import pytest

from steel_slag_slm.rag.chunking import (
    build_chunks,
    clean_lines,
    pack_paragraphs,
    passage_for_embedding,
    split_table,
)
from steel_slag_slm.schemas import Chunk, ParsedPaper

MARKDOWN = """Journal header line
## Introduction

Steel slag is a by-product of steelmaking. It is used in cement.

<!-- image -->

Journal header line

## Results

Free lime causes volume expansion.
Table 1. Composition
| Oxide | CaO |
|-------|-----|
| BOFS | 50 |

Fig. 1. Process outline

Journal header line
"""

TABLE = "Table 1. Composition\n| Oxide | CaO |\n|-------|-----|\n| BOFS | 50 |"


def _paper(markdown: str = MARKDOWN, tables: tuple[str, ...] = (TABLE,)) -> ParsedPaper:
    return ParsedPaper(openalex_id="W1", markdown=markdown, tables=tables)


def test_clean_lines_drops_placeholders_and_repeated_running_lines() -> None:
    cleaned = clean_lines(MARKDOWN)
    assert "<!-- image -->" not in cleaned
    assert "Journal header line" not in cleaned
    assert "## Results" in cleaned


def test_clean_lines_keeps_a_running_line_that_appears_fewer_than_three_times() -> None:
    cleaned = clean_lines("Short note\n\nSome text\n\nShort note\n")
    assert cleaned.count("Short note") == 2


def test_build_chunks_tags_each_chunk_with_its_section() -> None:
    chunks = build_chunks(_paper(), max_chars=800, table_max_chars=1500)
    intro = [chunk for chunk in chunks if chunk.kind == "text" and chunk.section == "Introduction"]

    assert [chunk.text for chunk in intro] == [
        "Steel slag is a by-product of steelmaking. It is used in cement."
    ]
    assert any(chunk.section == "Results" and "Free lime" in chunk.text for chunk in chunks)


def test_build_chunks_keeps_tables_whole_and_in_their_section() -> None:
    chunks = build_chunks(_paper(), max_chars=800, table_max_chars=1500)
    (table,) = [chunk for chunk in chunks if chunk.kind == "table"]
    assert table.text == TABLE
    assert table.section == "Results"


def test_build_chunks_marks_figure_captions() -> None:
    chunks = build_chunks(_paper(), max_chars=800, table_max_chars=1500)
    (caption,) = [chunk for chunk in chunks if chunk.kind == "figure_caption"]
    assert caption.text == "Fig. 1. Process outline"
    assert caption.section == "Results"


def test_build_chunks_numbers_positions_in_reading_order() -> None:
    chunks = build_chunks(_paper(), max_chars=800, table_max_chars=1500)
    assert [chunk.position for chunk in chunks] == list(range(len(chunks)))
    assert [chunk.kind for chunk in chunks][-2:] == ["table", "figure_caption"]


def test_build_chunks_keeps_text_within_the_size_limit() -> None:
    long_paragraph = " ".join(["cement"] * 200)
    chunks = build_chunks(_paper(markdown=f"## A\n\n{long_paragraph}\n"), 100, 1500)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 100 for chunk in chunks)


def test_build_chunks_rejects_non_positive_sizes() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        build_chunks(_paper(), max_chars=0, table_max_chars=1500)


def test_pack_paragraphs_joins_short_paragraphs_up_to_the_limit() -> None:
    assert pack_paragraphs(["aaa", "bbb", "ccc"], max_chars=7) == ["aaa bbb", "ccc"]


def test_split_table_repeats_caption_and_header_in_every_piece() -> None:
    rows = "\n".join(f"| row {i} | {'x' * 40} |" for i in range(10))
    table = f"Table 9. Long\n| A | B |\n|---|---|\n{rows}"

    pieces = split_table(table, max_chars=200)

    assert len(pieces) > 1
    for piece in pieces:
        assert piece.startswith("Table 9. Long\n| A | B |\n|---|---|")
    assert "row 9" in pieces[-1]


def test_split_table_returns_short_tables_unchanged() -> None:
    assert split_table(TABLE, max_chars=1000) == [TABLE]


def test_passage_for_embedding_puts_the_section_first() -> None:
    chunk = Chunk(paper_id="W1", kind="text", section="Results", text="Free lime", position=0)
    assert passage_for_embedding(chunk) == "Results\nFree lime"
    assert passage_for_embedding(Chunk("W1", "text", "", "Free lime", 0)) == "Free lime"
