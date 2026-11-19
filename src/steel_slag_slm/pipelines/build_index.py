"""Embed the chunks of every parsed paper and save the search index to Drive."""

import hashlib
import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from steel_slag_slm.config import EmbeddingSettings, PdfParsingSettings
from steel_slag_slm.data import drive_storage, embedding_store, parsed_index
from steel_slag_slm.data.parsed_document import parse_parsed_json
from steel_slag_slm.rag.chunking import CHUNKING_VERSION, build_chunks, passage_for_embedding
from steel_slag_slm.rag.embeddings import TextEncoder
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk

logger = logging.getLogger(__name__)

PARSED_FILE_NAME = "parsed.json"


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
    """Bring the saved index up to date with the parsed papers, re-embedding only what changed.

    Each paper is indexed under a version made from its parsed.json checksum in Drive, the
    chunking settings and its title. A paper is chunked again when that version changes, so
    any rewrite of its parsed.json, whoever made it, or a chunking change is picked up. Its
    vectors are only recomputed when the chunks differ from the indexed ones. Papers removed
    from the parsed folder are dropped. rebuild=True re-embeds every paper from scratch.
    A paper whose parsed.json is unreadable is left out and retried on the next run. The store
    is kept under store_folder_id, or under the parsed folder when it is None.
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
    outcomes: Counter[str] = Counter()
    _warn_skipped(papers, outcomes)

    checksums = drive_storage.file_checksums(drive_service, PARSED_FILE_NAME)
    versions = {
        paper_id: _paper_version(
            checksums.get(str(entry["json_file_id"])) or str(entry.get("parse_generation", "")),
            settings,
            titles.get(paper_id, ""),
        )
        for paper_id, entry in indexable_papers.items()
    }
    indexed_versions = store.paper_generations
    removed = set(indexed_versions) - set(indexable_papers)
    if removed:
        store.remove_papers(removed)
        outcomes["removed"] = len(removed)
    pending = [
        paper_id
        for paper_id in indexable_papers
        if indexed_versions.get(paper_id) != versions[paper_id]
    ]
    outcomes["already_indexed"] = len(indexable_papers) - len(pending)
    if max_papers is not None:
        outcomes["deferred"] = max(0, len(pending) - max_papers)
        pending = pending[:max_papers]

    unsaved = 0
    for paper_id in pending:
        json_file_id = str(indexable_papers[paper_id]["json_file_id"])
        try:
            paper = parse_parsed_json(drive_storage.download_file(drive_service, json_file_id))
        except ValueError as error:
            logger.error("Skipping %s: %s", paper_id, error)
            outcomes["failed"] += 1
            continue

        chunks = [
            replace(chunk, parse_generation=versions[paper_id])
            for chunk in build_chunks(
                paper,
                settings.chunk_max_chars,
                settings.table_max_chars,
                titles.get(paper_id, ""),
                settings.table_rows,
            )
        ]
        was_indexed = paper_id in indexed_versions and paper_id not in removed
        if was_indexed and _same_chunks(store.paper_chunks(paper_id), chunks):
            # The file changed in a way the index does not see, such as an unused field
            store.relabel_paper(paper_id, versions[paper_id])
            outcomes["unchanged"] += 1
        else:
            if was_indexed:
                store.remove_papers({paper_id})
            if not chunks:
                logger.warning("Not indexed, %s produced no chunks", paper_id)
                outcomes["empty"] += 1
                continue
            vectors = encoder.encode([passage_for_embedding(chunk) for chunk in chunks])
            store.add(chunks, vectors)
            outcomes["reindexed" if was_indexed else "indexed"] += 1
            outcomes["chunks"] += len(chunks)
            logger.info("Indexed paper %s: %d chunks", paper_id, len(chunks))

        unsaved += 1
        if unsaved >= settings.papers_per_save:
            _save(drive_service, store_folder, settings.model, store)
            unsaved = 0

    if unsaved or removed:
        _save(drive_service, store_folder, settings.model, store)
    logger.info("Index build complete: %s", dict(outcomes))
    return outcomes


def _paper_version(file_version: str, settings: EmbeddingSettings, title: str) -> str:
    """Fingerprint everything a paper's chunks are built from."""
    parts = [
        file_version,
        CHUNKING_VERSION,
        str(settings.chunk_max_chars),
        str(settings.table_max_chars),
        str(settings.table_rows),
        title,
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def _same_chunks(indexed: list[Chunk], rebuilt: list[Chunk]) -> bool:
    def key(chunk: Chunk) -> tuple[object, ...]:
        return (
            chunk.kind,
            chunk.section,
            chunk.text,
            chunk.pages,
            chunk.figure_captions,
            chunk.paper_title,
        )

    return [key(chunk) for chunk in indexed] == [key(chunk) for chunk in rebuilt]


def _warn_skipped(papers: Mapping[str, Mapping[str, object]], outcomes: Counter[str]) -> None:
    for paper_id, entry in papers.items():
        if entry.get("status") == "skipped":
            # Flagged on every run, so a paper missing from the search is never silent
            logger.warning(
                "Not indexed, parser skipped %s: %s",
                paper_id,
                entry.get("skip_reason", "no reason recorded"),
            )
            outcomes["skipped"] += 1


def _save(service: Any, parsed_folder_id: str, model_name: str, store: VectorStore) -> None:
    embedding_store.save_store(service, parsed_folder_id, model_name, store)
    logger.info("Saved index with %d chunks", store.size)
