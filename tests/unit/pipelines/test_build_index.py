import json
from typing import Any

from steel_slag_slm.config import (
    EmbeddingSettings,
    PdfParsingSettings,
)
from steel_slag_slm.data import embedding_store
from steel_slag_slm.pipelines.build_index import run_build_index
from steel_slag_slm.rag.search import search_store
from steel_slag_slm.rag.vector_store import VectorStore

PARSED_FOLDER = "parsed-folder"


def _settings(papers_per_save: int = 10) -> EmbeddingSettings:
    return EmbeddingSettings(
        model="model-a",
        query_prefix="",
        chunk_max_chars=800,
        table_max_chars=1500,
        papers_per_save=papers_per_save,
        top_k=3,
    )


def _parsing() -> PdfParsingSettings:
    return PdfParsingSettings(
        source_table="project.dataset.papers",
        output_folder_name="_parsed",
        index_file_name="index.json",
    )


def _add_paper(drive, paper_id: str, markdown: str, raw: bytes | None = None) -> dict[str, object]:
    """Store one paper's parsed.json in Drive and return its index entry."""
    content = (
        raw or json.dumps({"openalex_id": paper_id, "markdown": markdown, "tables": []}).encode()
    )
    paper_folder = drive.ensure_subfolder(None, PARSED_FOLDER, paper_id)
    json_file_id = drive.write_file(None, paper_folder, "parsed.json", content, "application/json")
    return {"paper_folder_id": paper_folder, "json_file_id": json_file_id, "image_file_ids": []}


def _write_index(drive, papers: dict[str, dict[str, object]]) -> None:
    index = {"schema_version": 1, "papers": papers}
    drive.write_file(None, PARSED_FOLDER, "index.json", json.dumps(index).encode(), "")


def test_build_index_embeds_each_parsed_paper_and_saves_to_drive(fake_drive, keyword_encoder):
    _write_index(
        fake_drive,
        {
            "W1": _add_paper(fake_drive, "W1", "## Results\n\nFree lime drives volume change."),
            "W2": _add_paper(fake_drive, "W2", "## Intro\n\nBlended cement uses slag."),
        },
    )

    outcomes = run_build_index(_settings(), _parsing(), None, PARSED_FOLDER, keyword_encoder)

    assert outcomes["indexed"] == 2
    assert outcomes["chunks"] >= 2
    store = embedding_store.load_store(None, PARSED_FOLDER, "model-a", keyword_encoder.dimension)
    assert store.paper_ids == {"W1", "W2"}
    hits = search_store(store, keyword_encoder, "lime", 1, query_prefix="")
    assert hits[0].chunk.paper_id == "W1"


def test_build_index_skips_papers_already_in_the_store(fake_drive, keyword_encoder):
    _write_index(
        fake_drive,
        {"W1": _add_paper(fake_drive, "W1", "## Results\n\nFree lime.")},
    )
    run_build_index(_settings(), _parsing(), None, PARSED_FOLDER, keyword_encoder)
    encoded_before = len(keyword_encoder.seen)

    outcomes = run_build_index(_settings(), _parsing(), None, PARSED_FOLDER, keyword_encoder)

    assert outcomes["already_indexed"] == 1
    assert outcomes.get("indexed", 0) == 0
    assert len(keyword_encoder.seen) == encoded_before


def test_build_index_adds_only_new_papers_to_an_existing_store(fake_drive, keyword_encoder):
    _write_index(fake_drive, {"W1": _add_paper(fake_drive, "W1", "## A\n\nFree lime.")})
    run_build_index(_settings(), _parsing(), None, PARSED_FOLDER, keyword_encoder)

    _write_index(
        fake_drive,
        {
            "W1": _add_paper(fake_drive, "W1", "## A\n\nFree lime."),
            "W2": _add_paper(fake_drive, "W2", "## B\n\nSinter fluxing agent."),
        },
    )
    outcomes = run_build_index(_settings(), _parsing(), None, PARSED_FOLDER, keyword_encoder)

    assert outcomes["already_indexed"] == 1
    assert outcomes["indexed"] == 1
    store = embedding_store.load_store(None, PARSED_FOLDER, "model-a", keyword_encoder.dimension)
    assert store.paper_ids == {"W1", "W2"}


def test_build_index_counts_unreadable_and_empty_papers(fake_drive, keyword_encoder):
    _write_index(
        fake_drive,
        {
            "W1": _add_paper(fake_drive, "W1", "", raw=b"{not json"),
            "W2": _add_paper(fake_drive, "W2", ""),
            "W3": _add_paper(fake_drive, "W3", "## A\n\nFree lime."),
        },
    )

    outcomes = run_build_index(_settings(), _parsing(), None, PARSED_FOLDER, keyword_encoder)

    assert outcomes["failed"] == 1
    assert outcomes["empty"] == 1
    assert outcomes["indexed"] == 1


def test_build_index_stops_after_max_papers(fake_drive, keyword_encoder):
    _write_index(
        fake_drive,
        {
            "W1": _add_paper(fake_drive, "W1", "## A\n\nFree lime."),
            "W2": _add_paper(fake_drive, "W2", "## B\n\nSinter."),
        },
    )

    outcomes = run_build_index(
        _settings(), _parsing(), None, PARSED_FOLDER, keyword_encoder, max_papers=1
    )

    assert outcomes["indexed"] == 1
    assert outcomes["deferred"] == 1


def test_build_index_saves_after_every_n_papers(fake_drive, keyword_encoder, monkeypatch):
    _write_index(
        fake_drive,
        {
            "W1": _add_paper(fake_drive, "W1", "## A\n\nFree lime."),
            "W2": _add_paper(fake_drive, "W2", "## B\n\nSinter."),
            "W3": _add_paper(fake_drive, "W3", "## C\n\nCement."),
        },
    )
    saves: list[int] = []
    real_save = embedding_store.save_store

    def counting_save(service: Any, folder: str, model: str, store: VectorStore) -> None:
        saves.append(store.size)
        real_save(service, folder, model, store)

    monkeypatch.setattr(embedding_store, "save_store", counting_save)

    run_build_index(_settings(papers_per_save=2), _parsing(), None, PARSED_FOLDER, keyword_encoder)

    assert len(saves) == 2  # after the second paper, and at the end for the third
