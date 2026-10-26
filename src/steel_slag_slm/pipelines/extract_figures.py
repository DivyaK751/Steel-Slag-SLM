"""Extract the figures of local PDFs into one folder per paper: PNGs, JSON and an annotated PDF."""

import json
import logging
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf
from docling.document_converter import DocumentConverter
from docling.exceptions import ConversionError

from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.data.figure_cleanup import add_inner_captions, clean_figures
from steel_slag_slm.data.figure_detection import DetectedLayout, detect_figures
from steel_slag_slm.data.pdf_pages import BoxAnnotation, draw_boxes, region_text, render_regions
from steel_slag_slm.schemas import Figure

logger = logging.getLogger(__name__)

FIGURES_FILE_NAME = "figures.json"
ANNOTATED_FILE_NAME = "annotated.pdf"
# Colours on the annotated PDF: everything Docling found, its captions, and what was kept
_DETECTED_COLOR = (0.85, 0.15, 0.15)
_CAPTION_COLOR = (0.1, 0.3, 0.9)
_KEPT_COLOR = (0.0, 0.6, 0.2)
_THIN_LINE, _THICK_LINE = 0.6, 2.0


def collect_pdf_paths(paths: list[Path]) -> list[Path]:
    """Expand files and folders into a sorted list of PDF files, without duplicates."""
    pdf_paths: set[Path] = set()
    for path in paths:
        if path.is_dir():
            pdf_paths.update(path.glob("*.pdf"))
        elif path.suffix.lower() == ".pdf":
            pdf_paths.add(path)
    return sorted(pdf_paths)


def _figure_record(figure: Figure, file_name: str, pdf_text: str) -> dict[str, Any]:
    return {
        "file": file_name,
        "figure_number": figure.figure_number,
        "caption": figure.caption,
        "page": figure.page_number,
        "bbox": [round(value, 1) for value in figure.bbox],
        "figure_type": figure.figure_type,
        "type_confidence": round(figure.type_confidence, 3),
        # Words the PDF itself stores inside the figure, used later to check a model's reading
        "pdf_text": pdf_text,
    }


def figure_annotations(layout: DetectedLayout, kept: list[Figure]) -> list[BoxAnnotation]:
    """Boxes for the annotated PDF: every detected region and caption, then the kept figures."""
    boxes = [
        BoxAnnotation(f.page_number, f.bbox, f.figure_type or "region", _DETECTED_COLOR, _THIN_LINE)
        for f in layout.figures
    ]
    boxes += [
        BoxAnnotation(c.page_number, c.bbox, "caption", _CAPTION_COLOR, _THIN_LINE)
        for c in layout.captions
    ]
    boxes += [
        BoxAnnotation(
            f.page_number,
            f.bbox,
            f"Fig. {f.figure_number} · {f.figure_type}"
            if f.figure_number
            else f"kept · {f.figure_type}",
            _KEPT_COLOR,
            _THICK_LINE,
            label_at_bottom=True,
        )
        for f in kept
    ]
    return boxes


def _reset_dir(directory: Path) -> None:
    """Empty a paper's folder, so files from an earlier run can't pass for this run's output."""
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


def extract_paper_figures(
    pdf_bytes: bytes,
    paper_dir: Path,
    converter: DocumentConverter,
    settings: FigureExtractionSettings,
) -> int:
    """Write a paper's figures (PNGs, figures.json, annotated.pdf), returning how many were kept.

    The paper's folder is emptied first, so it always holds only this run's output.
    """
    layout = detect_figures(pdf_bytes, converter)
    figures = clean_figures(layout.figures, layout.captions, settings)
    regions = [(figure.page_number, figure.bbox) for figure in figures]
    images = render_regions(pdf_bytes, regions, settings.render_dpi)
    texts = region_text(pdf_bytes, regions)
    figures = add_inner_captions(figures, texts)

    _reset_dir(paper_dir)
    annotated = draw_boxes(pdf_bytes, figure_annotations(layout, figures))
    (paper_dir / ANNOTATED_FILE_NAME).write_bytes(annotated)
    records = []
    for position, (figure, png, text) in enumerate(
        zip(figures, images, texts, strict=True), start=1
    ):
        file_name = f"figure_{position:02d}.png"
        (paper_dir / file_name).write_bytes(png)
        records.append(_figure_record(figure, file_name, text))
    (paper_dir / FIGURES_FILE_NAME).write_text(json.dumps(records, indent=2, ensure_ascii=False))
    logger.info(
        "%s: %d figure regions found, %d kept", paper_dir.name, len(layout.figures), len(figures)
    )
    return len(figures)


def run_figure_extraction(
    pdf_paths: list[Path],
    output_dir: Path,
    converter: DocumentConverter,
    settings: FigureExtractionSettings,
) -> Counter[str]:
    """Extract figures from each PDF into output_dir/<file stem>/, continuing past bad PDFs.

    Returns:
        Counts of papers done and failed, and of figures kept.
    """
    outcomes: Counter[str] = Counter()
    for pdf_path in pdf_paths:
        try:
            kept = extract_paper_figures(
                pdf_path.read_bytes(), output_dir / pdf_path.stem, converter, settings
            )
        except (OSError, ValueError, ConversionError, pymupdf.FileDataError) as error:
            logger.warning("Skipping %s: %s", pdf_path.name, error)
            outcomes["papers_failed"] += 1
            continue
        outcomes["papers_done"] += 1
        outcomes["figures_kept"] += kept
    return outcomes
