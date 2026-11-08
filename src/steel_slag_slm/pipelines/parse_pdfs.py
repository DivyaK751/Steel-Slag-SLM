"""Parse screened PDFs, read their figures with Gemini and publish everything to Drive."""

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
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

import torch
from docling.document_converter import DocumentConverter
from google.cloud import bigquery
from pydantic import ValidationError

from steel_slag_slm.config import FigureExtractionSettings, PdfParsingSettings
from steel_slag_slm.data import drive_storage, parsed_index, pdf_parsing, pdf_parsing_source
from steel_slag_slm.data.parsed_manifest import (
    CONTENT_SCHEMA_VERSION,
    FigureReference,
    ParsedManifest,
    parser_revision,
)
from steel_slag_slm.data.parsed_publication import publish_parse, publish_readings
from steel_slag_slm.data.pdf_pages import render_regions
from steel_slag_slm.data.pdf_parsing_source import PdfSource
from steel_slag_slm.pipelines.background_figure_reader import (
    BackgroundFigureReader,
    PaperReadings,
)
from steel_slag_slm.schemas import FigureReading, FigureToRead, ParsedDocument

logger = logging.getLogger(__name__)
_PAPER_ID_PATTERN = re.compile(r"^W\d+$")
# This many failures in a row means something shared is broken (network, Drive login)
MAX_FAILURE_STREAK = 10
# Papers waiting on Gemini at once; parsing pauses beyond this, so PDFs don't pile up in memory
READ_BACKLOG = 6
SCAN_PROGRESS_EVERY = 100
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


def _make_executor(workers: int, torch_threads: int, *, isolated: bool = False) -> Executor:
    """One worker parses in this process; more run as separate processes, each with its models.

    isolated forces separate processes even for one worker, so a crash can't take this one down.
    """
    if workers == 1 and not isolated:
        return ThreadPoolExecutor(1, initializer=_start_worker, initargs=(None,))
    return ProcessPoolExecutor(workers, initializer=_start_worker, initargs=(torch_threads,))


def _submit(
    pool: Executor, pdf_bytes: bytes, figure_settings: FigureExtractionSettings
) -> Future[ParsedDocument | None]:
    """Queue one PDF; if the pool already broke, return a future holding that error instead."""
    try:
        return pool.submit(_parse_pdf, pdf_bytes, figure_settings)
    except BrokenProcessPool as error:
        future: Future[ParsedDocument | None] = Future()
        future.set_exception(error)
        return future


def _parse_alone(
    pdf_bytes: bytes, figure_settings: FigureExtractionSettings, torch_threads: int
) -> Future[ParsedDocument | None]:
    """Parse one PDF in a fresh single-worker process, so a crash can only be this paper's."""
    with _make_executor(1, torch_threads, isolated=True) as solo:
        future = _submit(solo, pdf_bytes, figure_settings)
        wait([future])
    return future


def _crashed(future: Future[ParsedDocument | None]) -> bool:
    return future.done() and isinstance(future.exception(), BrokenProcessPool)


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
    readings: Sequence[FigureReading | None] | None = None,
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
        output.service, paper_folder, image_folder, source, pdf_bytes, parsed, revision, readings
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
    read = sum(reading is not None for reading in readings or ())
    logger.info(
        "Published %s with %d figures (%d read)", source.openalex_id, len(parsed.figures), read
    )
    return "parsed"


def _check_parse(
    future: Future[ParsedDocument | None], output: _OutputFolder, source: PdfSource
) -> ParsedDocument | str:
    """Return a usable parse, or index why the paper can't be parsed and return that outcome."""
    try:
        parsed = future.result()
    # Only reached for a paper parsed alone: it crashed its own worker (e.g. ran out of memory)
    except BrokenProcessPool:
        logger.error("Parsing %s crashed its worker", source.openalex_id)
        return _save_skip(output, source, "worker_crashed")
    # Docling and PyMuPDF raise many error types on broken PDFs; one bad paper can't stop the run
    except Exception as error:
        logger.error("Failed to parse %s: %s", source.openalex_id, error)
        return _save_skip(output, source, "parse_failed")

    if parsed is None:
        return _save_skip(output, source, "empty_pdf_file")
    if not (parsed.markdown.strip() or parsed.tables or parsed.content_blocks or parsed.figures):
        return _save_skip(output, source, "no_extracted_content")
    return parsed


