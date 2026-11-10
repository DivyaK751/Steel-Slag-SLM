"""Command-line entry points."""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from google.adk.runners import Runner
from google.cloud import bigquery

from steel_slag_slm.agents.abstract_extractor import build_extractor_agent
from steel_slag_slm.agents.data_extractor import build_data_extractor_agent
from steel_slag_slm.agents.figure_reader import build_figure_reader_agent
from steel_slag_slm.agents.relevance_screener import build_screener_agent
from steel_slag_slm.agents.runner import build_runner
from steel_slag_slm.config import (
    RETRIEVAL_EVAL_PATH,
    ScreeningSettings,
    Settings,
    load_abstract_extraction_settings,
    load_data_extraction_settings,
    load_drive_connection_settings,
    load_embedding_settings,
    load_evaluation_settings,
    load_extraction_tables_settings,
    load_figure_extraction_settings,
    load_figure_reading_settings,
    load_gcp_settings,
    load_pdf_parsing_settings,
    load_retrieval_eval_set,
    load_screening_settings,
    load_settings,
)
from steel_slag_slm.data.drive_storage import (
    build_drive_service,
    find_subfolder_id,
    list_paper_titles,
)
from steel_slag_slm.data.embedding_store import load_store
from steel_slag_slm.data.extraction_export import export_workbook
from steel_slag_slm.data.extraction_tables import ensure_extraction_tables
from steel_slag_slm.data.figure_detection import build_figure_converter
from steel_slag_slm.data.openalex import OpenAlexClient
from steel_slag_slm.data.paper_registry import build_bigquery_client
from steel_slag_slm.evaluation.figure_checks import total_checks
from steel_slag_slm.exceptions import PapersNotFoundError
from steel_slag_slm.llm.prompts import PromptTemplate, load_prompt
from steel_slag_slm.pipelines.background_figure_reader import BackgroundFigureReader
from steel_slag_slm.pipelines.build_index import run_build_index
from steel_slag_slm.pipelines.evaluate_retrieval import (
    format_retrieval_report,
    run_corpus_eval,
    run_retrieval_eval,
)
from steel_slag_slm.pipelines.evaluate_screening import run_evaluation
from steel_slag_slm.pipelines.export_parsed import run_export_parsed
from steel_slag_slm.pipelines.extract_data import run_data_extraction
from steel_slag_slm.pipelines.extract_figures import collect_pdf_paths, run_figure_extraction
from steel_slag_slm.pipelines.fetch_papers import run_fetch
from steel_slag_slm.pipelines.fill_abstracts import run_fill_abstracts
from steel_slag_slm.pipelines.paper_selection import select_papers
from steel_slag_slm.pipelines.parse_pdfs import format_parse_summary, run_pdf_parsing
from steel_slag_slm.pipelines.read_figures import (
    list_paper_dirs,
    run_figure_reading,
    summarize_costs,
)
from steel_slag_slm.pipelines.screen_papers import run_screening
from steel_slag_slm.pipelines.search_index import run_search
from steel_slag_slm.pipelines.sync_registry import run_registry_sync
from steel_slag_slm.rag.embeddings import Embedder
from steel_slag_slm.rag.retrieval import CrossEncoderScorer, TwoStageRetriever
from steel_slag_slm.schemas import ChunkKind

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _build_openalex_client(settings: Settings) -> OpenAlexClient:
    return OpenAlexClient(
        settings.openalex_api_key.get_secret_value(), settings.contact_email, settings.fetch
    )


def _build_drive_service(settings: Settings) -> Any:
    return build_drive_service(
        settings.google_service_account_file,
        settings.google_oauth_client_file,
        settings.google_oauth_token_file,
    )


def _build_registry_client(settings: Settings) -> bigquery.Client | None:
    if not settings.gcp_project:
        return None
    return build_bigquery_client(settings.gcp_project, settings.gcp_credentials_file)


