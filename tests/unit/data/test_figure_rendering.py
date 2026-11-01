from pathlib import Path

import pymupdf
import pytest

from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.data.figure_detection import DetectedLayout
from steel_slag_slm.data.figure_rendering import render_figures
from steel_slag_slm.schemas import CaptionBlock, Figure, PageBox


def _settings() -> FigureExtractionSettings:
    return FigureExtractionSettings(
        output_dir=Path("unused"),
        render_dpi=72,
        caption_max_gap_pt=60,
        min_side_pt=72,
        min_split_height_pt=50,
        drop_types=["logo"],
        min_uncaptioned_side_pt=150,
    )


def _pdf() -> bytes:
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.draw_rect(pymupdf.Rect(60, 110, 140, 190), color=(0, 0, 0))
        page.insert_text((60, 150), "Figure 4. Strength")
        return bytes(document.tobytes())


def test_render_figures_recovers_internal_caption_before_filtering() -> None:
    pdf = _pdf()
    layout = DetectedLayout([Figure(1, (50, 100, 150, 200), "line_chart", 0.9)], [])

    rendered = render_figures(pdf, layout, _settings())

    assert len(rendered) == 1
    item = rendered[0]
    assert item.figure.figure_number == "4"
    assert item.figure.caption_block is None
    assert item.file_name == "figure_01.png"
    assert (item.width, item.height) == (100, 100)
    assert "Figure 4. Strength" in item.pdf_text
    assert item.image_bytes.startswith(b"\x89PNG")

    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        assert document[0].get_images() == []


def test_render_figures_preserves_caption_and_drops_logo() -> None:
    caption = CaptionBlock(1, (50, 310, 300, 330), "Fig. 1 Strength", "1")
    figure = Figure(1, (50, 100, 300, 300), "line_chart", 0.9)
    logo = Figure(1, (10, 10, 100, 100), "logo", 0.9)

    rendered = render_figures(_pdf(), DetectedLayout([logo, figure], [caption]), _settings())

    assert len(rendered) == 1
    assert rendered[0].figure.caption_block is caption
    assert (rendered[0].width, rendered[0].height) == (250, 200)


def test_render_figures_accepts_zero_figures() -> None:
    assert render_figures(_pdf(), DetectedLayout([], []), _settings()) == ()


@pytest.mark.parametrize(
    ("page", "bbox"),
    [
        (0, (50, 100, 300, 300)),
        (2, (50, 100, 300, 300)),
        (1, (300, 100, 50, 300)),
        (1, (50, 100, float("nan"), 300)),
        (1, (50, 100, 700, 300)),
    ],
)
def test_render_figures_rejects_invalid_regions(page: int, bbox: PageBox) -> None:
    layout = DetectedLayout([Figure(page, bbox, "line_chart", 0.9)], [])

    with pytest.raises(ValueError):
        render_figures(_pdf(), layout, _settings())


def test_render_figures_rejects_nonpositive_dpi() -> None:
    settings = _settings().model_copy(update={"render_dpi": 0})

    with pytest.raises(ValueError, match="render_dpi"):
        render_figures(_pdf(), DetectedLayout([], []), settings)


def test_render_figures_rejects_empty_pdf() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        render_figures(b"", DetectedLayout([], []), _settings())