def _figure_to_read(paper_id: str, image: FigureReference) -> FigureToRead:
    return FigureToRead(
        paper_id=paper_id,
        image_path=Path(image.file_name),  # only names the figure in logs; nothing is written
        figure_number=image.figure_number,
        caption=image.caption,
        pdf_text=image.pdf_text,
        detected_type=image.figure_type,
        figure_id=image.figure_id,
    )


def _render_saved_figures(pdf_bytes: bytes, images: Sequence[FigureReference]) -> list[bytes]:
    """Render figures again from their saved page box, as parsing first rendered them."""
    return [
        render_regions(
            pdf_bytes,
            [(image.placements[0].page_number, image.placements[0].bbox)],
            image.render_dpi,
        )[0]
        for image in images
    ]


@dataclass
class _Tally:
    """Outcome counts of a run, the papers it couldn't parse, and its current failure streak."""

    outcomes: Counter[str]
    not_parsed: dict[str, str] = field(default_factory=dict)  # paper ID -> why
    failure_streak: int = 0

    def add(self, paper_id: str, outcome: str, why: str = "") -> None:
        """Count an outcome, stopping the run if too many papers fail in a row."""
        self.outcomes[outcome] += 1
        if outcome in ("skipped", "failed"):
            self.not_parsed.setdefault(paper_id, why or outcome)
        self.failure_streak = self.failure_streak + 1 if outcome == "failed" else 0
        if self.failure_streak >= MAX_FAILURE_STREAK:
            raise RuntimeError(
                f"{self.failure_streak} papers failed in a row; check the network and Drive "
                "access, then rerun to resume"
            )


@dataclass(frozen=True, slots=True)
class _ReadJob:
    """A paper waiting on Gemini: a fresh parse to publish, or a saved manifest to fill in."""

    source: PdfSource
    pdf_bytes: bytes = b""
    parsed: ParsedDocument | None = None
    manifest: ParsedManifest | None = None
    positions: tuple[int, ...] = ()  # which manifest images are being read


@dataclass
class _Run:
    """What every step of one run shares: where it publishes, its tally and Gemini's queue."""

    output: _OutputFolder
    revision: str
    store_images: bool
    tally: _Tally
    reader: BackgroundFigureReader | None
    reading: dict[Future[PaperReadings], _ReadJob] = field(default_factory=dict)

    @property
    def can_read(self) -> bool:
        return self.reader is not None and self.reader.stop_reason is None

    def count(self, paper_id: str, outcome: str) -> None:
        if outcome == "skipped":
            why = f"skipped: {self.output.papers[paper_id]['skip_reason']}"
        else:
            why = "failed: reparse failed, previous parse kept"
        self.tally.add(paper_id, outcome, why)

    def publish(
        self,
        source: PdfSource,
        pdf_bytes: bytes,
        parsed: ParsedDocument,
        readings: Sequence[FigureReading | None] | None = None,
    ) -> str:
        try:
            return _save_parse(
                self.output, source, pdf_bytes, parsed, self.revision, self.store_images, readings
            )
        # The parse breaks a manifest rule; parsing again would give the same result
        except ValidationError as error:
            logger.error("Parse of %s is not a valid manifest: %s", source.openalex_id, error)
            return _save_skip(self.output, source, "invalid_manifest")


def _settle(
    run: _Run, future: Future[ParsedDocument | None], source: PdfSource, pdf_bytes: bytes
) -> None:
    """Handle one finished parse: skip it, publish it, or send its figures to Gemini first."""
    try:
        parsed = _check_parse(future, run.output, source)
        if isinstance(parsed, str):
            outcome = parsed
        elif parsed.figures and run.can_read and run.reader is not None:
            figures = [
                FigureToRead(
                    paper_id=source.openalex_id,
                    image_path=Path(item.file_name),
                    figure_number=item.figure.figure_number,
                    caption=item.figure.caption,
                    pdf_text=item.pdf_text,
                    detected_type=item.figure.figure_type,
                )
                for item in parsed.figures
            ]
            images = [item.image_bytes for item in parsed.figures]
            run.reading[run.reader.submit(figures, images)] = _ReadJob(source, pdf_bytes, parsed)
            return
        else:
            outcome = run.publish(source, pdf_bytes, parsed)
    # A Drive upload failed; the paper stays unindexed, so the next run retries it
    except Exception as error:
        logger.error("Could not save %s: %s", source.openalex_id, error)
        run.tally.add(source.openalex_id, "failed", "failed: could not save to Drive")
        return
    run.count(source.openalex_id, outcome)