def _print_remaining_budget(client: OpenAlexClient) -> None:
    if client.remaining_budget_usd is not None:
        print(f"Remaining OpenAlex budget today: ${client.remaining_budget_usd:.3f}")


def _quiet_gemini_logs() -> None:
    # These log every Gemini request at INFO, which buries the per-paper results
    for noisy_logger in ("httpx", "google_genai", "google_adk"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def _build_screening(
    model: str | None = None,
) -> tuple[ScreeningSettings, PromptTemplate, Runner, bigquery.Client]:
    """Set up logging, settings, prompt, agent and BigQuery for the screening commands.

    model, if given, replaces the configured model for this run only.
    """
    _setup_logging()
    _quiet_gemini_logs()
    gcp = load_gcp_settings()
    settings = load_screening_settings()
    if model:
        settings = settings.model_copy(update={"model": model})
    if gcp.credentials_file:
        # Gemini uses ADC; on GitHub this variable otherwise points at the Drive sign-in file
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(gcp.credentials_file)
    prompt = load_prompt(settings.prompt_name)
    runner = build_runner(build_screener_agent(settings, prompt))
    return settings, prompt, runner, build_bigquery_client(gcp.project, gcp.credentials_file)


def _git_commit() -> str:
    """Short hash of the code being evaluated, marked if there are uncommitted changes."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit} (+ uncommitted changes)" if dirty else commit


def fetch_papers() -> None:
    """Download new relevant papers into the shared Drive folder."""
    parser = argparse.ArgumentParser(description=fetch_papers.__doc__)
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="stop after adding this many papers (default: run until the daily budget is used)",
    )
    args = parser.parse_args()
    _setup_logging()

    settings = load_settings()
    client = _build_openalex_client(settings)
    registry_client = _build_registry_client(settings)
    if registry_client is None:
        logger.warning("GOOGLE_CLOUD_PROJECT is not set; skipping the BigQuery registry")
    outcomes = run_fetch(
        settings, _build_drive_service(settings), client, args.max_papers, registry_client
    )

    print("Summary:", dict(outcomes))
    _print_remaining_budget(client)


def sync_registry() -> None:
    """Add a BigQuery registry row for every paper in Drive that doesn't have one yet."""
    argparse.ArgumentParser(description=sync_registry.__doc__).parse_args()
    _setup_logging()

    settings = load_settings()
    registry_client = _build_registry_client(settings)
    if registry_client is None:
        raise SystemExit("Set GOOGLE_CLOUD_PROJECT in .env to the project that holds BigQuery")
    client = _build_openalex_client(settings)
    outcomes = run_registry_sync(settings, _build_drive_service(settings), client, registry_client)

    print("Summary:", dict(outcomes))
    _print_remaining_budget(client)


def create_extraction_tables() -> None:
    """Create the BigQuery tables (one per input-workbook sheet) for extracted paper data."""
    argparse.ArgumentParser(description=create_extraction_tables.__doc__).parse_args()
    _setup_logging()
    gcp = load_gcp_settings()
    client = build_bigquery_client(gcp.project, gcp.credentials_file)
    for table_id in ensure_extraction_tables(client, load_extraction_tables_settings()):
        print("Ready:", table_id)


def export_extraction() -> None:
    """Export the nine extraction tables from BigQuery to one Excel workbook."""
    parser = argparse.ArgumentParser(description=export_extraction.__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="where to save the .xlsx (default: a timestamped file in reports/extraction/)",
    )
    args = parser.parse_args()
    _setup_logging()
    gcp = load_gcp_settings()
    settings = load_extraction_tables_settings()
    client = build_bigquery_client(gcp.project, gcp.credentials_file)
    output = args.output or (
        settings.export_dir / f"steel_slag_extraction_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    )
    row_counts = export_workbook(client, f"{client.project}.{settings.dataset}", output)
    for table, count in row_counts.items():
        print(f"{table:32} {count} rows")
    print("Saved:", output)


def screen_papers() -> None:
    """Fill the relevance columns of unscreened papers in BigQuery using Gemini."""
    parser = argparse.ArgumentParser(description=screen_papers.__doc__)
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="stop after screening this many papers (default: all unscreened papers)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log the decisions without writing them to BigQuery",
    )
    args = parser.parse_args()
    settings, prompt, runner, bq_client = _build_screening()
    outcomes = asyncio.run(
        run_screening(settings, bq_client, runner, prompt, args.max_papers, args.dry_run)
    )
    print("Summary:", dict(outcomes))


def evaluate_screening() -> None:
    """Screen the human-labelled papers and save an Excel report comparing the two."""
    parser = argparse.ArgumentParser(description=evaluate_screening.__doc__)
    parser.add_argument(
        "--model",
        default=None,
        help="Gemini model to evaluate, e.g. gemini-4-argon (default: the one in base.yaml)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="also save this run's answers in the labels table, replacing the previous run's",
    )
    args = parser.parse_args()
    settings, prompt, runner, bq_client = _build_screening(args.model)
    evaluation = load_evaluation_settings(settings.papers_table)
    run_info = {
        "Run at": datetime.now().isoformat(timespec="seconds"),
        "Model": settings.model,
        "Temperature": settings.temperature,
        "Prompt": f"{prompt.name} v{prompt.version}",
        "Git commit": _git_commit(),
    }
    path, metrics, updated = asyncio.run(
        run_evaluation(settings, evaluation, bq_client, runner, prompt, run_info, args.write)
    )
    accuracy = "n/a" if metrics.accuracy is None else f"{metrics.accuracy:.0%}"
    print(
        f"Agreed with humans on {metrics.matched}/{metrics.labelled} labelled papers ({accuracy})"
    )
    print(f"Report: {path}")
    if args.write:
        print(f"Updated {updated} rows in {evaluation.labels_table}")


def _build_background_reader(max_gemini_usd: float | None) -> BackgroundFigureReader:
    """Set up Gemini figure reading for parse-pdfs, capped at the given or configured budget."""
    _quiet_gemini_logs()
    settings = load_figure_reading_settings()
    prompt = load_prompt(settings.prompt_name)
    runner = build_runner(build_figure_reader_agent(settings, prompt))
    budget = max_gemini_usd if max_gemini_usd is not None else settings.max_run_usd
    return BackgroundFigureReader(runner, prompt, settings, budget)


def parse_pdfs() -> None:
    """Parse unprocessed screened-paper PDFs and save results to Drive."""
    parser = argparse.ArgumentParser(description=parse_pdfs.__doc__)
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="stop after parsing this many unindexed papers (default: all)",
    )
    parser.add_argument(
        "--reparse",
        action="store_true",
        help="reparse current successful papers",
    )
    parser.add_argument(
        "--retry-skipped",
        action="store_true",
        help="retry deliberately skipped papers",
    )
    parser.add_argument(
        "--output-folder",
        default=None,
        help="override the parsed Drive folder name",
    )
    parser.add_argument(
        "--paper-ids",
        nargs="+",
        default=None,
        help="parse only these OpenAlex IDs from the source table (default: every paper)",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="write only parsed.json; keep figure boxes and captions but upload no PNGs",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="papers parsed at once, each in its own process (default: 1)",
    )
    parser.add_argument(
        "--no-figure-reading",
        action="store_true",
        help="parse only; don't send figures to Gemini",
    )
    parser.add_argument(
        "--max-gemini-usd",
        type=float,
        default=None,
        help=(
            "stop calling Gemini once this run's estimated cost reaches this "
            "(default: figure_reading.max_run_usd in configs/base.yaml)"
        ),
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="don't update the folder's search index after parsing",
    )
    args = parser.parse_args()
    if args.max_papers is not None and args.max_papers <= 0:
        parser.error("--max-papers must be greater than zero")
    if args.workers <= 0:
        parser.error("--workers must be greater than zero")
    if args.max_gemini_usd is not None and args.max_gemini_usd <= 0:
        parser.error("--max-gemini-usd must be greater than zero")

    _setup_logging()
    parsing_settings = load_pdf_parsing_settings()

    if args.output_folder is not None:
        parsing_settings = parsing_settings.model_copy(
            update={"output_folder_name": args.output_folder}
        )
    drive_settings = load_drive_connection_settings()
    gcp_settings = load_gcp_settings()

    drive_service = build_drive_service(
        drive_settings.service_account_file,
        drive_settings.oauth_client_file,
        drive_settings.oauth_token_file,
    )
    bigquery_client = build_bigquery_client(
        gcp_settings.project,
        gcp_settings.credentials_file,
    )
    reader = None if args.no_figure_reading else _build_background_reader(args.max_gemini_usd)
    try:
        outcomes = run_pdf_parsing(
            parsing_settings,
            bigquery_client,
            drive_service,
            drive_settings.folder_id,
            args.max_papers,
            args.paper_ids,
            figure_settings=load_figure_extraction_settings(),
            reparse=args.reparse,
            retry_skipped=args.retry_skipped,
            store_images=not args.json_only,
            workers=args.workers,
            figure_reader=reader,
        )
    finally:
        if reader is not None:
            reader.close()
    print(format_parse_summary(outcomes, parsing_settings.output_folder_name))
    if reader is not None:
        print(f"Gemini cost of figure reading in this run: about ${reader.spent_usd:.2f}")
        if reader.stop_reason is not None:
            print(f"Figure reading stopped early: {reader.stop_reason}; rerun to continue")

    # Indexing right after parsing keeps search in step with the folder just parsed
    unindexed = 0
    parsed_folder_id = find_subfolder_id(
        drive_service, drive_settings.folder_id, parsing_settings.output_folder_name
    )
    if not args.skip_index and parsed_folder_id is not None:
        unindexed = _index_parsed_folder(drive_service, parsed_folder_id)
    if outcomes["failed"]:
        raise SystemExit(f"{outcomes['failed']} paper(s) failed; rerun the same command to retry")
    if unindexed:
        raise SystemExit(
            f"{unindexed} parsed paper(s) could not be indexed; see the warnings above"
        )


