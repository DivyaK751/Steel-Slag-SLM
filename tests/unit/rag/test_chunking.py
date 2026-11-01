import pytest

from steel_slag_slm.rag.chunking import (
    build_chunks,
    clean_lines,
    pack_paragraphs,
    passage_for_embedding,
    split_table,
)
from steel_slag_slm.schemas import Chunk, ContentPlacement, ParsedContentBlock, ParsedPaper

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
    long_paragraph = " ".join(["Cement."] * 200)  # short sentences can be packed
    chunks = build_chunks(_paper(markdown=f"## A\n\n{long_paragraph}\n"), 100, 1500)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 100 for chunk in chunks)


def test_build_chunks_rejects_non_positive_sizes() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        build_chunks(_paper(), max_chars=0, table_max_chars=1500)


def test_pack_paragraphs_joins_short_paragraphs_up_to_the_limit() -> None:
    packed = pack_paragraphs([("aaa", ()), ("bbb", ()), ("ccc", ())], max_chars=7)
    assert packed == [("aaa bbb", ()), ("ccc", ())]


def test_pack_paragraphs_gives_a_passage_the_union_of_its_pages() -> None:
    packed = pack_paragraphs([("aaa", (2,)), ("bbb", (3, 2))], max_chars=800)
    assert packed == [("aaa bbb", (2, 3))]


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


def _block(label: str, text: str, pages: tuple[int, ...] = (1,), kind: str = "text"):
    placements = tuple(
        ContentPlacement(
            page_number=page, bbox=(0.0, 0.0, 1.0, 1.0), coordinate_origin="BOTTOMLEFT"
        )
        for page in pages
    )
    return ParsedContentBlock(kind=kind, label=label, text=text, placements=placements)


def _block_paper(blocks: list[ParsedContentBlock]) -> ParsedPaper:
    return ParsedPaper(openalex_id="W1", markdown="", tables=(), content_blocks=tuple(blocks))