def _start_filling(run: _Run, drive_service: Any, source: PdfSource, json_file_id: str) -> None:
    """Send the unread figures of an already-parsed paper to Gemini, without parsing it again."""
    if run.reader is None:
        return
    try:
        manifest = ParsedManifest.model_validate_json(
            drive_storage.download_file(drive_service, json_file_id)
        )
        positions = tuple(i for i, image in enumerate(manifest.images) if image.reading is None)
        unread = [manifest.images[position] for position in positions]
        images = _render_saved_figures(_download_pdf(drive_service, source), unread)
    # Unchanged on Drive, so the next run tries this paper again
    except Exception as error:
        logger.error("Could not prepare %s for figure reading: %s", source.openalex_id, error)
        run.tally.add(source.openalex_id, "failed", "failed: could not prepare figure reading")
        return
    changed = sum(
        sha256(png).hexdigest() != image.sha256 for png, image in zip(images, unread, strict=True)
    )
    if changed:
        # Still the same page region; only the pixels differ (e.g. a newer PyMuPDF)
        logger.warning(
            "%d re-rendered figures of %s differ from the parse", changed, source.openalex_id
        )
    figures = [_figure_to_read(source.openalex_id, image) for image in unread]
    job = _ReadJob(source, manifest=manifest, positions=positions)
    run.reading[run.reader.submit(figures, images)] = job