def export_parsed() -> None:
    """Export unified Drive parses locally for optional figure reading."""
    parser = argparse.ArgumentParser(description=export_parsed.__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--paper-ids", nargs="+", default=None)
    parser.add_argument("--max-papers", type=int, default=None)
    parser.add_argument("--output-folder", default=None)
    args = parser.parse_args()

    if args.max_papers is not None and args.max_papers <= 0:
        parser.error("--max-papers must be greater than zero")

    _setup_logging()
    settings = load_pdf_parsing_settings()
    if args.output_folder is not None:
        settings = settings.model_copy(update={"output_folder_name": args.output_folder})

    outcomes = run_export_parsed(
        settings,
        *_connect_to_parsed_folder(settings.output_folder_name),
        args.out_dir,
        args.paper_ids,
        args.max_papers,
    )
    print("Summary:", dict(outcomes))


def fill_abstracts() -> None:
    """Fill missing abstracts in BigQuery from the first pages of each paper's PDF."""
    parser = argparse.ArgumentParser(description=fill_abstracts.__doc__)
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="stop after trying this many papers (default: every paper missing an abstract)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log the extracted abstracts without writing them to BigQuery",
    )
    args = parser.parse_args()
    _setup_logging()
    _quiet_gemini_logs()

    gcp = load_gcp_settings()
    settings = load_abstract_extraction_settings()
    drive_settings = load_drive_connection_settings()
    drive_service = build_drive_service(
        drive_settings.service_account_file,
        drive_settings.oauth_client_file,
        drive_settings.oauth_token_file,
    )
    if gcp.credentials_file:
        # Gemini signs in through Application Default Credentials. Set only now: on GitHub
        # this variable would otherwise make Drive use the service account instead of the
        # saved login that can see the folder
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(gcp.credentials_file)
    prompt = load_prompt(settings.prompt_name)
    runner = build_runner(build_extractor_agent(settings, prompt))
    outcomes = asyncio.run(
        run_fill_abstracts(
            settings,
            build_bigquery_client(gcp.project, gcp.credentials_file),
            drive_service,
            runner,
            prompt,
            args.max_papers,
            args.dry_run,
        )
    )
    print("Summary:", dict(outcomes))


