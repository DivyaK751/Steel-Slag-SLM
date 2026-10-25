"""Extract the figures of local PDFs into one folder per paper: PNG crops plus figures.json."""

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf
from docling.document_converter import DocumentConverter
from docling.exceptions import ConversionError

from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.data.figure_cleanup import clean_figures
from steel_slag_slm.data.figure_detection import detect_figures
from steel_slag_slm.data.pdf_pages import render_regions
from steel_slag_slm.schemas import Figure

logger = logging.getLogger(__name__)

FIGURES_FILE_NAME = "figures.json"


def collect_pdf_paths(paths: list[Path]) -> list[Path]:
    """Expand files and folders into a sorted list of PDF files, without duplicates."""
    pdf_paths: set[Path] = set()
    for path in paths:
        if path.is_dir():
            pdf_paths.update(path.glob("*.pdf"))
        elif path.suffix.lower() == ".pdf":
            pdf_paths.add(path)
    return sorted(pdf_paths)


def _figure_record(figure: Figure, file_name: str) -> dict[str, Any]:
    return {
        "file": file_name,
        "figure_number": figure.figure_number,
        "caption": figure.caption,
        "page": figure.page_number,
        "bbox": [round(value, 1) for value in figure.bbox],
        "figure_type": figure.figure_type,
        "type_confidence": round(figure.type_confidence, 3),
    }


def extract_paper_figures(
    pdf_bytes: bytes,
    paper_dir: Path,
    converter: DocumentConverter,
    settings: FigureExtractionSettings,
) -> int:
    """Write a paper's cleaned figures as PNGs plus figures.json, returning how many were kept."""
    layout = detect_figures(pdf_bytes, converter)
    figures = clean_figures(layout.figures, layout.captions, settings)
    images = render_regions(
        pdf_bytes, [(figure.page_number, figure.bbox) for figure in figures], settings.render_dpi
    )

    paper_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for position, (figure, png) in enumerate(zip(figures, images, strict=True), start=1):
        file_name = f"figure_{position:02d}.png"
        (paper_dir / file_name).write_bytes(png)
        records.append(_figure_record(figure, file_name))
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