def _finish_reading(run: _Run, future: Future[PaperReadings], job: _ReadJob) -> None:
    """Publish a paper whose figures Gemini has read, counting what was and wasn't read."""
    try:
        readings = future.result().readings
    # The reader catches per-figure errors itself; anything else leaves the figures unread
    except Exception as error:
        logger.error("Figure reading for %s failed: %s", job.source.openalex_id, error)
        count = len(job.parsed.figures) if job.parsed else len(job.positions)
        readings = [None] * count

    paper_id = job.source.openalex_id
    try:
        if job.parsed is not None:
            outcome = run.publish(job.source, job.pdf_bytes, job.parsed, readings)
        elif job.manifest is not None:
            new = {
                position: reading
                for position, reading in zip(job.positions, readings, strict=True)
                if reading is not None
            }
            if new:
                paper_folder = run.output.papers[paper_id]["paper_folder_id"]
                publish_readings(run.output.service, str(paper_folder), job.manifest, new)
            logger.info("Read %d of %d figures of %s", len(new), len(job.positions), paper_id)
            outcome = "figures_added"
    except Exception as error:
        logger.error("Could not save %s: %s", paper_id, error)
        run.tally.add(paper_id, "failed", "failed: could not save to Drive")
        return

    if outcome in ("parsed", "figures_added"):
        read = sum(reading is not None for reading in readings)
        run.tally.outcomes["figures_read"] += read
        run.tally.outcomes["figures_unread"] += len(readings) - read
    run.count(paper_id, outcome)


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
    figure_reader: BackgroundFigureReader | None = None,
) -> Counter[str]:
    """Publish pending papers, parsing up to `workers` at once and continuing past failures.

    Each paper has one mutable parsed.json. Upload images before replacing the manifest,
    then update the root index after every paper, so a rerun resumes where this one stopped.
    Papers whose PDF can't be parsed are indexed as skipped (retry with retry_skipped);
    Drive errors leave the paper unindexed, so the next run tries it again. Only this
    process talks to Drive. Run only one publisher against an output folder.
    paper_ids limits the run to selected papers in the source table. With store_images
    False only parsed.json is written; figures keep their page, box and caption but no PNG.

    With a figure_reader, Gemini reads every figure before its paper is published, and
    papers parsed earlier get their unread figures read without being parsed again. A figure
    Gemini can't read is saved unread and retried by the next run.
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
    # Parsed papers with figures Gemini hasn't read yet: (source, ID of their parsed.json)
    unread: list[tuple[PdfSource, str]] = []

    logger.info("Checking which of %d papers still need parsing or figure reading", len(sources))
    for checked, source in enumerate(sources, start=1):
        # Checking a parsed paper downloads its parsed.json, so this takes a few minutes
        if checked % SCAN_PROGRESS_EVERY == 0:
            logger.info("Checked %d of %d papers", checked, len(sources))
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
            elif figure_reader is not None and any(i.reading is None for i in manifest.images):
                unread.append((source, str(entry["json_file_id"])))
            else:
                outcomes["already_parsed"] += 1
        else:
            pending.append(source)

    if max_papers is not None:
        outcomes["deferred"] = max(0, len(pending) + len(unread) - max_papers)
        pending = pending[:max_papers]
        unread = unread[: max_papers - len(pending)]

    output = _OutputFolder(drive_service, output_folder, settings.index_file_name, papers)
    run = _Run(output, revision, store_images, _Tally(outcomes), figure_reader)
    threads = settings.torch_threads_per_worker
    parse_queue = iter(pending)
    unread_queue = iter(unread)
    parsing: dict[Future[ParsedDocument | None], tuple[PdfSource, bytes]] = {}
    pool = _make_executor(workers, threads)

    try:
        while True:
            # Keep every worker busy plus one PDF waiting, without holding all PDFs in memory
            while (
                len(parsing) <= workers
                and len(run.reading) < READ_BACKLOG
                and (queued := next(parse_queue, None)) is not None
            ):
                try:
                    pdf_bytes = _download_pdf(drive_service, queued)
                # Unindexed, so the next run retries it
                except Exception as error:
                    logger.error("Could not download %s: %s", queued.openalex_id, error)
                    run.tally.add(
                        queued.openalex_id, "failed", "failed: could not download the PDF"
                    )
                    continue
                parsing[_submit(pool, pdf_bytes, figure_settings)] = (queued, pdf_bytes)

            while (
                run.can_read
                and len(run.reading) < READ_BACKLOG
                and (filling := next(unread_queue, None)) is not None
            ):
                _start_filling(run, drive_service, *filling)

            if not parsing and not run.reading:
                break

            in_flight: list[Future[Any]] = [*parsing, *run.reading]
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in [future for future in done if future in run.reading]:
                _finish_reading(run, future, run.reading.pop(future))
            parsed = [future for future in parsing if future in done]
            if not any(_crashed(future) for future in parsed):
                for future in parsed:
                    _settle(run, future, *parsing.pop(future))
                continue

            # A dead worker breaks the whole pool, and every unfinished paper with it. Reparse
            # those alone so the one that crashes is found and skipped, then start a new pool.
            wait(parsing)
            pool.shutdown(cancel_futures=True)
            suspects = [(future, *parsing[future]) for future in parsing]
            parsing.clear()
            logger.warning(
                "A parsing worker crashed; reparsing %d papers one at a time", len(suspects)
            )
            for future, source, pdf_bytes in suspects:
                if _crashed(future):
                    future = _parse_alone(pdf_bytes, figure_settings, threads)
                _settle(run, future, source, pdf_bytes)
            pool = _make_executor(workers, threads)
    finally:
        pool.shutdown(cancel_futures=True)

    outcomes["figures_waiting"] = sum(1 for _ in unread_queue)
    if figure_reader is not None and figure_reader.stop_reason is not None:
        logger.warning("Stopped reading figures: %s", figure_reader.stop_reason)
    if run.tally.not_parsed:
        listing = "\n".join(
            f"  {paper_id}  {why}" for paper_id, why in sorted(run.tally.not_parsed.items())
        )
        logger.warning("Not parsed in this run (%d):\n%s", len(run.tally.not_parsed), listing)
    return outcomes


# Each outcome's line in the end-of-run summary, in display order; zero counts are hidden
# except for the first three
_SUMMARY_LINES = {
    "parsed": "Parsed in this run",
    "skipped": "Skipped, PDF unusable (rerun with --retry-skipped to try again)",
    "failed": "Failed, retried automatically on the next run",
    "figures_added": "Parsed earlier, figures read by Gemini in this run",
    "already_parsed": "Already parsed by an earlier run",
    "deferred": "Not started yet (beyond --max-papers)",
    "not_in_source_table": "Requested IDs not in the approved list",
    "figures_waiting": "Parsed, figures not sent to Gemini yet (rerun to continue)",
    "figures_read": "Figures read by Gemini",
    "figures_unread": "Figures Gemini couldn't read (rerun to retry)",
}


def format_parse_summary(outcomes: Counter[str], output_folder_name: str) -> str:
    """Describe a parsing run's counts and where its output went, for the terminal."""
    lines = [f"Parsing summary (Drive folder {output_folder_name}/)"]
    for position, (key, label) in enumerate(_SUMMARY_LINES.items()):
        if position < 3 or outcomes[key]:
            lines.append(f"  {outcomes[key]:>5}  {label}")
    return "\n".join(lines)
