"""Parse screened PDFs and publish unified text, tables and figures to Drive."""

import json
import logging
import re
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import (
    FIRST_COMPLETED,
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import Any

import torch
from docling.document_converter import DocumentConverter
from google.cloud import bigquery

from steel_slag_slm.config import FigureExtractionSettings, PdfParsingSettings
from steel_slag_slm.data import drive_storage, parsed_index, pdf_parsing, pdf_parsing_source
from steel_slag_slm.data.parsed_manifest import (
    CONTENT_SCHEMA_VERSION,
    ParsedManifest,
    parser_revision,
)
from steel_slag_slm.data.parsed_publication import publish_parse
from steel_slag_slm.data.pdf_parsing_source import PdfSource
from steel_slag_slm.schemas import ParsedDocument

logger = logging.getLogger(__name__)
_PAPER_ID_PATTERN = re.compile(r"^W\d+$")
# This many failures in a row means something shared is broken (network, Drive login)
MAX_FAILURE_STREAK = 10
# Each worker loads Docling's models once, in _start_worker, and reuses them for every paper
_worker_converter: DocumentConverter | None = None


@dataclass(frozen=True, slots=True)
class _OutputFolder:
    """The Drive folder a run publishes to, and the index it rewrites after every paper."""

    service: Any
    folder_id: str
    index_file_name: str
    papers: dict[str, Any]


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


def _start_worker(torch_threads: int | None) -> None:
    """Load Docling once per worker; cap torch threads so parallel workers share the cores."""
    global _worker_converter
    if torch_threads is not None:
        torch.set_num_threads(torch_threads)
    _worker_converter = pdf_parsing.build_docling_converter(include_figures=True)


def _parse_pdf(
    pdf_bytes: bytes, figure_settings: FigureExtractionSettings
) -> ParsedDocument | None:
    """Parse one PDF inside a worker; None means the downloaded file was empty."""
    if not pdf_bytes:
        return None
    if _worker_converter is None:
        raise RuntimeError("Parsing worker was started without a Docling converter")
    return pdf_parsing.parse_pdf_with_figures(pdf_bytes, _worker_converter, figure_settings)


def _make_executor(workers: int, torch_threads: int) -> Executor:
    """One worker parses in this process; more run as separate processes, each with its models."""
    if workers == 1:
        return ThreadPoolExecutor(1, initializer=_start_worker, initargs=(None,))
    return ProcessPoolExecutor(workers, initializer=_start_worker, initargs=(torch_threads,))


def _download_pdf(drive_service: Any, source: PdfSource) -> bytes:
    if not _PAPER_ID_PATTERN.fullmatch(source.openalex_id):
        raise ValueError(f"Invalid OpenAlex paper ID: {source.openalex_id!r}")
    source_id = drive_storage.drive_file_id_from_link(source.drive_link)
    return drive_storage.download_pdf(drive_service, source_id)


def _save_skip(output: _OutputFolder, source: PdfSource, reason: str) -> str:
    """Index a paper that can't be parsed so later runs skip it, unless it has a previous parse."""
    if output.papers.get(source.openalex_id, {}).get("json_file_id"):
        logger.error(
            "Reparse of %s failed (%s); preserving previous parse", source.openalex_id, reason
        )
        return "failed"

    output.papers[source.openalex_id] = {
        "status": "skipped",
        "skip_reason": reason,
        "source_drive_link": source.drive_link,
        "json_file_id": "",
        "image_file_ids": [],
    }
    _write_index(output.service, output.folder_id, output.index_file_name, output.papers)
    logger.warning("Skipped %s: %s", source.openalex_id, reason)
    return "skipped"


def _save_parse(
    output: _OutputFolder,
    source: PdfSource,
    pdf_bytes: bytes,
    parsed: ParsedDocument,
    revision: str,
    store_images: bool,
) -> str:
    """Publish one paper's manifest (and images if stored), then record it in the index."""
    paper_folder = drive_storage.ensure_subfolder(
        output.service, output.folder_id, source.openalex_id
    )
    image_folder = (
        drive_storage.ensure_subfolder(output.service, paper_folder, "images")
        if store_images
        else None
    )
    published = publish_parse(
        output.service, paper_folder, image_folder, source, pdf_bytes, parsed, revision
    )
    output.papers[source.openalex_id] = {
        "status": "parsed",
        "source_drive_link": source.drive_link,
        "paper_folder_id": paper_folder,
        "image_folder_id": image_folder or "",
        "json_file_id": published.json_file_id,
        "image_file_ids": published.image_file_ids,
        "content_schema_version": CONTENT_SCHEMA_VERSION,
        "parser_revision": revision,
        "parse_generation": published.manifest.parse_generation,
    }
    _write_index(output.service, output.folder_id, output.index_file_name, output.papers)
    logger.info("Published %s with %d figures", source.openalex_id, len(parsed.figures))
    return "parsed"


def _finish_paper(
    future: Future[ParsedDocument | None],
    output: _OutputFolder,
    source: PdfSource,
    pdf_bytes: bytes,
    revision: str,
    store_images: bool,
) -> str:
    """Save a finished parse, or index why the paper can't be parsed; return the outcome."""
    try:
        parsed = future.result()
    except BrokenProcessPool:
        raise
    # Docling and PyMuPDF raise many error types on broken PDFs; one bad paper can't stop the run
    except Exception as error:
        logger.error("Failed to parse %s: %s", source.openalex_id, error)
        return _save_skip(output, source, "parse_failed")

    if parsed is None:
        return _save_skip(output, source, "empty_pdf_file")
    if not (parsed.markdown.strip() or parsed.tables or parsed.content_blocks or parsed.figures):
        return _save_skip(output, source, "no_extracted_content")
    return _save_parse(output, source, pdf_bytes, parsed, revision, store_images)


def _tally(outcomes: Counter[str], outcome: str, failure_streak: int) -> int:
    """Count an outcome and return the new failure streak, stopping the run if it's too long."""
    outcomes[outcome] += 1
    failure_streak = failure_streak + 1 if outcome == "failed" else 0
    if failure_streak >= MAX_FAILURE_STREAK:
        raise RuntimeError(
            f"{failure_streak} papers failed in a row; check the network and Drive access, "
            "then rerun to resume"
        )
    return failure_streak


def run_pdf_parsing(
    settings: PdfParsingSettings,
    bigquery_client: bigquery.Client,
    drive_service: Any,
    source_folder_id: str,
    max_papers: int | None = None,
    paper_ids: Sequence[str] | None = None,
    *,
    figure_settings: FigureExtractionSettings,
    reparse: bool = False,
    retry_skipped: bool = False,
    store_images: bool = True,
    workers: int = 1,
) -> Counter[str]:
    """Publish pending papers, parsing up to `workers` at once and continuing past failures.

    Each paper has one mutable parsed.json. Upload images before replacing the manifest,
    then update the root index after every paper, so a rerun resumes where this one stopped.
    Papers whose PDF can't be parsed are indexed as skipped (retry with retry_skipped);
    Drive errors leave the paper unindexed, so the next run tries it again. Only this
    process talks to Drive. Run only one publisher against an output folder.
    paper_ids limits the run to selected papers in the source table. With store_images
    False only parsed.json is written; figures keep their page, box and caption but no PNG.
    """
    if max_papers is not None and max_papers <= 0:
        raise ValueError("max_papers must be greater than zero")
    if workers <= 0:
        raise ValueError("workers must be greater than zero")

    sources = pdf_parsing_source.list_pdf_sources(
        bigquery_client, settings.source_table, settings.relevant_labels
    )
    outcomes: Counter[str] = Counter()
    if paper_ids:
        requested = set(paper_ids)
        # Only papers in the source table have a Drive link to their PDF
        outcomes["not_in_source_table"] = len(requested - {s.openalex_id for s in sources})
        sources = [source for source in sources if source.openalex_id in requested]
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

        required: tuple[str, ...] = ("paper_folder_id", "json_file_id")
        # A JSON-only parse is incomplete for a run that stores images
        if store_images:
            required += ("image_folder_id",)
        complete = all(entry.get(key) for key in required)
        current = (
            entry.get("content_schema_version") == CONTENT_SCHEMA_VERSION
            and entry.get("parser_revision") == revision
            and entry.get("source_drive_link") == source.drive_link
            and entry.get("status") != "skipped"
        )

        if complete and current and not reparse:
            content = drive_storage.download_file(
                drive_service,
                str(entry["json_file_id"]),
            )
            manifest = ParsedManifest.model_validate_json(content)
            if manifest.openalex_id != source.openalex_id:
                raise ValueError("Indexed paper does not match its manifest")

            if (
                manifest.parse_generation != entry.get("parse_generation")
                or manifest.parser_revision != revision
                or manifest.source_drive_link != source.drive_link
            ):
                logger.warning(
                    "Manifest metadata differs from index for %s; scheduling reparse",
                    source.openalex_id,
                )
                pending.append(source)
            else:
                outcomes["already_parsed"] += 1
        else:
            pending.append(source)

    if max_papers is not None:
        outcomes["deferred"] = max(0, len(pending) - max_papers)
        pending = pending[:max_papers]

    output = _OutputFolder(drive_service, output_folder, settings.index_file_name, papers)
    queue = iter(pending)
    in_flight: dict[Future[ParsedDocument | None], tuple[PdfSource, bytes]] = {}
    failure_streak = 0
    not_parsed: dict[str, str] = {}  # paper ID -> why, listed at the end of the run

    with _make_executor(workers, settings.torch_threads_per_worker) as pool:
        while True:
            # Keep every worker busy plus one PDF waiting, without holding all PDFs in memory
            while len(in_flight) <= workers and (queued := next(queue, None)) is not None:
                try:
                    pdf_bytes = _download_pdf(drive_service, queued)
                # Unindexed, so the next run retries it
                except Exception as error:
                    logger.error("Could not download %s: %s", queued.openalex_id, error)
                    not_parsed[queued.openalex_id] = "failed: could not download the PDF"
                    failure_streak = _tally(outcomes, "failed", failure_streak)
                    continue
                future = pool.submit(_parse_pdf, pdf_bytes, figure_settings)
                in_flight[future] = (queued, pdf_bytes)

            if not in_flight:
                break

            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                source, pdf_bytes = in_flight.pop(future)
                try:
                    outcome = _finish_paper(
                        future, output, source, pdf_bytes, revision, store_images
                    )
                except BrokenProcessPool as error:
                    raise RuntimeError(
                        "A parsing worker crashed (often out of memory); "
                        "rerun with fewer --workers to resume"
                    ) from error
                # A Drive upload failed; the paper stays unindexed, so the next run retries it
                except Exception as error:
                    logger.error("Could not save %s: %s", source.openalex_id, error)
                    outcome = "failed"
                    not_parsed[source.openalex_id] = "failed: could not save to Drive"
                if outcome == "skipped":
                    reason = output.papers[source.openalex_id]["skip_reason"]
                    not_parsed[source.openalex_id] = f"skipped: {reason}"
                elif outcome == "failed":
                    not_parsed.setdefault(
                        source.openalex_id, "failed: reparse failed, previous parse kept"
                    )
                failure_streak = _tally(outcomes, outcome, failure_streak)

    if not_parsed:
        listing = "\n".join(f"  {paper_id}  {why}" for paper_id, why in sorted(not_parsed.items()))
        logger.warning("Not parsed in this run (%d):\n%s", len(not_parsed), listing)
    return outcomes


# Each outcome's line in the end-of-run summary, in display order; zero counts are hidden
# except for the first three
_SUMMARY_LINES = {
    "parsed": "Parsed in this run",
    "skipped": "Skipped, PDF unusable (rerun with --retry-skipped to try again)",
    "failed": "Failed, retried automatically on the next run",
    "already_parsed": "Already parsed by an earlier run",
    "deferred": "Not started yet (beyond --max-papers)",
    "not_in_source_table": "Requested IDs not in the approved list",
}


def format_parse_summary(outcomes: Counter[str], output_folder_name: str) -> str:
    """Describe a parsing run's counts and where its output went, for the terminal."""
    lines = [f"Parsing summary (Drive folder {output_folder_name}/)"]
    for position, (key, label) in enumerate(_SUMMARY_LINES.items()):
        if position < 3 or outcomes[key]:
            lines.append(f"  {outcomes[key]:>5}  {label}")
    return "\n".join(lines)
