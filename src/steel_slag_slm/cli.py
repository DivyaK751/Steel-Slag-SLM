"""Command-line entry points."""

import argparse
import asyncio
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from google.adk.runners import Runner
from google.cloud import bigquery

from steel_slag_slm.agents.abstract_extractor import build_extractor_agent
from steel_slag_slm.agents.figure_reader import build_figure_reader_agent
from steel_slag_slm.agents.relevance_screener import build_screener_agent
from steel_slag_slm.agents.runner import build_runner
from steel_slag_slm.config import (
    ScreeningSettings,
    Settings,
    load_abstract_extraction_settings,
    load_drive_connection_settings,
    load_embedding_settings,
    load_evaluation_settings,
    load_figure_extraction_settings,
    load_figure_reading_settings,
    load_gcp_settings,
    load_pdf_parsing_settings,
    load_retrieval_eval_set,
    load_screening_settings,
    load_settings,
)
from steel_slag_slm.data.drive_storage import build_drive_service, find_subfolder_id
from steel_slag_slm.data.figure_detection import build_figure_converter
from steel_slag_slm.data.openalex import OpenAlexClient
from steel_slag_slm.data.paper_registry import build_bigquery_client
from steel_slag_slm.evaluation.figure_checks import total_checks
from steel_slag_slm.evaluation.retrieval_metrics import mean_scores
from steel_slag_slm.exceptions import PapersNotFoundError
from steel_slag_slm.llm.prompts import PromptTemplate, load_prompt
from steel_slag_slm.pipelines.build_index import run_build_index
from steel_slag_slm.pipelines.evaluate_retrieval import run_retrieval_eval
from steel_slag_slm.pipelines.evaluate_screening import run_evaluation
from steel_slag_slm.pipelines.extract_figures import collect_pdf_paths, run_figure_extraction
from steel_slag_slm.pipelines.fetch_papers import run_fetch
from steel_slag_slm.pipelines.fill_abstracts import run_fill_abstracts
from steel_slag_slm.pipelines.paper_selection import select_papers
from steel_slag_slm.pipelines.parse_pdfs import run_pdf_parsing
from steel_slag_slm.pipelines.read_figures import (
    list_paper_dirs,
    run_figure_reading,
    summarize_costs,
)
from steel_slag_slm.pipelines.screen_papers import run_screening
from steel_slag_slm.pipelines.search_index import run_search
from steel_slag_slm.pipelines.sync_registry import run_registry_sync
from steel_slag_slm.rag.embeddings import Embedder
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


def _build_screening() -> tuple[ScreeningSettings, PromptTemplate, Runner, bigquery.Client]:
    """Set up logging, settings, prompt, agent and BigQuery for the screening commands."""
    _setup_logging()
    _quiet_gemini_logs()
    gcp = load_gcp_settings()
    settings = load_screening_settings()
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
    argparse.ArgumentParser(description=evaluate_screening.__doc__).parse_args()
    settings, prompt, runner, bq_client = _build_screening()
    evaluation = load_evaluation_settings(settings.papers_table)
    run_info = {
        "Run at": datetime.now().isoformat(timespec="seconds"),
        "Model": settings.model,
        "Temperature": settings.temperature,
        "Prompt": f"{prompt.name} v{prompt.version}",
        "Git commit": _git_commit(),
    }
    path, metrics = asyncio.run(
        run_evaluation(settings, evaluation, bq_client, runner, prompt, run_info)
    )
    accuracy = "n/a" if metrics.accuracy is None else f"{metrics.accuracy:.0%}"
    print(
        f"Agreed with humans on {metrics.matched}/{metrics.labelled} labelled papers ({accuracy})"
    )
    print(f"Report: {path}")


