import json

import pytest

from steel_slag_slm.data import embedding_store
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk

CHUNKS = [
    Chunk("W1", "text", "Results", "Free lime drives volume change.", 0),
    Chunk("W2", "table", "Intro", "Table 1. Sinter composition", 1),
]


def _filled_store(encoder) -> VectorStore:
    store = VectorStore.empty(encoder.dimension)
    store.add(CHUNKS, encoder.encode([chunk.text for chunk in CHUNKS]))
    return store


def test_load_store_is_empty_before_anything_is_saved(fake_drive):
    store = embedding_store.load_store(None, "parsed", "model-a", dimension=4)
    assert store.size == 0
    assert store.dimension == 4


def test_save_then_load_keeps_chunks_and_vectors(fake_drive, keyword_encoder):
    embedding_store.save_store(None, "parsed", "model-a", _filled_store(keyword_encoder))

    loaded = embedding_store.load_store(None, "parsed", "model-a", keyword_encoder.dimension)

    assert loaded.chunks == CHUNKS
    assert loaded.paper_ids == {"W1", "W2"}
    query = keyword_encoder.encode(["lime"])[0]
    assert loaded.search(query, top_k=1)[0][1] == CHUNKS[0]


def test_load_refuses_an_index_built_with_another_model(fake_drive, keyword_encoder):
    embedding_store.save_store(None, "parsed", "model-a", _filled_store(keyword_encoder))

    with pytest.raises(ValueError, match="built with model-a"):
        embedding_store.load_store(None, "parsed", "model-b", keyword_encoder.dimension)


def test_save_removes_the_previous_index_file(fake_drive, keyword_encoder):
    store = _filled_store(keyword_encoder)
    embedding_store.save_store(None, "parsed", "model-a", store)
    embedding_store.save_store(None, "parsed", "model-a", store)

    store_folder = fake_drive.find_subfolder_id(None, "parsed", embedding_store.STORE_FOLDER_NAME)
    index_files = [
        name
        for name in fake_drive.list_file_names(None, store_folder)
        if name.startswith(embedding_store.INDEX_FILE_PREFIX)
    ]
    assert len(index_files) == 1


def test_load_reports_a_missing_index_file(fake_drive, keyword_encoder):
    embedding_store.save_store(None, "parsed", "model-a", _filled_store(keyword_encoder))
    store_folder = fake_drive.find_subfolder_id(None, "parsed", embedding_store.STORE_FOLDER_NAME)
    for name, file_id in fake_drive.list_file_names(None, store_folder).items():
        if name.startswith(embedding_store.INDEX_FILE_PREFIX):
            fake_drive.delete_file(None, file_id)

    with pytest.raises(ValueError, match="missing from Drive"):
        embedding_store.load_store(None, "parsed", "model-a", keyword_encoder.dimension)


def test_load_rejects_a_chunks_file_with_the_wrong_schema(fake_drive):
    store_folder = fake_drive.ensure_subfolder(None, "parsed", embedding_store.STORE_FOLDER_NAME)
    fake_drive.write_file(None, store_folder, "chunks.json", b'{"schema_version": 99}', "")

    with pytest.raises(ValueError, match="unsupported structure"):
        embedding_store.load_store(None, "parsed", "model-a", dimension=4)


def test_save_then_load_keeps_the_pages_of_each_chunk(fake_drive, keyword_encoder):
    chunks = [Chunk("W1", "text", "Results", "Free lime.", 0, pages=(3, 4))]
    store = VectorStore.empty(keyword_encoder.dimension)
    store.add(chunks, keyword_encoder.encode([chunk.text for chunk in chunks]))
    embedding_store.save_store(None, "parsed", "model-a", store)

    loaded = embedding_store.load_store(None, "parsed", "model-a", keyword_encoder.dimension)

    assert loaded.chunks[0].pages == (3, 4)


def test_chunks_saved_before_pages_existed_load_with_no_pages(fake_drive, keyword_encoder):
    embedding_store.save_store(None, "parsed", "model-a", _filled_store(keyword_encoder))
    store_folder = fake_drive.find_subfolder_id(None, "parsed", embedding_store.STORE_FOLDER_NAME)
    record = json.loads(fake_drive.read_file_bytes(None, store_folder, "chunks.json"))
    for entry in record["chunks"]:
        del entry["pages"]
    fake_drive.write_file(None, store_folder, "chunks.json", json.dumps(record).encode(), "")

    loaded = embedding_store.load_store(None, "parsed", "model-a", keyword_encoder.dimension)

    assert [chunk.pages for chunk in loaded.chunks] == [(), ()]