def _connect_to_parsed_folder(folder_name: str | None = None) -> tuple[Any, str]:
    """Connect to the configured parsed Drive folder, optionally overriding its name."""

    drive_settings = load_drive_connection_settings()
    drive_service = build_drive_service(
        drive_settings.service_account_file,
        drive_settings.oauth_client_file,
        drive_settings.oauth_token_file,
    )
    parsed_folder_id = find_subfolder_id(
        drive_service,
        drive_settings.folder_id,
        folder_name if folder_name is not None else load_pdf_parsing_settings().output_folder_name,
    )
    if parsed_folder_id is None:
        raise SystemExit("Configured parsed folder is missing in Drive; run parse-pdfs first")
    return drive_service, parsed_folder_id


def _add_parsed_folder_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--parsed-folder",
        default=None,
        help="parsed Drive folder whose search index to use (default: the configured one)",
    )


def extract_data() -> None:
    """Extract workbook data values from parsed papers into the BigQuery sheet tables."""
    parser = argparse.ArgumentParser(description=extract_data.__doc__)
    parser.add_argument(
        "--paper-ids",
        nargs="+",
        default=None,
        help="OpenAlex IDs to (re-)extract (default: every parsed paper not extracted yet)",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="stop after this many papers (default: no limit)",
    )
    args = parser.parse_args()
    if args.max_papers is not None and args.max_papers <= 0:
        parser.error("--max-papers must be greater than zero")
    _setup_logging()
    _quiet_gemini_logs()

    gcp = load_gcp_settings()
    settings = load_data_extraction_settings()
    drive_service, parsed_folder_id = _connect_to_parsed_folder()
    if gcp.credentials_file:
        # Set only after Drive signs in, as in fill-abstracts: Gemini uses ADC
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(gcp.credentials_file)
    prompt = load_prompt(settings.prompt_name)
    runner = build_runner(build_data_extractor_agent(settings, prompt))
    outcomes = asyncio.run(
        run_data_extraction(
            settings,
            load_pdf_parsing_settings(),
            build_bigquery_client(gcp.project, gcp.credentials_file),
            drive_service,
            parsed_folder_id,
            runner,
            prompt,
            args.paper_ids,
            args.max_papers,
        )
    )
    print("Summary:", dict(outcomes))


