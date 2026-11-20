import pytest

from steel_slag_slm.data.markdown_tables import (
    MarkdownGrid,
    TableShapeError,
    parse_grid,
    resolve_header,
    split_cells,
)


def _grid(*lines: str) -> MarkdownGrid:
    return parse_grid(list(lines))


def test_split_cells_unescapes_pipes_inside_cells() -> None:
    assert split_cells(r"| S\|1 | a&#124;b |") == ("S|1", "a|b")
    assert split_cells("S1 | 45") is None


def test_parse_grid_requires_a_divider_under_the_header() -> None:
    with pytest.raises(TableShapeError, match="header and divider"):
        _grid("| Mix | CaO |", "| S1 | 45 |")


def test_resolve_header_drops_columns_blank_throughout() -> None:
    resolved = resolve_header(_grid("| Mix | | CaO |", "| - | - | - |", "| S1 | | 45 |"))
    assert resolved.header == ("Mix", "CaO")
    assert resolved.body == (("S1", "45"),)
    assert resolved.columns == (0, 2)


def test_resolve_header_promotes_a_label_row_under_a_blank_header() -> None:
    resolved = resolve_header(_grid("| | |", "| - | - |", "| Mix | CaO |", "| S1 | 45 |"))
    assert resolved.header == ("Mix", "CaO")
    assert resolved.body == (("S1", "45"),)


def test_resolve_header_never_promotes_a_value_row() -> None:
    with pytest.raises(TableShapeError, match="Blank column header"):
        resolve_header(_grid("| | |", "| - | - |", "| S1 | 45 |"))


def test_resolve_header_moves_a_full_width_title_into_context() -> None:
    resolved = resolve_header(
        _grid("| Slag | Slag |", "| - | - |", "| Size | 1 sand |", "| Density | 3180 |")
    )
    assert resolved.context == ("Slag",)
    assert resolved.header == ("Size", "1 sand")
    assert resolved.body == (("Density", "3180"),)


def test_resolve_header_merges_a_spanning_label_with_the_label_row_below() -> None:
    resolved = resolve_header(
        _grid("| Mix | UCS (MPa) | |", "| - | - | - |", "| | 7 d | 28 d |", "| S1 | 30 | 45 |")
    )
    assert resolved.header == ("Mix", "UCS (MPa) - 7 d", "UCS (MPa) - 28 d")
    assert resolved.body == (("S1", "30", "45"),)


def test_resolve_header_numbers_labels_it_cannot_tell_apart() -> None:
    resolved = resolve_header(_grid("| Mix | Base | |", "| - | - | - |", "| S1 | 30 | 45 |"))
    assert resolved.header == ("Mix", "Base [1]", "Base [2]")


def test_resolve_header_moves_a_caption_merged_into_every_header_cell_into_context() -> None:
    resolved = resolve_header(
        _grid("| Table 1. Slag - Oxide | Table 1. Slag - EAF |", "| - | - |", "| CaO | 32.1 |")
    )
    assert resolved.context == ("Table 1. Slag",)
    assert resolved.header == ("Oxide", "EAF")


def test_resolve_header_treats_labels_differing_in_case_as_different_columns() -> None:
    resolved = resolve_header(
        _grid(
            "| | HGE2 | HgE2 |",
            "| - | - | - |",
            "| Shrinkage | x | x |",
            "| Moist | -16.6 | -16,6 |",
        )
    )
    assert resolved.header == ("", "HGE2", "HgE2")
    assert len(resolved.body) == 2
