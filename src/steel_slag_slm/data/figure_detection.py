"""Find figure regions and "Fig. N" caption blocks in a PDF with Docling's layout model."""

from dataclasses import dataclass
from io import BytesIO
from typing import Any

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.text import TextItem

from steel_slag_slm.data.figure_cleanup import parse_figure_number
from steel_slag_slm.schemas import CaptionBlock, Figure, PageBox

CAPTION_LABEL = "caption"


@dataclass(frozen=True, slots=True)
class DetectedLayout:
    """Figure regions and caption blocks found in one PDF, before cleanup."""

    figures: list[Figure]
    captions: list[CaptionBlock]


def build_figure_converter() -> DocumentConverter:
    """Create a Docling converter that finds and classifies figures without OCR or tables."""
    # OCR and table structure don't affect where figures are, and skipping them is much faster
    pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=False,
        do_picture_classification=True,
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def detect_figures(pdf_bytes: bytes, converter: DocumentConverter) -> DetectedLayout:
    """Return every figure region and caption block in the PDF, in top-left page coordinates.

    Raises:
        ValueError: If the PDF bytes are empty.
    """
    if not pdf_bytes:
        raise ValueError("PDF bytes must not be empty")
    document = converter.convert(
        DocumentStream(name="paper.pdf", stream=BytesIO(pdf_bytes))
    ).document

    figures: list[Figure] = []
    captions: list[CaptionBlock] = []
    for item, _ in document.iterate_items():
        if isinstance(item, PictureItem):
            figure_type, confidence = top_figure_class(item)
            figures.extend(
                Figure(page_number, bbox, figure_type, confidence)
                for page_number, bbox in _placements(item, document)
            )
        # Body sentences such as "Figure 6 shows ..." are labelled text, not caption
        elif (
            isinstance(item, TextItem)
            and item.label.value == CAPTION_LABEL
            and (number := parse_figure_number(item.text))
        ):
            captions.extend(
                CaptionBlock(page_number, bbox, item.text.strip(), number)
                for page_number, bbox in _placements(item, document)
            )
    return DetectedLayout(figures=figures, captions=captions)


def _placements(item: Any, document: DoclingDocument) -> list[tuple[int, PageBox]]:
    """Each page an item sits on, with its box flipped to a top-left origin."""
    placements = []
    for provenance in item.prov:
        page_height = document.pages[provenance.page_no].size.height
        box = provenance.bbox.to_top_left_origin(page_height=page_height)
        placements.append(
            (provenance.page_no, (float(box.l), float(box.t), float(box.r), float(box.b)))
        )
    return placements


def top_figure_class(picture: Any) -> tuple[str, float]:
    """Return the classifier's most likely label and its confidence, or ("", 0.0) if none.

    Docling moved predictions from `annotations` to `meta.classification`; both are read so
    the code works on either side of that change.
    """
    meta = getattr(picture, "meta", None)
    classification = getattr(meta, "classification", None) if meta is not None else None
    predictions = list(getattr(classification, "predictions", None) or [])
    if not predictions:
        for annotation in getattr(picture, "annotations", None) or []:
            predictions.extend(getattr(annotation, "predicted_classes", None) or [])
    if not predictions:
        return "", 0.0
    best = max(predictions, key=lambda prediction: prediction.confidence)
    return str(best.class_name), float(best.confidence)