def build_index() -> None:
    """Embed every parsed paper and save the search index to its parsed Drive folder."""
    parser = argparse.ArgumentParser(description=build_index.__doc__)
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="stop after indexing this many papers (default: all unindexed papers)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="re-chunk and re-embed every paper, for example after a chunking change",
    )
    _add_parsed_folder_argument(parser)
    args = parser.parse_args()
    if args.max_papers is not None and args.max_papers <= 0:
        parser.error("--max-papers must be greater than zero")

    _setup_logging()
    unindexed = _index_parsed_folder(
        *_connect_to_parsed_folder(args.parsed_folder), args.max_papers, args.rebuild
    )
    if unindexed:
        raise SystemExit(
            f"{unindexed} parsed paper(s) could not be indexed; see the warnings above"
        )


def _index_parsed_folder(
    drive_service: Any,
    parsed_folder_id: str,
    max_papers: int | None = None,
    rebuild: bool = False,
) -> int:
    """Update the folder's search index, print its summary and return the papers left out."""
    settings = load_embedding_settings()
    # Titles come from the PDFs' Drive descriptions; tables and the reranker need them
    titles = list_paper_titles(drive_service, load_drive_connection_settings().folder_id)
    outcomes = run_build_index(
        settings,
        load_pdf_parsing_settings(),
        drive_service,
        parsed_folder_id,
        Embedder(settings.model),
        max_papers,
        rebuild,
        titles,
    )
    print("Index summary:", dict(outcomes))
    # Papers the parser skipped on purpose are expected; anything else missing counts
    return outcomes["failed"] + outcomes["empty"]


