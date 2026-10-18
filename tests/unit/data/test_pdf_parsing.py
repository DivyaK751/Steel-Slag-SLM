from unittest.mock import MagicMock

import pymupdf
import pytest

from steel_slag_slm.data.pdf_parsing import parse_pdf


def _make_pdf_with_image() -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Synthetic parser test")

    pixmap = pymupdf.Pixmap(
        pymupdf.csRGB,
        pymupdf.IRect(0, 0, 2, 2),
        False,
    )
    pixmap.clear_with(255)
    page.insert_image(
        pymupdf.Rect(72, 100, 92, 120),
        stream=pixmap.tobytes("png"),
    )

    pdf_bytes = pdf.tobytes()
    pdf.close()
    return pdf_bytes


def test_parse_pdf_returns_text_and_image_provenance() -> None:
    document = MagicMock()
    document.export_to_markdown.return_value = "Parsed paper text"
    document.iterate_items.return_value = iter([])

    converter = MagicMock()
    converter.convert.return_value.document = document

    parsed = parse_pdf(_make_pdf_with_image(), converter)

    assert parsed.text_parser == "Docling"
    assert parsed.image_parser == "PyMuPDF"
    assert parsed.page_count == 1
    assert parsed.markdown == "Parsed paper text"
    assert parsed.tables == ()
    assert len(parsed.images) == 1

    image = parsed.images[0]
    assert image.file_name.startswith("image_001.")
    assert image.width == 2
    assert image.height == 2
    assert image.image_bytes
    assert len(image.placements) == 1
    assert image.placements[0].page_number == 1
    assert len(image.placements[0].bbox) == 4


def test_parse_pdf_rejects_empty_bytes() -> None:
    with pytest.raises(ValueError, match="PDF bytes must not be empty"):
        parse_pdf(b"", MagicMock())
