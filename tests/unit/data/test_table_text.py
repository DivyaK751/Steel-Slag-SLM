import pytest

from steel_slag_slm.data.table_text import (
    compact_markdown_table,
    is_contents_list,
    table_label,
)


def test_compact_table_drops_padding_and_separator_but_keeps_caption_and_cells() -> None:
    table = (
        "Table 2. Strength of the mixes.\n\n"
        "| Mix   |   UCS (MPa) - 28 d |\n"
        "|-------|-------------------:|\n"
        "| 9#    |              45.14 |\n"
        "| ψ < 1 | a \\| b            |"
    )

    assert compact_markdown_table(table) == (
        "Table 2. Strength of the mixes.\n\n"
        "| Mix | UCS (MPa) - 28 d |\n"
        "| 9# | 45.14 |\n"
        "| ψ < 1 | a \\| b |"
    )


def test_compact_table_keeps_empty_cells_and_rows_without_outer_pipes() -> None:
    assert compact_markdown_table("| A |   |\n|---|---|\n| 1 | 2") == "| A |  |\n| 1 | 2 |"


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Table 3. Mineral composition\n\n| a |", "Table 3"),
        ("TABLE 4 Oxides\n| a |", "Table 4"),
        ("Table-1. Mixes", "Table 1"),
        ("Tab. 2 Mixes", "Table 2"),
        ("Table iv: Phases", "Table IV"),
        ("| Table 1 | heading cell |", None),
        ("Tables of the appendix", None),
    ],
)
def test_table_label_reads_the_caption_number(text: str, label: str | None) -> None:
    assert table_label(text) == label


def test_is_contents_list_finds_dot_leaders_and_contents_headings() -> None:
    assert is_contents_list("| 1 Introduction ........ 3 |")
    assert is_contents_list("Table of contents")
    assert not is_contents_list("Table 1. Oxide composition")