def search_index() -> None:
    """Search the saved index for passages that match a query."""
    parser = argparse.ArgumentParser(description=search_index.__doc__)
    parser.add_argument("query", help="what to look for, in plain language")
    parser.add_argument("--top-k", type=int, default=None, help="how many results to show")
    parser.add_argument(
        "--kind",
        action="append",
        choices=["text", "table", "figure"],
        help="only show this kind of chunk; repeat to allow several",
    )
    _add_parsed_folder_argument(parser)
    args = parser.parse_args()

    _setup_logging()
    settings = load_embedding_settings()
    kinds = cast(set[ChunkKind], set(args.kind)) if args.kind else None
    drive_service, parsed_folder_id = _connect_to_parsed_folder(args.parsed_folder)
    hits = run_search(
        settings,
        drive_service,
        parsed_folder_id,
        Embedder(settings.model),
        CrossEncoderScorer(settings.reranker),
        args.query,
        args.top_k,
        kinds,
    )
    titles = list_paper_titles(drive_service, load_drive_connection_settings().folder_id)
    for rank, hit in enumerate(hits, start=1):
        chunk = hit.chunk
        title = titles.get(chunk.paper_id) or "(title unknown)"
        pages = f" · pages {', '.join(str(page) for page in chunk.pages)}" if chunk.pages else ""
        print(f"{rank}. [{hit.score:.3f}] {chunk.paper_id} · {title}")
        print(f"   {chunk.kind} · {chunk.section}{pages}")
        print(textwrap.fill(chunk.text, width=100, initial_indent="   ", subsequent_indent="   "))
        print()


def evaluate_retrieval() -> None:
    """Score retrieval against a fixed test set (default configs/retrieval_eval.yaml).

    With --papers-dir the test set's own papers are indexed in memory from local parsed.json
    files; otherwise the parsed folder's saved index in Drive is searched.
    """
    parser = argparse.ArgumentParser(description=evaluate_retrieval.__doc__)
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=RETRIEVAL_EVAL_PATH,
        help="test set YAML, e.g. configs/retrieval_eval_heldout.yaml",
    )
    parser.add_argument(
        "--papers-dir",
        type=Path,
        default=None,
        help="folder of <paper id>.json files; search only the papers the test set lists",
    )
    parser.add_argument(
        "--titles",
        type=Path,
        default=None,
        help="JSON file mapping paper IDs to titles, used with --papers-dir",
    )
    _add_parsed_folder_argument(parser)
    args = parser.parse_args()
    _setup_logging()
    settings = load_embedding_settings()
    encoder = Embedder(settings.model)
    scorer = CrossEncoderScorer(settings.reranker)
    if args.papers_dir is not None:
        titles = json.loads(args.titles.read_text(encoding="utf-8")) if args.titles else {}
        eval_set, report = run_corpus_eval(
            args.eval_set, args.papers_dir, titles, settings, encoder, scorer
        )
    else:
        eval_set = load_retrieval_eval_set(args.eval_set)
        drive_service, parsed_folder_id = _connect_to_parsed_folder(args.parsed_folder)
        store = load_store(drive_service, parsed_folder_id, settings.model, encoder.dimension)
        retriever = TwoStageRetriever(
            store, encoder, scorer, settings.query_prefix, settings.top_papers, settings.candidates
        )
        report = run_retrieval_eval(eval_set, retriever, store)
    print(format_retrieval_report(eval_set, report))


