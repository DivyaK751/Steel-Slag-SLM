import io

import pymupdf
from PIL import Image

from steel_slag_slm.data.pdf_pages import (
    BoxAnnotation,
    draw_boxes,
    first_pages,
    page_text,
    region_text,
    render_regions,
)


def _pdf(*page_texts: str) -> bytes:
    with pymupdf.open() as document:
        for text in page_texts:
            document.new_page().insert_text((72, 72), text)
        return document.tobytes()


def test_first_pages_keeps_only_the_requested_pages() -> None:
    excerpt = first_pages(_pdf("one", "two", "three", "four"), 3)
    assert page_text(excerpt).split() == ["one", "two", "three"]


def test_first_pages_keeps_all_pages_of_a_shorter_pdf() -> None:
    assert page_text(first_pages(_pdf("only page"), 3)).split() == ["only", "page"]


def test_render_regions_returns_a_png_per_region_scaled_to_the_dpi() -> None:
    pngs = render_regions(_pdf("one", "two"), [(1, (0, 0, 72, 36)), (2, (0, 0, 144, 72))], dpi=144)
    sizes = [Image.open(io.BytesIO(png)).size for png in pngs]
    # 144 dpi is twice the PDF's 72 points per inch
    assert sizes == [(144, 72), (288, 144)]


def test_region_text_reads_only_the_words_inside_each_region() -> None:
    # _pdf writes its text at (72, 72), i.e. the baseline sits one inch from the top
    texts = region_text(_pdf("inside"), [(1, (60, 50, 200, 80)), (1, (300, 300, 400, 400))])
    assert texts == ["inside", ""]


def test_draw_boxes_outlines_and_labels_each_box_on_its_page() -> None:
    boxes = [
        BoxAnnotation(1, (100, 100, 300, 250), "line_chart", (1.0, 0.0, 0.0), 0.6),
        BoxAnnotation(2, (100, 100, 300, 250), "Fig. 3 · bar_chart", (0.0, 0.6, 0.2), 2.0),
    ]
    annotated = draw_boxes(_pdf("one", "two"), boxes)
    with pymupdf.open(stream=annotated, filetype="pdf") as document:
        assert document.page_count == 2
        assert all(page.get_drawings() for page in document)
        assert "line_chart" in document[0].get_text()
        assert "Fig. 3" in document[1].get_text()
