"""Inspect selected local pages; never publish or modify production parses."""

import logging
from hashlib import sha256
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from typing import Literal

import pymupdf
from docling.datamodel.base_models import ConversionStatus, DocumentStream
from docling.document_converter import DocumentConverter
from docling.exceptions import ConversionError
from pydantic import BaseModel

from steel_slag_slm.config import TableInspectionCase
from steel_slag_slm.data.drive_storage import parse_openalex_id
from steel_slag_slm.data.table_inspection import InspectedTable, capture_tables

logger = logging.getLogger(__name__)


class TableInspectionReport(BaseModel):
    """Record the exact source, selected page and unmodified Docling table observations."""

    schema_version: Literal[1] = 1
    case: TableInspectionCase
    source_filename: str
    source_sha256: str
    source_page_count: int
    docling_version: str
    docling_core_version: str
    status: Literal["captured", "no_tables"]
    tables: list[InspectedTable]


def inspect_local_tables(
    cases: list[TableInspectionCase],
    pdf_dir: Path,
    output_dir: Path,
    converter: DocumentConverter,
) -> list[TableInspectionReport]:
    """Convert each selected page and save one JSON report per paper without overwriting.

    Page-limited conversion retains physical PDF numbering but may differ from full-paper
    layout recognition. Captured tables still require manual comparison with the source.
    """
    if not cases or len({case.paper_id for case in cases}) != len(cases):
        raise ValueError("Select at least one paper, with only one page per paper")
    sources: list[Path] = []
    for case in cases:
        matches = [
            path for path in pdf_dir.glob("*.pdf") if parse_openalex_id(path.name) == case.paper_id
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{case.paper_id}: expected one PDF in {pdf_dir}, found {len(matches)}"
            )
        if (output_dir / f"{case.paper_id}.json").exists():
            raise FileExistsError(f"Inspection already exists for {case.paper_id} in {output_dir}")
        sources.append(matches[0])

    output_dir.mkdir(parents=True, exist_ok=True)
    reports: list[TableInspectionReport] = []
    for case, source in zip(cases, sources, strict=True):
        content = source.read_bytes()
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            page_count = pdf.page_count
        if case.page > page_count:
            raise ValueError(f"{case.paper_id}: page {case.page} exceeds {page_count} pages")
        logger.info("Inspecting %s, physical PDF page %d", case.paper_id, case.page)
        result = converter.convert(
            DocumentStream(name=source.name, stream=BytesIO(content)),
            page_range=(case.page, case.page),
        )
        if result.status != ConversionStatus.SUCCESS:
            raise ConversionError(f"{case.paper_id}: conversion status {result.status.value}")
        tables = capture_tables(result.document)
        if not tables:
            logger.error("%s: no tables detected on page %d", case.paper_id, case.page)
        report = TableInspectionReport(
            case=case,
            source_filename=source.name,
            source_sha256=sha256(content).hexdigest(),
            source_page_count=page_count,
            docling_version=version("docling"),
            docling_core_version=version("docling-core"),
            status="captured" if tables else "no_tables",
            tables=tables,
        )
        with (output_dir / f"{case.paper_id}.json").open("x", encoding="utf-8") as destination:
            destination.write(report.model_dump_json(indent=2) + "\n")
        reports.append(report)
    return reports