def parse_pdfs() -> None:
    """Parse unprocessed screened-paper PDFs and save results to Drive."""
    parser = argparse.ArgumentParser(description=parse_pdfs.__doc__)
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="stop after parsing this many unindexed papers (default: all)",
    )
    args = parser.parse_args()
    if args.max_papers is not None and args.max_papers <= 0:
        parser.error("--max-papers must be greater than zero")

    _setup_logging()
    parsing_settings = load_pdf_parsing_settings()
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
    outcomes = run_pdf_parsing(
        parsing_settings,
        bigquery_client,
        drive_service,
        drive_settings.folder_id,
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


def _connect_to_parsed_folder() -> tuple[Any, str]:
    """Sign in to Drive and find the _parsed folder inside DRIVE_FOLDER_ID."""
    drive_settings = load_drive_connection_settings()
    drive_service = build_drive_service(
        drive_settings.service_account_file,
        drive_settings.oauth_client_file,
        drive_settings.oauth_token_file,
    )
    parsed_folder_id = find_subfolder_id(
        drive_service, drive_settings.folder_id, load_pdf_parsing_settings().output_folder_name
    )
    if parsed_folder_id is None:
        raise SystemExit("No _parsed folder in DRIVE_FOLDER_ID yet; run parse-pdfs first")
    return drive_service, parsed_folder_id


def build_index() -> None:
    """Embed every parsed paper and save the search index to the _parsed Drive folder."""
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
    args = parser.parse_args()
    if args.max_papers is not None and args.max_papers <= 0:
        parser.error("--max-papers must be greater than zero")

    _setup_logging()
    settings = load_embedding_settings()
    outcomes = run_build_index(
        settings,
        load_pdf_parsing_settings(),
        *_connect_to_parsed_folder(),
        Embedder(settings.model),
        args.max_papers,
        args.rebuild,
    )
    print("Summary:", dict(outcomes))
    # Papers the parser skipped on purpose are expected; anything else missing fails the run
    unindexed = outcomes["failed"] + outcomes["empty"]
    if unindexed:
        raise SystemExit(
            f"{unindexed} parsed paper(s) could not be indexed; see the warnings above"
        )


def search_index() -> None:
    """Search the saved index for passages that match a query."""
    parser = argparse.ArgumentParser(description=search_index.__doc__)
    parser.add_argument("query", help="what to look for, in plain language")
    parser.add_argument("--top-k", type=int, default=None, help="how many results to show")
    parser.add_argument(
        "--kind",
        action="append",
        choices=["text", "table", "figure_caption"],
        help="only show this kind of chunk; repeat to allow several",
    )
    args = parser.parse_args()

    _setup_logging()
    settings = load_embedding_settings()
    kinds = cast(set[ChunkKind], set(args.kind)) if args.kind else None
    hits = run_search(
        settings,
        *_connect_to_parsed_folder(),
        Embedder(settings.model),
        args.query,
        args.top_k,
        kinds,
    )
    for rank, hit in enumerate(hits, start=1):
        chunk = hit.chunk
        pages = f" · pages {', '.join(str(page) for page in chunk.pages)}" if chunk.pages else ""
        print(f"{rank}. [{hit.score:.3f}] {chunk.paper_id} · {chunk.kind} · {chunk.section}{pages}")
        print(f"   {' '.join(chunk.text.split())[:300]}")


def evaluate_retrieval() -> None:
    """Score the saved index against the fixed test set in configs/retrieval_eval.yaml."""
    argparse.ArgumentParser(description=evaluate_retrieval.__doc__).parse_args()
    _setup_logging()
    settings = load_embedding_settings()
    report = run_retrieval_eval(
        settings,
        load_retrieval_eval_set(),
        *_connect_to_parsed_folder(),
        Embedder(settings.model),
    )
    print(f"{'question':<10} hit@5 hit@20 rec@5 rec@20    RR ndcg@10")
    for s in report.scores:
        print(
            f"{s.question_id:<10} {s.hit_at_5:5.0f} {s.hit_at_20:6.0f} {s.recall_at_5:5.2f} "
            f"{s.recall_at_20:6.2f} {s.reciprocal_rank:5.2f} {s.ndcg_at_10:7.2f}"
        )
    means = mean_scores(report.scores)
    print(
        f"{'MEAN':<10} {means['hit_at_5']:5.2f} {means['hit_at_20']:6.2f} "
        f"{means['recall_at_5']:5.2f} {means['recall_at_20']:6.2f} "
        f"{means['reciprocal_rank']:5.2f} {means['ndcg_at_10']:7.2f}"
    )
    for question_id, stale in report.missing.items():
        for evidence in stale:
            print(
                f"STALE LABEL {question_id}: {evidence.paper_id} {evidence.kind} {evidence.text!r}"
            )


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
        help=f"folder written by extract-figures (default: {extraction.output_dir})",
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
        parser.error(f"{error}; run extract-figures on them first")
    if not paper_dirs:
        parser.error(f"no figures.json found under {args.figures_dir}; run extract-figures first")

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
    print(f"Each reading is saved beside its figure (figure_NN.json) in {args.figures_dir}")
