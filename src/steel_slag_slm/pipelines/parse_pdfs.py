"""Parse trial-paper PDFs and store their results in Drive."""

import json
import logging
import mimetypes
import re
from collections import Counter
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.config import PdfParsingSettings
from steel_slag_slm.data import drive_storage, pdf_parsing, pdf_parsing_source
from steel_slag_slm.data.pdf_parsing_source import PdfSource
from steel_slag_slm.schemas import ParsedDocument

logger = logging.getLogger(__name__)

_PAPER_ID_PATTERN = re.compile(r"^W\d+$")
_INDEX_SCHEMA_VERSION = 1


def _load_index(
    drive_service: Any,
    output_folder_id: str,
    index_file_name: str,
) -> dict[str, dict[str, str | list[str]]]:
    """Read and validate the existing Drive index."""
    index_text = drive_storage.read_text_file(drive_service, output_folder_id, index_file_name)
    if not index_text:
        return {}

    try:
        stored_index = json.loads(index_text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Drive index {index_file_name!r} is not valid JSON") from error

    if (
        not isinstance(stored_index, dict)
        or stored_index.get("schema_version") != _INDEX_SCHEMA_VERSION
        or not isinstance(stored_index.get("papers"), dict)
    ):
        raise ValueError(f"Drive index {index_file_name!r} has an unsupported structure")

    papers: dict[str, dict[str, str | list[str]]] = {}
    for paper_id, entry in stored_index["papers"].items():
        if not isinstance(paper_id, str) or not isinstance(entry, dict):
            raise ValueError(f"Drive index {index_file_name!r} contains an invalid paper entry")

        json_file_id = entry.get("json_file_id")
        image_file_ids = entry.get("image_file_ids")
        if (
            not isinstance(json_file_id, str)
            or not isinstance(image_file_ids, list)
            or not all(isinstance(file_id, str) for file_id in image_file_ids)
        ):
            raise ValueError(f"Drive index {index_file_name!r} contains an invalid paper entry")

        papers[paper_id] = {
            "json_file_id": json_file_id,
            "image_file_ids": image_file_ids,
        }

    return papers


def _serialize_document(
    source: PdfSource,
    parsed: ParsedDocument,
    image_references: list[dict[str, object]],
) -> bytes:
    """Serialize parsed content and Drive image references without embedding image bytes."""
    record = {
        "openalex_id": source.openalex_id,
        "source_drive_link": source.drive_link,
        "text_parser": parsed.text_parser,
        "image_parser": parsed.image_parser,
        "page_count": parsed.page_count,
        "markdown": parsed.markdown,
        "tables": list(parsed.tables),
        "images": image_references,
    }
    return json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")


def run_pdf_parsing(
    settings: PdfParsingSettings,
    bigquery_client: bigquery.Client,
    drive_service: Any,
    source_folder_id: str,
    max_papers: int | None = None,
) -> Counter[str]:
    """Parse unprocessed trial papers and save their artifacts and index to Drive."""
    if max_papers is not None and max_papers <= 0:
        raise ValueError("max_papers must be greater than zero")

    sources = pdf_parsing_source.list_pdf_sources(bigquery_client, settings.source_table)
    outcomes: Counter[str] = Counter()
    if not sources:
        return outcomes

    output_folder_id = drive_storage.ensure_subfolder(
        drive_service, source_folder_id, settings.output_folder_name
    )
    papers = _load_index(drive_service, output_folder_id, settings.index_file_name)
    converter = None

    pending_sources = [source for source in sources if source.openalex_id not in papers]
    outcomes["already_parsed"] = len(sources) - len(pending_sources)

    if max_papers is not None:
        outcomes["deferred"] = max(0, len(pending_sources) - max_papers)
        pending_sources = pending_sources[:max_papers]

    for source in pending_sources:
        if not _PAPER_ID_PATTERN.fullmatch(source.openalex_id):
            raise ValueError(f"Invalid OpenAlex paper ID: {source.openalex_id!r}")

        if converter is None:
            converter = pdf_parsing.build_docling_converter()

        source_file_id = drive_storage.drive_file_id_from_link(source.drive_link)
        pdf_bytes = drive_storage.download_pdf(drive_service, source_file_id)
        parsed = pdf_parsing.parse_pdf(pdf_bytes, converter)

        image_references: list[dict[str, object]] = []
        image_file_ids: list[str] = []
        for image in parsed.images:
            drive_file_name = f"{source.openalex_id}_{image.file_name}"
            mime_type = mimetypes.guess_type(drive_file_name)[0] or "application/octet-stream"
            image_file_id = drive_storage.write_file(
                drive_service,
                output_folder_id,
                drive_file_name,
                image.image_bytes,
                mime_type,
            )
            image_file_ids.append(image_file_id)
            image_references.append(
                {
                    "xref": image.xref,
                    "file_name": drive_file_name,
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
            output_folder_id,
            f"{source.openalex_id}.json",
            _serialize_document(source, parsed, image_references),
            "application/json",
        )

        papers[source.openalex_id] = {
            "json_file_id": json_file_id,
            "image_file_ids": image_file_ids,
        }
        index_bytes = json.dumps(
            {"schema_version": _INDEX_SCHEMA_VERSION, "papers": papers},
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