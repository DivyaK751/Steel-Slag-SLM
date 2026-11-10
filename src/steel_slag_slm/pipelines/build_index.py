"""Embed the chunks of every parsed paper and save the search index to Drive."""

import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from steel_slag_slm.config import EmbeddingSettings, PdfParsingSettings
from steel_slag_slm.data import drive_storage, embedding_store, parsed_index
from steel_slag_slm.data.parsed_document import parse_parsed_json
from steel_slag_slm.rag.chunking import build_chunks, passage_for_embedding
from steel_slag_slm.rag.embeddings import TextEncoder
from steel_slag_slm.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


def run_build_index(
    settings: EmbeddingSettings,
    parsing: PdfParsingSettings,
    drive_service: Any,
    parsed_folder_id: str,
    encoder: TextEncoder,
    max_papers: int | None = None,
    rebuild: bool = False,
    titles: Mapping[str, str] | None = None,
    store_folder_id: str | None = None,
) -> Counter[str]:
    """Index each parsed paper that is not in the saved store yet, or was parsed again since.

    Papers already in the store are skipped, so new papers can be added to the index later
    without re-embedding the others. A paper whose parse generation changed has its old chunks
    replaced. Chunking changes do not trigger this, so pass rebuild=True to re-chunk and
    re-embed every paper from scratch. A paper whose parsed.json is unreadable is left out and
    retried on the next run. Each chunk carries its paper's title from titles, when given.
    The store is kept under store_folder_id, or under the parsed folder when it is None.
    """
    titles = titles or {}
    store_folder = store_folder_id or parsed_folder_id
    if max_papers is not None and max_papers <= 0:
        raise ValueError("max_papers must be greater than zero")

    papers = parsed_index.load_index(drive_service, parsed_folder_id, parsing.index_file_name)
    if rebuild:
        store = VectorStore.empty(encoder.dimension)
    else:
        store = embedding_store.load_store(
            drive_service, store_folder, settings.model, encoder.dimension
        )
    indexable_papers = {
        paper_id: entry for paper_id, entry in papers.items() if entry.get("status") != "skipped"
    }
    indexed_generations = store.paper_generations
    pending = [
        paper_id
        for paper_id, entry in indexable_papers.items()
        if paper_id not in indexed_generations or _is_reparsed(entry, indexed_generations[paper_id])
    ]
    outcomes: Counter[str] = Counter()
    skipped_count = len(papers) - len(indexable_papers)
    if skipped_count:
        outcomes["skipped"] = skipped_count
    for paper_id, entry in papers.items():
        if entry.get("status") == "skipped":
            # Flagged on every run, so a paper missing from the search is never silent
            logger.warning(
                "Not indexed, parser skipped %s: %s",
                paper_id,
                entry.get("skip_reason", "no reason recorded"),
            )
    outcomes["already_indexed"] = len(indexable_papers) - len(pending)
    if max_papers is not None:
        outcomes["deferred"] = max(0, len(pending) - max_papers)
        pending = pending[:max_papers]
    # Old chunks of re-parsed papers go first; a paper that then fails is retried next run
    reparsed = {paper_id for paper_id in pending if paper_id in indexed_generations}
    if reparsed:
        store.remove_papers(reparsed)
        outcomes["reindexed"] = len(reparsed)

    unsaved = 0
    for paper_id in pending:
        json_file_id = str(indexable_papers[paper_id]["json_file_id"])
        try:
            paper = parse_parsed_json(drive_storage.download_file(drive_service, json_file_id))
        except ValueError as error:
            logger.error("Skipping %s: %s", paper_id, error)
            outcomes["failed"] += 1
            continue

        generation = str(indexable_papers[paper_id].get("parse_generation", ""))
        chunks = [
            replace(chunk, parse_generation=generation)
            for chunk in build_chunks(
                paper,
                settings.chunk_max_chars,
                settings.table_max_chars,
                titles.get(paper_id, ""),
            )
        ]
        if not chunks:
            logger.warning("Not indexed, %s produced no chunks", paper_id)
            outcomes["empty"] += 1
            continue
        vectors = encoder.encode([passage_for_embedding(chunk) for chunk in chunks])
        store.add(chunks, vectors)
        outcomes["indexed"] += 1
        outcomes["chunks"] += len(chunks)
        logger.info("Indexed paper %s: %d chunks", paper_id, len(chunks))

        unsaved += 1
        if unsaved >= settings.papers_per_save:
            _save(drive_service, store_folder, settings.model, store)
            unsaved = 0

    if unsaved:
        _save(drive_service, store_folder, settings.model, store)
    logger.info("Index build complete: %s", dict(outcomes))
    return outcomes


def _is_reparsed(entry: Mapping[str, object], indexed_generation: str) -> bool:
    # Papers parsed before generations existed have none, so they are never seen as re-parsed
    generation = entry.get("parse_generation")
    return isinstance(generation, str) and generation != indexed_generation


def _save(service: Any, parsed_folder_id: str, model_name: str, store: VectorStore) -> None:
    embedding_store.save_store(service, parsed_folder_id, model_name, store)
    logger.info("Saved index with %d chunks", store.size)
