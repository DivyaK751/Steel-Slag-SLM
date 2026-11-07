"""Parse PDF bytes into text, tables, embedded resources or rendered figure crops."""

from io import BytesIO
from typing import Literal

import pymupdf
from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.exceptions import ConversionError
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.text import TextItem

from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.data.figure_detection import detect_figures_in_document
from steel_slag_slm.data.figure_rendering import render_figures
from steel_slag_slm.schemas import (
    ContentPlacement,
    ImagePlacement,
    ParsedContentBlock,
    ParsedDocument,
    ParsedImage,
)


def build_docling_converter(*, include_figures: bool = False) -> DocumentConverter:
    """Create a text/table converter, optionally enabling figure classification."""
    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
        do_picture_classification=include_figures,
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def parse_pdf(pdf_bytes: bytes, converter: DocumentConverter) -> ParsedDocument:
    """Extract Markdown, tables, and unique embedded image resources from PDF bytes."""
    if not pdf_bytes:
        raise ValueError("PDF bytes must not be empty")

    document = converter.convert(
        DocumentStream(name="paper.pdf", stream=BytesIO(pdf_bytes))
    ).document
    markdown = document.export_to_markdown()
    content_blocks = _extract_content_blocks(document)
    tables = tuple(block.text for block in content_blocks if block.kind == "table")

    images = _extract_embedded_images(pdf_bytes)
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        page_count = pdf.page_count

    return ParsedDocument(
        text_parser="Docling",
        image_parser="PyMuPDF",
        page_count=page_count,
        markdown=markdown,
        tables=tables,
        content_blocks=content_blocks,
        images=images,
    )


def _extract_content_blocks(document: DoclingDocument) -> tuple[ParsedContentBlock, ...]:
    content_blocks: list[ParsedContentBlock] = []

    for item, _ in document.iterate_items():
        if isinstance(item, TableItem):
            block_text = item.export_to_markdown(document)
            kind: Literal["text", "table"] = "table"
        elif isinstance(item, TextItem):
            block_text = item.text
            kind = "text"
        else:
            continue

        content_placements = tuple(
            ContentPlacement(
                page_number=provenance.page_no,
                bbox=(
                    float(provenance.bbox.l),
                    float(provenance.bbox.t),
                    float(provenance.bbox.r),
                    float(provenance.bbox.b),
                ),
                coordinate_origin=provenance.bbox.coord_origin.value,
            )
            for provenance in item.prov
            # Docling sometimes gives empty or OCR-only lines a zero-width or zero-height box,
            # which can't locate anything; the block's text is kept either way
            if provenance.bbox.width > 0 and provenance.bbox.height > 0
        )
        content_blocks.append(
            ParsedContentBlock(
                kind=kind,
                label=item.label.value,
                text=block_text,
                placements=content_placements,
            )
        )

    return tuple(content_blocks)


def _extract_embedded_images(pdf_bytes: bytes) -> tuple[ParsedImage, ...]:
    placements_by_xref: dict[int, list[ImagePlacement]] = {}

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        for page in pdf:
            page_number = page.number + 1
            page_xrefs = {image_info[0] for image_info in page.get_images(full=True)}

            for xref in page_xrefs:
                placements = placements_by_xref.setdefault(xref, [])
                for rect in page.get_image_rects(xref):
                    placement = ImagePlacement(
                        page_number=page_number,
                        bbox=(
                            float(rect.x0),
                            float(rect.y0),
                            float(rect.x1),
                            float(rect.y1),
                        ),
                    )
                    if placement not in placements:
                        placements.append(placement)

        images = []
        for image_number, (xref, placements) in enumerate(placements_by_xref.items(), start=1):
            image = pdf.extract_image(xref)
            images.append(
                ParsedImage(
                    xref=xref,
                    file_name=f"image_{image_number:03d}.{image['ext']}",
                    width=int(image["width"]),
                    height=int(image["height"]),
                    placements=tuple(placements),
                    image_bytes=image["image"],
                )
            )

    return tuple(images)


def parse_pdf_with_figures(
    pdf_bytes: bytes,
    converter: DocumentConverter,
    settings: FigureExtractionSettings,
) -> ParsedDocument:
    """Parse text, tables and figure crops with exactly one Docling conversion.

    Build the converter with include_figures=True. Return crops in figures; leave the legacy
    embedded-resource images field empty. This entry point does not publish artifacts.
    """
    if not pdf_bytes:
        raise ValueError("PDF bytes must not be empty")

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        page_count = pdf.page_count

    result = converter.convert(DocumentStream(name="paper.pdf", stream=BytesIO(pdf_bytes)))
    if result.status != ConversionStatus.SUCCESS:
        raise ConversionError(f"Docling conversion did not succeed: {result.status.value}")

    document = result.document
    content_blocks = _extract_content_blocks(document)

    return ParsedDocument(
        text_parser="Docling",
        image_parser="Docling+PyMuPDF",
        page_count=page_count,
        markdown=document.export_to_markdown(),
        tables=tuple(block.text for block in content_blocks if block.kind == "table"),
        content_blocks=content_blocks,
        images=(),
        figures=render_figures(pdf_bytes, detect_figures_in_document(document), settings),
    )
