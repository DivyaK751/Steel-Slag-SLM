"""Parse PDF bytes into text, tables, and embedded image resources."""

from io import BytesIO
from typing import Literal

import pymupdf
from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import TableItem, TextItem

from steel_slag_slm.schemas import (
    ContentPlacement,
    ImagePlacement,
    ParsedContentBlock,
    ParsedDocument,
    ParsedImage,
)


def build_docling_converter() -> DocumentConverter:
    """Create a Docling converter configured for text and table extraction."""
    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
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
    tables: list[str] = []
    content_blocks: list[ParsedContentBlock] = []

    for item, _ in document.iterate_items():
        if isinstance(item, TableItem):
            block_text = item.export_to_markdown(document)
            kind: Literal["text", "table"] = "table"
            tables.append(block_text)
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
        )
        content_blocks.append(
            ParsedContentBlock(
                kind=kind,
                label=item.label.value,
                text=block_text,
                placements=content_placements,
            )
        )

    placements_by_xref: dict[int, list[ImagePlacement]] = {}
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        page_count = pdf.page_count
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

    return ParsedDocument(
        text_parser="Docling",
        image_parser="PyMuPDF",
        page_count=page_count,
        markdown=markdown,
        tables=tuple(tables),
        content_blocks=tuple(content_blocks),
        images=tuple(images),
    )
