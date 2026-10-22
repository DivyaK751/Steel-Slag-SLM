"""Embed the chunks of every parsed paper and save the search index to Drive."""

import logging
from collections import Counter
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
) -> Counter[str]:
    """Index each parsed paper that is not in the saved store yet.

    Papers already in the store are skipped, so new papers can be added to the index later
    without re-embedding the others. A paper whose parsed.json is unreadable is left out and
    retried on the next run.
    """
    if max_papers is not None and max_papers <= 0:
        raise ValueError("max_papers must be greater than zero")

    papers = parsed_index.load_index(drive_service, parsed_folder_id, parsing.index_file_name)
    store = embedding_store.load_store(
        drive_service, parsed_folder_id, settings.model, encoder.dimension
    )
    pending = [paper_id for paper_id in papers if paper_id not in store.paper_ids]

    outcomes: Counter[str] = Counter()
    outcomes["already_indexed"] = len(papers) - len(pending)
    if max_papers is not None:
        outcomes["deferred"] = max(0, len(pending) - max_papers)
        pending = pending[:max_papers]

    unsaved = 0
    for paper_id in pending:
        json_file_id = str(papers[paper_id]["json_file_id"])
        try:
            paper = parse_parsed_json(drive_storage.download_file(drive_service, json_file_id))
        except ValueError as error:
            logger.error("Skipping %s: %s", paper_id, error)
            outcomes["failed"] += 1
            continue

        chunks = build_chunks(paper, settings.chunk_max_chars, settings.table_max_chars)
        if not chunks:
            outcomes["empty"] += 1
            continue
        vectors = encoder.encode([passage_for_embedding(chunk) for chunk in chunks])
        store.add(chunks, vectors)
        outcomes["indexed"] += 1
        outcomes["chunks"] += len(chunks)
        logger.info("Indexed paper %s: %d chunks", paper_id, len(chunks))

        unsaved += 1
        if unsaved >= settings.papers_per_save:
            _save(drive_service, parsed_folder_id, settings.model, store)
            unsaved = 0

    if unsaved:
        _save(drive_service, parsed_folder_id, settings.model, store)
    logger.info("Index build complete: %s", dict(outcomes))
    return outcomes


def _save(service: Any, parsed_folder_id: str, model_name: str, store: VectorStore) -> None:
    embedding_store.save_store(service, parsed_folder_id, model_name, store)
    logger.info("Saved index with %d chunks", store.size)
