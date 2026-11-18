"""Verify complete captured sources and measure conservative rendering coverage."""

import logging
from hashlib import sha256
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel

from steel_slag_slm.data.local_pdfs import select_local_pdfs
from steel_slag_slm.data.table_evidence import RenderingIssue, render_table_evidence
from steel_slag_slm.data.table_inspection import capture_tables
from steel_slag_slm.pipelines.capture_table_corpus import FullDocumentCapture

logger = logging.getLogger(__name__)


class TableCoverage(BaseModel):
    """Count candidate rows without treating parser output as reviewed evidence."""

    reference: str
    caption: str
    pages: list[int]
    native_rows: int
    native_columns: int
    candidate_row_indices: list[int]
    issues: list[RenderingIssue]


class PaperCoverage(BaseModel):
    """Link per-table coverage to exact snapshot bytes and the original source."""

    paper_id: str
    capture_filename: str
    capture_sha256: str
    source_sha256: str
    physical_pages: int
    tables: list[TableCoverage]


class CorpusCoverage(BaseModel):
    """Record inventory and structural coverage, not retrieval or extraction accuracy."""

    schema_version: Literal[1] = 1
    review_status: Literal["unreviewed"] = "unreviewed"
    docling_version: str
    docling_core_version: str
    pipeline_options_sha256: str
    papers: list[PaperCoverage]


def audit_table_corpus(
    paper_ids: list[str],
    pdf_dir: Path,
    captures_dir: Path,
    output_path: Path,
) -> CorpusCoverage:
    """Verify every selected snapshot/source before saving a new coverage report.

    Missing papers, changed sources, mixed parser settings and invalid native geometry fail
    explicitly. Candidate row counts describe structural eligibility only, before token
    budgeting or manual review. Reports never overwrite existing files.
    """
    if output_path.exists():
        raise FileExistsError(f"Coverage report already exists: {output_path}")
    sources = select_local_pdfs(paper_ids, pdf_dir)
    paths = [captures_dir / f"{paper_id}.json" for paper_id in paper_ids]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing full-document captures: {missing}")
    papers: list[PaperCoverage] = []
    signatures: set[tuple[str, str, str]] = set()
    for paper_id, source, path in zip(paper_ids, sources, paths, strict=True):
        content = path.read_bytes()
        capture = FullDocumentCapture.model_validate_json(content)
        source_bytes = source.read_bytes()
        with pymupdf.open(stream=source_bytes, filetype="pdf") as pdf:
            page_count = pdf.page_count
        if (
            capture.paper_id != paper_id
            or capture.source_filename != source.name
            or capture.source_sha256 != sha256(source_bytes).hexdigest()
            or capture.source_page_count != page_count
        ):
            raise ValueError(f"{paper_id}: capture does not match the selected source")
        signatures.add(
            (
                capture.docling_version,
                capture.docling_core_version,
                capture.pipeline_options_sha256,
            )
        )
        if len(signatures) != 1:
            raise ValueError("Selected captures have mixed parser versions or settings")
        tables: list[TableCoverage] = []
        for table in capture_tables(capture.document):
            pages = sorted({p.page_no for p in table.provenance})
            if any(page < 1 or page > page_count for page in pages):
                raise ValueError(f"{paper_id} {table.reference}: invalid table provenance")
            rendered = render_table_evidence(table)
            if rendered.issues:
                logger.warning("%s %s: %s", paper_id, table.reference, rendered.issues)
            tables.append(
                TableCoverage(
                    reference=table.reference,
                    caption=table.caption,
                    pages=pages,
                    native_rows=table.data.num_rows,
                    native_columns=table.data.num_cols,
                    candidate_row_indices=[row.row_index for row in rendered.rows],
                    issues=rendered.issues,
                )
            )
        if not tables:
            logger.warning("%s: no detected tables", paper_id)
        papers.append(
            PaperCoverage(
                paper_id=paper_id,
                capture_filename=path.name,
                capture_sha256=sha256(content).hexdigest(),
                source_sha256=capture.source_sha256,
                physical_pages=page_count,
                tables=tables,
            )
        )
    docling_version, core_version, options_hash = signatures.pop()
    report = CorpusCoverage(
        docling_version=docling_version,
        docling_core_version=core_version,
        pipeline_options_sha256=options_hash,
        papers=papers,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(report.model_dump_json(indent=2) + "\n")
    return report
