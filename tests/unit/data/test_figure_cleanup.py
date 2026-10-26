from pathlib import Path

import pytest

from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.data.figure_cleanup import (
    add_inner_captions,
    clean_figures,
    is_kept,
    match_captions,
    parse_figure_number,
    split_merged_figures,
)
from steel_slag_slm.schemas import CaptionBlock, Figure, PageBox

SETTINGS = FigureExtractionSettings(
    output_dir=Path("unused"),
    render_dpi=200,
    caption_max_gap_pt=60,
    min_side_pt=72,
    min_split_height_pt=50,
    drop_types=["logo", "icon"],
    min_uncaptioned_side_pt=150,
)


def _figure(bbox: PageBox, page: int = 1, figure_type: str = "line_chart") -> Figure:
    return Figure(page_number=page, bbox=bbox, figure_type=figure_type, type_confidence=0.9)


def _caption(bbox: PageBox, text: str, page: int = 1) -> CaptionBlock:
    number = parse_figure_number(text)
    assert number is not None
    return CaptionBlock(page_number=page, bbox=bbox, text=text, figure_number=number)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fig. 3 Particle size distribution", "3"),
        ("Figure 12: TG curves", "12"),
        ("FIGURE 5. XRD pattern", "5"),
        ("Fig.4a SEM image", "4a"),
        ("The results in Fig. 3 show", None),
        ("Table 2. Composition", None),
    ],
)
def test_parse_figure_number_reads_only_captions_that_start_with_fig(
    text: str, expected: str | None
) -> None:
    assert parse_figure_number(text) == expected


def test_match_captions_takes_the_nearest_caption_below_or_above() -> None:
    figures = [_figure((50, 100, 400, 300)), _figure((50, 400, 400, 600))]
    captions = [
        _caption((50, 310, 400, 330), "Fig. 1 Strength vs age"),
        _caption((50, 610, 400, 630), "Fig. 2 XRD pattern"),
    ]
    matched = match_captions(figures, captions, max_gap_pt=60)
    assert [figure.figure_number for figure in matched] == ["1", "2"]
    assert matched[1].caption == "Fig. 2 XRD pattern"


def test_match_captions_ignores_captions_on_other_pages_or_too_far_away() -> None:
    figures = [_figure((50, 100, 400, 300))]
    captions = [
        _caption((50, 310, 400, 330), "Fig. 1 Other page", page=2),
        _caption((50, 500, 400, 520), "Fig. 2 Too far below"),
    ]
    assert match_captions(figures, captions, max_gap_pt=60)[0].figure_number is None


def test_match_captions_uses_each_caption_once() -> None:
    # Two figures side by side share one caption below; only the nearer figure gets it
    figures = [_figure((50, 100, 300, 300)), _figure((50, 100, 300, 280))]
    captions = [_caption((50, 310, 300, 330), "Fig. 1 Shared")]
    matched = match_captions(figures, captions, max_gap_pt=60)
    assert sorted(str(figure.figure_number) for figure in matched) == ["1", "None"]


def test_match_captions_accepts_a_caption_in_the_column_beside_the_figure() -> None:
    figures = [_figure((164, 60, 500, 314))]
    captions = [_caption((47, 60, 136, 89), "Fig. 3 Particle size distribution")]
    assert match_captions(figures, captions, max_gap_pt=60)[0].figure_number == "3"


def test_match_captions_prefers_the_figure_a_caption_sits_under() -> None:
    # The caption is 11 pt under the first figure and 6 pt above the second
    figures = [_figure((129, 336, 490, 471)), _figure((77, 495, 540, 745))]
    captions = [_caption((129, 482, 444, 489), "Figure 2. SEM images")]
    matched = match_captions(figures, captions, max_gap_pt=60)
    assert [figure.figure_number for figure in matched] == ["2", None]


def test_split_merged_figures_splits_at_a_caption_inside_the_region() -> None:
    merged = _figure((50, 100, 400, 700))
    inner_caption = _caption((50, 380, 400, 400), "Figure 5. XRD of slag")
    parts = split_merged_figures([merged], [inner_caption], min_part_height_pt=50)
    assert [part.bbox for part in parts] == [(50, 100, 400, 380), (50, 400, 400, 700)]


def test_split_merged_figures_keeps_regions_whose_caption_sits_at_the_edge() -> None:
    figure = _figure((50, 100, 400, 420))
    caption_at_bottom = _caption((50, 395, 400, 420), "Fig. 1 Strength")
    assert split_merged_figures([figure], [caption_at_bottom], min_part_height_pt=50) == [figure]


def test_is_kept_drops_logos_and_tiny_regions() -> None:
    captioned = Figure(1, (0, 0, 300, 300), "line_chart", 0.9, caption="Fig. 1", figure_number="1")
    assert is_kept(captioned, SETTINGS)
    assert not is_kept(Figure(1, (0, 0, 300, 300), "logo", 0.9, "Fig. 1", "1"), SETTINGS)
    assert not is_kept(Figure(1, (0, 0, 40, 50), "line_chart", 0.9, "Fig. 1", "1"), SETTINGS)


def test_is_kept_keeps_only_large_uncaptioned_regions() -> None:
    # Some papers print the caption inside the image; author photos are small and uncaptioned
    assert is_kept(_figure((0, 0, 400, 250)), SETTINGS)
    assert not is_kept(_figure((0, 0, 95, 125), figure_type="photograph"), SETTINGS)


def test_clean_figures_returns_split_captioned_figures_in_reading_order() -> None:
    figures = [
        _figure((50, 100, 400, 700), page=2),
        _figure((50, 100, 400, 300), page=1),
        _figure((10, 10, 40, 40), page=1, figure_type="logo"),
    ]
    captions = [
        _caption((50, 310, 400, 330), "Fig. 1 First", page=1),
        _caption((50, 380, 400, 400), "Fig. 2 Upper", page=2),
        _caption((50, 710, 400, 730), "Fig. 3 Lower", page=2),
    ]
    cleaned = clean_figures(figures, captions, SETTINGS)
    assert [(figure.page_number, figure.figure_number) for figure in cleaned] == [
        (1, "1"),
        (2, "2"),
        (2, "3"),
    ]


def test_add_inner_captions_reads_a_caption_printed_inside_an_uncaptioned_figure() -> None:
    uncaptioned = _figure((0, 0, 400, 300))
    captioned = Figure(1, (0, 0, 400, 300), "bar_chart", 0.9, "Fig. 1 Strength", "1")
    texts = ["J. Civ. Eng. 2 theta Figure 4. XRD trace of un-reacted GGBS.", "Fig. 9 ignored"]
    result = add_inner_captions([uncaptioned, captioned], texts)
    assert (result[0].figure_number, result[0].caption) == (
        "4",
        "Figure 4. XRD trace of un-reacted GGBS.",
    )
    assert result[1] == captioned
