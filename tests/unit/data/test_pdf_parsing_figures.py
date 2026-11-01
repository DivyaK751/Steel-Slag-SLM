from pathlib import Path
from unittest.mock import MagicMock

import pymupdf
import pytest
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.exceptions import ConversionError
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableCell, TableData
from docling_core.types.doc.labels import DocItemLabel

from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.data import pdf_parsing


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
        document.new_page(width=600, height=800)
        page = document.new_page(width=600, height=800)
        page.draw_rect(pymupdf.Rect(50, 100, 300, 300))
        page.insert_text((60, 150), "Age Strength")
        return bytes(document.tobytes())


def _provenance(page: int, bbox: tuple[float, float, float, float]) -> ProvenanceItem:
    return ProvenanceItem(
        page_no=page,
        bbox=BoundingBox(
            l=bbox[0],
            t=bbox[1],
            r=bbox[2],
            b=bbox[3],
            coord_origin=CoordOrigin.TOPLEFT,
        ),
        charspan=(0, 0),
    )


def _document() -> DoclingDocument:
    document = DoclingDocument(name="paper")

    for page in (1, 2):
        document.add_page(page_no=page, size=Size(width=600, height=800))

    document.add_text(
        label=DocItemLabel.TEXT,
        text="Sample text",
        prov=_provenance(1, (50, 50, 300, 80)),
    )
    table = document.add_table(
        data=TableData(
            num_rows=1,
            num_cols=1,
            table_cells=[
                TableCell(
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                    text="Strength",
                )
            ],
        ),
        prov=_provenance(1, (50, 400, 300, 500)),
    )
    table.prov.append(_provenance(2, (50, 400, 300, 500)))

    document.add_picture(prov=_provenance(2, (50, 100, 300, 300)))
    document.add_text(
        label=DocItemLabel.CAPTION,
        text="Fig. 1 Strength",
        prov=_provenance(2, (50, 310, 300, 330)),
    )
    return document


def test_parse_pdf_with_figures_converts_once_and_preserves_all_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    converter = MagicMock()
    converter.convert.return_value.status = ConversionStatus.SUCCESS
    converter.convert.return_value.document = document

    legacy_extractor = MagicMock(side_effect=AssertionError("must not extract XRefs"))
    monkeypatch.setattr(pdf_parsing, "_extract_embedded_images", legacy_extractor)

    parsed = pdf_parsing.parse_pdf_with_figures(_pdf(), converter, _settings())

    converter.convert.assert_called_once()
    legacy_extractor.assert_not_called()

    assert parsed.page_count == 2
    assert parsed.text_parser == "Docling"
    assert parsed.image_parser == "Docling+PyMuPDF"
    assert "Sample text" in parsed.markdown
    assert len(parsed.tables) == 1
    assert "Strength" in parsed.tables[0]

    text_block = next(block for block in parsed.content_blocks if block.label == "text")
    assert text_block.placements[0].bbox == (50.0, 50.0, 300.0, 80.0)
    assert text_block.placements[0].coordinate_origin == "TOPLEFT"

    table_block = next(block for block in parsed.content_blocks if block.kind == "table")
    assert table_block.text == parsed.tables[0]
    assert [placement.page_number for placement in table_block.placements] == [1, 2]

    assert parsed.images == ()
    assert len(parsed.figures) == 1
    assert parsed.figures[0].figure.page_number == 2
    assert parsed.figures[0].figure.bbox == (50.0, 100.0, 300.0, 300.0)

    caption = parsed.figures[0].figure.caption_block
    assert caption is not None
    assert caption.bbox == (50.0, 310.0, 300.0, 330.0)
    assert (parsed.figures[0].width, parsed.figures[0].height) == (250, 200)


@pytest.mark.parametrize(
    "status",
    [
        ConversionStatus.FAILURE,
        ConversionStatus.PARTIAL_SUCCESS,
        ConversionStatus.SKIPPED,
    ],
)
def test_parse_pdf_with_figures_rejects_incomplete_conversion(status: ConversionStatus) -> None:
    converter = MagicMock()
    converter.convert.return_value.status = status

    with pytest.raises(ConversionError, match="did not succeed"):
        pdf_parsing.parse_pdf_with_figures(_pdf(), converter, _settings())


def test_parse_pdf_with_figures_rejects_empty_pdf() -> None:
    converter = MagicMock()

    with pytest.raises(ValueError, match="must not be empty"):
        pdf_parsing.parse_pdf_with_figures(b"", converter, _settings())

    converter.convert.assert_not_called()


def test_parse_pdf_with_figures_accepts_no_figures() -> None:
    document = DoclingDocument(name="text-only")
    document.add_text(label=DocItemLabel.TEXT, text="Text only")

    converter = MagicMock()
    converter.convert.return_value.status = ConversionStatus.SUCCESS
    converter.convert.return_value.document = document

    parsed = pdf_parsing.parse_pdf_with_figures(_pdf(), converter, _settings())

    assert parsed.figures == ()
    assert "Text only" in parsed.markdown


@pytest.mark.parametrize("include_figures", [False, True])
def test_build_docling_converter_preserves_ocr_and_tables(
    monkeypatch: pytest.MonkeyPatch, include_figures: bool
) -> None:
    constructor = MagicMock()
    monkeypatch.setattr(pdf_parsing, "DocumentConverter", constructor)

    pdf_parsing.build_docling_converter(include_figures=include_figures)

    options = constructor.call_args.kwargs["format_options"][InputFormat.PDF].pipeline_options
    assert options.do_ocr is True
    assert options.do_table_structure is True
    assert options.do_picture_classification is include_figures
