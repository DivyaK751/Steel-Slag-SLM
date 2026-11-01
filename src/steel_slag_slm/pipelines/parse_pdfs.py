"""Parse trial-paper PDFs and store their results in Drive."""

import json
import logging
import mimetypes
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.config import PdfParsingSettings
from steel_slag_slm.data import drive_storage, parsed_index, pdf_parsing, pdf_parsing_source
from steel_slag_slm.data.pdf_parsing_source import PdfSource
from steel_slag_slm.schemas import ParsedDocument

logger = logging.getLogger(__name__)

_PAPER_ID_PATTERN = re.compile(r"^W\d+$")
_CONTENT_SCHEMA_VERSION = 3


def _serialize_document(
    source: PdfSource,
    parsed: ParsedDocument,
    image_references: list[dict[str, object]],
) -> bytes:
    """Serialize parsed content and Drive image references without embedding image bytes."""
    record = {
        "openalex_id": source.openalex_id,
        "source_drive_link": source.drive_link,
        "content_schema_version": _CONTENT_SCHEMA_VERSION,
        "text_parser": parsed.text_parser,
        "image_parser": parsed.image_parser,
        "page_count": parsed.page_count,
        "markdown": parsed.markdown,
        "tables": list(parsed.tables),
        "content_blocks": [
            {
                "kind": block.kind,
                "label": block.label,
                "text": block.text,
                "placements": [
                    {
                        "page_number": placement.page_number,
                        "bbox": list(placement.bbox),
                        "coordinate_origin": placement.coordinate_origin,
                    }
                    for placement in block.placements
                ],
            }
            for block in parsed.content_blocks
        ],
        "images": image_references,
    }
    return json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")


def _record_skipped_paper(
    source: PdfSource,
    reason: str,
    papers: dict[str, dict[str, str | int | list[str]]],
    drive_service: Any,
    output_folder_id: str,
    index_file_name: str,
) -> None:
    """Record a paper that should not be retried automatically."""
    papers[source.openalex_id] = {
        "status": "skipped",
        "skip_reason": reason,
        "source_drive_link": source.drive_link,
        "json_file_id": "",
        "image_file_ids": [],
    }
    index_bytes = json.dumps(
        {"schema_version": parsed_index.INDEX_SCHEMA_VERSION, "papers": papers},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    drive_storage.write_file(
        drive_service,
        output_folder_id,
        index_file_name,
        index_bytes,
        "application/json",
    )
    logger.warning("Skipped paper %s: %s", source.openalex_id, reason)


def run_pdf_parsing(
    settings: PdfParsingSettings,
    bigquery_client: bigquery.Client,
    drive_service: Any,
    source_folder_id: str,
    max_papers: int | None = None,
    paper_ids: Sequence[str] | None = None,
) -> Counter[str]:
    """Parse unprocessed screened papers and save their artifacts and index to Drive.

    paper_ids limits the run to those papers; ones already parsed are still left alone.
    """
    if max_papers is not None and max_papers <= 0:
        raise ValueError("max_papers must be greater than zero")

    sources = pdf_parsing_source.list_pdf_sources(bigquery_client, settings.source_table)
    outcomes: Counter[str] = Counter()
    if paper_ids:
        requested = set(paper_ids)
        # Only papers in the source table have a Drive link to their PDF
        outcomes["not_in_source_table"] = len(requested - {s.openalex_id for s in sources})
        sources = [source for source in sources if source.openalex_id in requested]
    if not sources:
        return outcomes

    output_folder_id = drive_storage.ensure_subfolder(
        drive_service, source_folder_id, settings.output_folder_name
    )
    papers = parsed_index.load_index(drive_service, output_folder_id, settings.index_file_name)
    converter = None

    skipped_sources = [
        source
        for source in sources
        if papers.get(source.openalex_id, {}).get("status") == "skipped"
    ]
    pending_sources = [
        source
        for source in sources
        if papers.get(source.openalex_id, {}).get("status") != "skipped"
        and (
            not papers.get(source.openalex_id, {}).get("paper_folder_id")
            or not papers.get(source.openalex_id, {}).get("image_folder_id")
            or papers.get(source.openalex_id, {}).get("content_schema_version")
            != _CONTENT_SCHEMA_VERSION
        )
    ]
    if skipped_sources:
        outcomes["skipped"] = len(skipped_sources)
    outcomes["already_parsed"] = len(sources) - len(pending_sources) - len(skipped_sources)

    if max_papers is not None:
        outcomes["deferred"] = max(0, len(pending_sources) - max_papers)
        pending_sources = pending_sources[:max_papers]

    for source in pending_sources:
        if not _PAPER_ID_PATTERN.fullmatch(source.openalex_id):
            raise ValueError(f"Invalid OpenAlex paper ID: {source.openalex_id!r}")

        source_file_id = drive_storage.drive_file_id_from_link(source.drive_link)
        pdf_bytes = drive_storage.download_pdf(drive_service, source_file_id)

        if not pdf_bytes:
            _record_skipped_paper(
                source,
                "empty_pdf_file",
                papers,
                drive_service,
                output_folder_id,
                settings.index_file_name,
            )
            outcomes["skipped"] += 1
            continue

        if converter is None:
            converter = pdf_parsing.build_docling_converter()

        parsed = pdf_parsing.parse_pdf(pdf_bytes, converter)
        if not parsed.markdown.strip() and not parsed.tables and not parsed.content_blocks:
            _record_skipped_paper(
                source,
                "no_extracted_text_tables_or_content_blocks",
                papers,
                drive_service,
                output_folder_id,
                settings.index_file_name,
            )
            outcomes["skipped"] += 1
            continue

        paper_folder_id = drive_storage.ensure_subfolder(
            drive_service, output_folder_id, source.openalex_id
        )
        image_folder_id = drive_storage.ensure_subfolder(drive_service, paper_folder_id, "images")

        image_references: list[dict[str, object]] = []
        image_file_ids: list[str] = []
        for image in parsed.images:
            mime_type = mimetypes.guess_type(image.file_name)[0] or "application/octet-stream"
            image_file_id = drive_storage.write_file(
                drive_service,
                image_folder_id,
                image.file_name,
                image.image_bytes,
                mime_type,
            )
            image_file_ids.append(image_file_id)
            image_references.append(
                {
                    "xref": image.xref,
                    "file_name": image.file_name,
                    "file_id": image_file_id,
                    "width": image.width,
                    "height": image.height,
                    "placements": [
                        {"page_number": placement.page_number, "bbox": list(placement.bbox)}
                        for placement in image.placements
                    ],
                }
            )

        json_file_id = drive_storage.write_file(
            drive_service,
            paper_folder_id,
            "parsed.json",
            _serialize_document(source, parsed, image_references),
            "application/json",
        )

        papers[source.openalex_id] = {
            "source_drive_link": source.drive_link,
            "paper_folder_id": paper_folder_id,
            "json_file_id": json_file_id,
            "image_folder_id": image_folder_id,
            "image_file_ids": image_file_ids,
            "content_schema_version": _CONTENT_SCHEMA_VERSION,
        }

        index_bytes = json.dumps(
            {"schema_version": parsed_index.INDEX_SCHEMA_VERSION, "papers": papers},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        drive_storage.write_file(
            drive_service,
            output_folder_id,
            settings.index_file_name,
            index_bytes,
            "application/json",
        )
        outcomes["parsed"] += 1
        logger.info("Parsed paper %s", source.openalex_id)

    logger.info("PDF parsing complete: %s", dict(outcomes))
    return outcomes
