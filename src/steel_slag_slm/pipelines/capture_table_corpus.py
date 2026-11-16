"""Cache complete local Docling documents for paired table-retrieval experiments."""

import json
import logging
from hashlib import sha256
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from typing import Literal

import pymupdf
from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter
from docling.exceptions import ConversionError
from docling_core.types.doc.document import DoclingDocument
from pydantic import BaseModel, Field, model_validator

from steel_slag_slm.data.local_pdfs import select_local_pdfs

logger = logging.getLogger(__name__)


class FullDocumentCapture(BaseModel):
    """Preserve native content from one successful full-PDF conversion, not a page pilot."""

    schema_version: Literal[1] = 1
    scope: Literal["full_document"] = "full_document"
    paper_id: str = Field(pattern=r"^W[0-9]+$")
    source_filename: str
    source_sha256: str
    source_page_count: int = Field(gt=0)
    docling_version: str
    docling_core_version: str
    pipeline_options_sha256: str
    document: DoclingDocument

    @model_validator(mode="after")
    def require_all_pages(self) -> "FullDocumentCapture":
        if set(self.document.pages) != set(range(1, self.source_page_count + 1)):
            raise ValueError("Full-document capture must contain every physical PDF page")
        return self


def capture_local_corpus(
    paper_ids: list[str],
    pdf_dir: Path,
    output_dir: Path,
    converter: DocumentConverter,
) -> list[Path]:
    """Capture each complete PDF once, resuming only validated matching cached reports.

    Resolve every requested source before converting. Source or parser-setting changes,
    invalid/partial cache files and failed conversions are explicit errors. Previously
    completed papers remain available after an error; no outputs are overwritten.
    """
    sources = select_local_pdfs(paper_ids, pdf_dir)
    options = converter.format_to_options[InputFormat.PDF].pipeline_options
    if not isinstance(options, PdfPipelineOptions):
        raise ValueError("Native PDF pipeline options are required")
    raw_options = options.model_dump(mode="json")
    # This native set otherwise serializes in process-dependent hash order.
    raw_options["chart_extraction_options"]["model_spec"]["supported_engines"].sort()
    options_hash = sha256(json.dumps(raw_options, sort_keys=True).encode()).hexdigest()
    versions = {
        "docling_version": version("docling"),
        "docling_core_version": version("docling-core"),
    }
    paths: list[Path] = []
    for paper_id, source in zip(paper_ids, sources, strict=True):
        content = source.read_bytes()
        source_hash = sha256(content).hexdigest()
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            page_count = pdf.page_count
        destination = output_dir / f"{paper_id}.json"
        if destination.exists():
            saved = FullDocumentCapture.model_validate_json(destination.read_bytes())
            expected = (
                paper_id,
                source.name,
                source_hash,
                page_count,
                options_hash,
                *versions.values(),
            )
            actual = (
                saved.paper_id,
                saved.source_filename,
                saved.source_sha256,
                saved.source_page_count,
                saved.pipeline_options_sha256,
                saved.docling_version,
                saved.docling_core_version,
            )
            if actual != expected:
                raise ValueError(
                    f"{paper_id}: cached source or parser settings changed; use a new directory"
                )
            logger.info("%s: reusing verified full-document capture", paper_id)
        else:
            logger.info("%s: capturing all %d physical pages", paper_id, page_count)
            result = converter.convert(DocumentStream(name=source.name, stream=BytesIO(content)))
            if result.status != ConversionStatus.SUCCESS:
                raise ConversionError(f"{paper_id}: conversion status {result.status.value}")
            report = FullDocumentCapture(
                paper_id=paper_id,
                source_filename=source.name,
                source_sha256=source_hash,
                source_page_count=page_count,
                pipeline_options_sha256=options_hash,
                document=result.document,
                docling_version=versions["docling_version"],
                docling_core_version=versions["docling_core_version"],
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as handle:
                handle.write(report.model_dump_json() + "\n")
        paths.append(destination)
    return paths