def extract_figures() -> None:
    """Extract the figures of local PDFs into PNGs plus a figures.json per paper."""
    settings = load_figure_extraction_settings()
    parser = argparse.ArgumentParser(description=extract_figures.__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="PDF files or folders of PDFs")
    parser.add_argument(
        "--paper-ids",
        nargs="+",
        default=None,
        help="only these papers, by OpenAlex ID (PDFs named e.g. W2114885577.pdf)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=settings.output_dir,
        help=f"where each paper's folder is written (default: {settings.output_dir})",
    )
    args = parser.parse_args()
    try:
        pdf_paths = select_papers(collect_pdf_paths(args.paths), args.paper_ids)
    except PapersNotFoundError as error:
        parser.error(str(error))
    if not pdf_paths:
        parser.error("no PDF files found in the given paths")

    _setup_logging()
    outcomes = run_figure_extraction(pdf_paths, args.out_dir, build_figure_converter(), settings)
    print("Summary:", dict(outcomes))
    print(f"Figures written to {args.out_dir}")


def read_figures() -> None:
    """Read extracted figures with Gemini, check each reading, and report what it cost."""
    extraction = load_figure_extraction_settings()
    parser = argparse.ArgumentParser(description=read_figures.__doc__)
    parser.add_argument(
        "--figures-dir",
        type=Path,
        default=extraction.output_dir,
        help=(
            f"local output from export-parsed or extract-figures (default: {extraction.output_dir})"
        ),
    )
    parser.add_argument(
        "--paper-ids",
        nargs="+",
        default=None,
        help="only these papers, by OpenAlex ID (default: every extracted paper)",
    )
    parser.add_argument(
        "--max-papers", type=int, default=None, help="read only this many papers (default: all)"
    )
    parser.add_argument(
        "--project-papers",
        type=int,
        default=2000,
        help="number of papers to project the full-corpus cost for (default: 2000)",
    )
    args = parser.parse_args()
    try:
        paper_dirs = list_paper_dirs(args.figures_dir, args.max_papers, args.paper_ids)
    except PapersNotFoundError as error:
        parser.error(f"{error}; run export-parsed or extract-figures first")
    if not paper_dirs:
        parser.error(
            f"no parsed.json or figures.json found under {args.figures_dir}; "
            "run export-parsed or extract-figures first"
        )

    _setup_logging()
    _quiet_gemini_logs()
    load_gcp_settings()  # checks GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION, selects Vertex AI
    settings = load_figure_reading_settings()
    prompt = load_prompt(settings.prompt_name)
    runner = build_runner(build_figure_reader_agent(settings, prompt))
    results = asyncio.run(run_figure_reading(paper_dirs, runner, prompt, settings))

    cost = summarize_costs(results)
    checks = total_checks([result.check for result in results if result.check])
    lines = [
        f"Read {cost.figures_read} figures ({cost.figures_failed} failed) "
        f"from {cost.papers} papers with {settings.model}",
        f"Cost: total ${cost.total_usd:.4f} | per figure ${cost.mean_per_figure_usd:.4f} "
        f"(min ${cost.min_per_figure_usd:.4f}, max ${cost.max_per_figure_usd:.4f}) | "
        f"per paper ${cost.mean_per_paper_usd:.4f}",
        *(
            f"  {kind}: ${mean:.4f} per figure"
            for kind, mean in sorted(cost.mean_per_type_usd.items())
        ),
        f"Projected for {args.project_papers} papers: "
        f"${cost.mean_per_paper_usd * args.project_papers:.2f}",
    ]
    print("\n" + "\n".join(lines))
    print(
        f"Checks: {checks.labels_unsupported} of {checks.labels_checked} labels not in the PDF "
        f"text ({checks.figures_with_pdf_text} figures had text to check); "
        f"{checks.values_out_of_range} of {checks.values_checked} values outside their axes; "
        f"{checks.rows_malformed} rows with missing cells"
    )
    print(f"Each reading is saved beside its source image under {args.figures_dir}")
