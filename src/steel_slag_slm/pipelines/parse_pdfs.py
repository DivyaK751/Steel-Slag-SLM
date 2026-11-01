"""Parse screened PDFs and publish unified text, tables and figures to Drive."""

import json
import logging
import re
from collections import Counter
from typing import Any

import pymupdf
from docling.exceptions import ConversionError
from google.cloud import bigquery

from steel_slag_slm.config import FigureExtractionSettings, PdfParsingSettings
from steel_slag_slm.data import drive_storage, parsed_index, pdf_parsing, pdf_parsing_source
from steel_slag_slm.data.parsed_manifest import (
    CONTENT_SCHEMA_VERSION,
    ParsedManifest,
    parser_revision,
)
from steel_slag_slm.data.parsed_publication import publish_parse

logger = logging.getLogger(__name__)
_PAPER_ID_PATTERN = re.compile(r"^W\d+$")


def _write_index(
    service: Any,
    folder_id: str,
    filename: str,
    papers: dict[str, Any],
) -> None:
    content = json.dumps(
        {"schema_version": parsed_index.INDEX_SCHEMA_VERSION, "papers": papers},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    drive_storage.write_file(service, folder_id, filename, content, "application/json")


def run_pdf_parsing(
    settings: PdfParsingSettings,
    bigquery_client: bigquery.Client,
    drive_service: Any,
    source_folder_id: str,
    max_papers: int | None = None,
    *,
    figure_settings: FigureExtractionSettings,
    reparse: bool = False,
    retry_skipped: bool = False,
) -> Counter[str]:
    """Publish pending papers, recording parse failures while letting storage errors abort.

    Keep old generations for readers and rollback. The root index points to an immutable
    manifest; parsed.json is a browsing copy updated only after the index is committed.
    """
    if max_papers is not None and max_papers <= 0:
        raise ValueError("max_papers must be greater than zero")

    sources = pdf_parsing_source.list_pdf_sources(bigquery_client, settings.source_table)
    outcomes: Counter[str] = Counter()
    if not sources:
        return outcomes

    output_folder = drive_storage.ensure_subfolder(
        drive_service,
        source_folder_id,
        settings.output_folder_name,
    )
    papers = parsed_index.load_index(
        drive_service,
        output_folder,
        settings.index_file_name,
    )
    revision = parser_revision(figure_settings)
    pending = []

    for source in sources:
        entry = papers.get(source.openalex_id, {})

        if entry.get("status") == "skipped" and not retry_skipped:
            outcomes["skipped"] += 1
            continue

        complete = all(
            entry.get(key) for key in ("paper_folder_id", "image_folder_id", "json_file_id")
        )
        current = (
            entry.get("content_schema_version") == CONTENT_SCHEMA_VERSION
            and entry.get("parser_revision") == revision
            and entry.get("source_drive_link") == source.drive_link
            and entry.get("status") != "skipped"
        )

        if complete and current and not reparse:
            # Repair a browsing copy left stale by an interrupted post-index write.
            content = drive_storage.download_file(
                drive_service,
                str(entry["json_file_id"]),
            )
            manifest = ParsedManifest.model_validate_json(content)
            if manifest.openalex_id != source.openalex_id:
                raise ValueError("Indexed paper does not match its manifest")

            drive_storage.write_file(
                drive_service,
                str(entry["paper_folder_id"]),
                "parsed.json",
                content,
                "application/json",
            )
            outcomes["already_parsed"] += 1
        else:
            pending.append(source)

    if max_papers is not None:
        outcomes["deferred"] = max(0, len(pending) - max_papers)
        pending = pending[:max_papers]

    converter = None

    for source in pending:
        if not _PAPER_ID_PATTERN.fullmatch(source.openalex_id):
            raise ValueError(f"Invalid OpenAlex paper ID: {source.openalex_id!r}")

        source_id = drive_storage.drive_file_id_from_link(source.drive_link)
        pdf_bytes = drive_storage.download_pdf(drive_service, source_id)

        if not pdf_bytes:
            reason = "empty_pdf_file"
        else:
            if converter is None:
                converter = pdf_parsing.build_docling_converter(include_figures=True)

            try:
                parsed = pdf_parsing.parse_pdf_with_figures(
                    pdf_bytes,
                    converter,
                    figure_settings,
                )
            except (ConversionError, pymupdf.FileDataError, ValueError) as error:
                logger.error("Failed to parse %s: %s", source.openalex_id, error)
                outcomes["failed"] += 1
                continue

            if parsed.markdown.strip() or parsed.tables or parsed.content_blocks or parsed.figures:
                reason = ""
            else:
                reason = "no_extracted_content"

        if reason:
            if papers.get(source.openalex_id, {}).get("json_file_id"):
                logger.error(
                    "Reparse of %s has no content; preserving previous parse",
                    source.openalex_id,
                )
                outcomes["failed"] += 1
                continue

            papers[source.openalex_id] = {
                "status": "skipped",
                "skip_reason": reason,
                "source_drive_link": source.drive_link,
                "json_file_id": "",
                "image_file_ids": [],
            }
            _write_index(
                drive_service,
                output_folder,
                settings.index_file_name,
                papers,
            )
            logger.warning("Skipped %s: %s", source.openalex_id, reason)
            outcomes["skipped"] += 1
            continue

        paper_folder = drive_storage.ensure_subfolder(
            drive_service,
            output_folder,
            source.openalex_id,
        )
        image_folder = drive_storage.ensure_subfolder(
            drive_service,
            paper_folder,
            "images",
        )

        published = publish_parse(
            drive_service,
            paper_folder,
            image_folder,
            source,
            pdf_bytes,
            parsed,
            revision,
        )
        papers[source.openalex_id] = {
            "status": "parsed",
            "source_drive_link": source.drive_link,
            "paper_folder_id": paper_folder,
            "image_folder_id": image_folder,
            "json_file_id": published.json_file_id,
            "image_file_ids": published.image_file_ids,
            "content_schema_version": CONTENT_SCHEMA_VERSION,
            "parser_revision": revision,
            "parse_generation": published.manifest.parse_generation,
        }

        _write_index(
            drive_service,
            output_folder,
            settings.index_file_name,
            papers,
        )
        drive_storage.write_file(
            drive_service,
            paper_folder,
            "parsed.json",
            published.content,
            "application/json",
        )

        outcomes["parsed"] += 1
        logger.info(
            "Published %s with %d figures",
            source.openalex_id,
            len(parsed.figures),
        )

    logger.info("PDF parsing complete: %s", dict(outcomes))
    return outcomes
