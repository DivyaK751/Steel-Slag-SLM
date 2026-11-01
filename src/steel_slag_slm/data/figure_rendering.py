"""Prepare figure crops in memory without converting PDFs or publishing artifacts."""

import logging
from math import isfinite

import pymupdf

from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.data.figure_cleanup import add_inner_captions, is_kept, prepare_figures
from steel_slag_slm.data.figure_detection import DetectedLayout
from steel_slag_slm.data.pdf_pages import region_text, render_regions
from steel_slag_slm.schemas import Figure, RenderedFigure

logger = logging.getLogger(__name__)


def _validate_regions(pdf_bytes: bytes, figures: list[Figure]) -> None:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        for figure in figures:
            if not 1 <= figure.page_number <= document.page_count:
                raise ValueError(f"Figure page {figure.page_number} is outside the source PDF")

            x0, y0, x1, y1 = figure.bbox
            if not all(isfinite(value) for value in figure.bbox) or x1 <= x0 or y1 <= y0:
                raise ValueError(f"Invalid figure bounding box: {figure.bbox!r}")

            if not document[figure.page_number - 1].rect.contains(pymupdf.Rect(*figure.bbox)):
                raise ValueError(
                    f"Figure bounding box {figure.bbox!r} is outside page {figure.page_number}"
                )


def render_figures(
    pdf_bytes: bytes,
    layout: DetectedLayout,
    settings: FigureExtractionSettings,
) -> tuple[RenderedFigure, ...]:
    """Return retained figure crops in reading order, recovering captions before filtering.

    Do not convert the PDF or write files. Reject invalid page regions rather than silently
    clipping a crop whose recorded provenance would not match its image.
    """
    if not pdf_bytes:
        raise ValueError("PDF bytes must not be empty")
    if settings.render_dpi <= 0:
        raise ValueError("render_dpi must be greater than zero")

    figures = prepare_figures(layout.figures, layout.captions, settings)
    _validate_regions(pdf_bytes, figures)

    regions = [(figure.page_number, figure.bbox) for figure in figures]
    texts = region_text(pdf_bytes, regions)
    recovered = add_inner_captions(figures, texts)
    kept = [
        (figure, text)
        for figure, text in zip(recovered, texts, strict=True)
        if is_kept(figure, settings)
    ]

    images = render_regions(
        pdf_bytes,
        [(figure.page_number, figure.bbox) for figure, _ in kept],
        settings.render_dpi,
    )

    rendered: list[RenderedFigure] = []
    for position, ((figure, text), png) in enumerate(zip(kept, images, strict=True), start=1):
        pixmap = pymupdf.Pixmap(png)
        rendered.append(
            RenderedFigure(
                figure=figure,
                file_name=f"figure_{position:02d}.png",
                width=int(pixmap.width),
                height=int(pixmap.height),
                render_dpi=settings.render_dpi,
                pdf_text=text,
                image_bytes=png,
            )
        )

    logger.info("Found %d figure regions; retained %d", len(layout.figures), len(rendered))
    return tuple(rendered)