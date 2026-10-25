import io

import pymupdf
from PIL import Image

from steel_slag_slm.data.pdf_pages import first_pages, page_text, render_regions


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