def test_block_chunks_take_sections_from_section_headers_and_keep_pages() -> None:
    blocks = [
        _block("section_header", "Introduction"),
        _block("text", "Steel slag is used in cement.", pages=(1,)),
        _block("section_header", "Results"),
        _block("text", "Free lime causes expansion.", pages=(2,)),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [(c.section, c.text, c.pages) for c in chunks] == [
        ("Introduction", "Steel slag is used in cement.", (1,)),
        ("Results", "Free lime causes expansion.", (2,)),
    ]


def test_block_chunks_merge_the_pages_of_packed_blocks() -> None:
    blocks = [_block("text", "aaa", pages=(3,)), _block("text", "bbb", pages=(4,))]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [(c.text, c.pages) for c in chunks] == [("aaa bbb", (3, 4))]


def test_block_chunks_skip_page_headers_and_footers() -> None:
    blocks = [
        _block("page_header", "Journal header"),
        _block("text", "Body text"),
        _block("page_footer", "3"),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [c.text for c in chunks] == ["Body text"]


def test_block_tables_keep_their_section_and_pages() -> None:
    blocks = [
        _block("section_header", "Results"),
        _block("table", TABLE, pages=(5,), kind="table"),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    (table,) = [c for c in chunks if c.kind == "table"]
    assert (table.text, table.section, table.pages) == (TABLE, "Results", (5,))


def test_block_captions_that_name_a_figure_become_figure_captions() -> None:
    blocks = [_block("caption", "Fig. 2. Setup", pages=(6,))]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [(c.kind, c.text, c.pages) for c in chunks] == [
        ("figure_caption", "Fig. 2. Setup", (6,))
    ]


def test_papers_without_blocks_fall_back_to_markdown_with_no_pages() -> None:
    chunks = build_chunks(_paper(), max_chars=800, table_max_chars=1500)
    assert chunks
    assert all(chunk.pages == () for chunk in chunks)


def test_a_title_header_is_body_text_not_a_section() -> None:
    blocks = [
        _block("section_header", "EAF slag influence on concrete"),
        _block("text", "Graduation thesis No.: 142/B-GR"),
    ]
    (chunk,) = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert chunk.section == ""
    assert chunk.text == "EAF slag influence on concrete Graduation thesis No.: 142/B-GR"


def test_numbered_and_standard_headers_start_sections() -> None:
    blocks = [
        _block("section_header", "3.2. Utilization for road construction"),
        _block("text", "Road text."),
        _block("section_header", "Results"),
        _block("text", "Results text."),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [(c.section, c.text) for c in chunks] == [
        ("3.2. Utilization for road construction", "Road text."),
        ("Results", "Results text."),
    ]


def test_publisher_and_repository_notices_are_dropped() -> None:
    blocks = [
        _block("text", "ACCEPTED MANUSCRIPT"),
        _block("text", "Digital repository of the faculty, archiving date 15-10-2014"),
        _block("text", "Body text about steel slag."),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [c.text for c in chunks] == ["Body text about steel slag."]


def test_long_section_titles_are_cut_to_the_cap() -> None:
    long_title = "2 " + "Kinetics of aqueous leaching " * 10
    blocks = [_block("section_header", long_title), _block("text", "Text.")]
    (chunk,) = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert len(chunk.section) <= 150


def test_body_text_that_starts_with_fig_stays_text_when_the_parser_says_text() -> None:
    blocks = [_block("text", "Fig 1 shows some of the important relationships.")]
    (chunk,) = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert chunk.kind == "text"


def test_long_paragraphs_break_at_sentence_ends_not_inside_sentences() -> None:
    sentences = [f"Sentence number {i} about steel slag carbonation." for i in range(12)]
    blocks = [_block("text", " ".join(sentences))]
    chunks = build_chunks(_block_paper(blocks), max_chars=150, table_max_chars=1500)
    assert len(chunks) > 1
    assert all(c.text.endswith(".") for c in chunks)
    assert all(c.text[:1].isupper() for c in chunks)


def test_a_sentence_split_across_blocks_is_joined_back() -> None:
    blocks = [
        _block("text", "The steel slag is rich in calcium", pages=(1,)),
        _block("text", "oxide and used as aggregate.", pages=(2,)),
    ]
    (chunk,) = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert chunk.text == "The steel slag is rich in calcium oxide and used as aggregate."
    assert chunk.pages == (1, 2)


def test_blocks_are_not_joined_when_the_next_one_starts_a_new_sentence() -> None:
    blocks = [
        _block("text", "The steel slag is rich in calcium oxide"),
        _block("text", "Another paragraph begins here."),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert len(chunks) == 1
    assert chunks[0].text.startswith("The steel slag is rich in calcium oxide Another")


def test_licence_text_is_dropped() -> None:
    blocks = [
        _block(
            "text", "This is an open access article distributed under the terms of the Creative"
        ),
        _block("text", "Body text about slag."),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [c.text for c in chunks] == ["Body text about slag."]


def test_a_sentence_resumed_after_a_table_is_joined_back() -> None:
    blocks = [
        _block("text", "The slag is rich in calcium", pages=(1,)),
        _block("table", TABLE, pages=(2,), kind="table"),
        _block("text", "oxide and used as aggregate.", pages=(3,)),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    text_chunks = [c for c in chunks if c.kind == "text"]
    assert [c.text for c in text_chunks] == [
        "The slag is rich in calcium oxide and used as aggregate."
    ]
    assert [c.kind for c in chunks] == ["table", "text"]


def test_an_unfinished_sentence_moves_to_the_start_of_the_next_chunk() -> None:
    from steel_slag_slm.rag.chunking import _carry_unfinished_sentences

    pieces = [
        ("text", "S", "Alpha is short. Gamma runs on in the", ()),
        ("text", "S", "utilisation of slag.", ()),
    ]
    assert _carry_unfinished_sentences(pieces) == [
        ("text", "S", "Alpha is short.", ()),
        ("text", "S", "Gamma runs on in the utilisation of slag.", ()),
    ]


def test_soft_hyphens_from_line_breaks_are_removed() -> None:
    blocks = [_block("text", "The steel slag produc\u00ad tion is large.")]
    (chunk,) = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert chunk.text == "The steel slag production is large."


def test_table_of_contents_is_dropped() -> None:
    blocks = [
        _block("text", "Kazalo vsebine 1 Uvod ........ 3 2 Results ........ 7"),
        _block("text", "Body text."),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [c.text for c in chunks] == ["Body text."]


def test_administrative_and_peer_review_text_is_dropped() -> None:
    blocks = [_block("text", "FOR PEER REVIEW ONLY"), _block("text", "Body text.")]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [c.text for c in chunks] == ["Body text."]


def test_repeated_text_in_a_paper_is_kept_once() -> None:
    blocks = [_block("text", "Same passage here."), _block("text", "Same passage here.")]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [c.text for c in chunks] == ["Same passage here."]


def test_a_figure_caption_is_linked_to_text_that_mentions_the_figure() -> None:
    blocks = [
        _block("text", "The microstructure is shown in Fig. 2 for both slags."),
        _block("caption", "Fig. 2. Microstructure of the slag.", kind="text"),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    text_chunk = next(c for c in chunks if c.kind == "text" and "Fig. 2" in c.text)
    assert text_chunk.figure_captions == ("Fig. 2. Microstructure of the slag.",)
    assert "Fig. 2. Microstructure of the slag." in passage_for_embedding(text_chunk)


def test_a_contents_list_read_as_a_table_is_dropped() -> None:
    dots = "." * 40
    contents = f"| Uvod {dots} | 3 |\n| Results {dots} | 7 |"
    blocks = [_block("table", contents, kind="table"), _block("text", "Body text.")]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [c.text for c in chunks] == ["Body text."]


def test_a_numbered_heading_inside_body_text_starts_its_own_section() -> None:
    blocks = [
        _block("section_header", "3.4. Thermodynamic Simulation"),
        _block("text", "4. Conclusions LCC-SP presented a model."),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [(c.section, c.text) for c in chunks] == [
        ("4. Conclusions", "LCC-SP presented a model."),
    ]


def test_captions_in_other_languages_are_linked() -> None:
    blocks = [
        _block("text", "Mikrostruktura je prikazana na Slika 1 za oba vzorca."),
        _block("caption", "Slika 1. Mikrostruktura žlindre.", kind="text"),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    text_chunk = next(c for c in chunks if c.kind == "text" and "Slika 1" in c.text)
    assert text_chunk.figure_captions == ("Slika 1. Mikrostruktura žlindre.",)


def test_short_affiliation_lines_on_the_first_pages_are_dropped() -> None:
    blocks = [
        _block("text", "Faculty of Civil Engineering, University of Ljubljana, Slovenia"),
        _block("text", "Body text about the slag."),
    ]
    chunks = build_chunks(_block_paper(blocks), max_chars=800, table_max_chars=1500)
    assert [c.text for c in chunks] == ["Body text about the slag."]
