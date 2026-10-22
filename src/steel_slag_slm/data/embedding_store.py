"""Save and load the search index in the `_parsed` folder on Drive.

The store is two files in a `_embeddings` subfolder. The FAISS index gets a new unique name on
every save, and chunks.json names the current one and is written last. A run that stops midway
leaves chunks.json pointing at the previous index, so the store is never half-updated.
"""

import json
import uuid
from typing import Any

from steel_slag_slm.data import drive_storage
from steel_slag_slm.rag.vector_store import VectorStore
from steel_slag_slm.schemas import Chunk

STORE_FOLDER_NAME = "_embeddings"
CHUNKS_FILE_NAME = "chunks.json"
INDEX_FILE_PREFIX = "index-"
INDEX_FILE_SUFFIX = ".faiss"
STORE_SCHEMA_VERSION = 1


def load_store(service: Any, parsed_folder_id: str, model_name: str, dimension: int) -> VectorStore:
    """Return the saved store, or an empty one when nothing has been indexed yet.

    Raises:
        ValueError: If the saved store was built with another model or is inconsistent.
    """
    store_folder_id = drive_storage.find_subfolder_id(service, parsed_folder_id, STORE_FOLDER_NAME)
    chunks_bytes = (
        drive_storage.read_file_bytes(service, store_folder_id, CHUNKS_FILE_NAME)
        if store_folder_id
        else None
    )
    if chunks_bytes is None:
        return VectorStore.empty(dimension)

    record = _parse_chunks_file(chunks_bytes)
    if record["model"] != model_name or record["dimension"] != dimension:
        raise ValueError(
            f"the saved index was built with {record['model']} ({record['dimension']} dims); "
            f"it cannot be searched with {model_name} ({dimension} dims)"
        )
    index_bytes = drive_storage.read_file_bytes(service, store_folder_id, record["index_file"])
    if index_bytes is None:
        raise ValueError(f"the saved index file {record['index_file']!r} is missing from Drive")
    return VectorStore.from_index_bytes(index_bytes, record["chunks"])


def save_store(service: Any, parsed_folder_id: str, model_name: str, store: VectorStore) -> None:
    """Write the store to Drive and remove index files that are no longer referenced."""
    store_folder_id = drive_storage.ensure_subfolder(service, parsed_folder_id, STORE_FOLDER_NAME)
    index_file = f"{INDEX_FILE_PREFIX}{uuid.uuid4().hex}{INDEX_FILE_SUFFIX}"
    drive_storage.write_file(
        service,
        store_folder_id,
        index_file,
        store.to_index_bytes(),
        "application/octet-stream",
    )
    chunks_bytes = _serialize_chunks_file(model_name, store, index_file)
    drive_storage.write_file(
        service, store_folder_id, CHUNKS_FILE_NAME, chunks_bytes, "application/json"
    )
    for name, file_id in drive_storage.list_file_names(service, store_folder_id).items():
        if name.startswith(INDEX_FILE_PREFIX) and name != index_file:
            drive_storage.delete_file(service, file_id)


def _serialize_chunks_file(model_name: str, store: VectorStore, index_file: str) -> bytes:
    record = {
        "schema_version": STORE_SCHEMA_VERSION,
        "model": model_name,
        "dimension": store.dimension,
        "index_file": index_file,
        "chunks": [
            {
                "paper_id": chunk.paper_id,
                "kind": chunk.kind,
                "section": chunk.section,
                "text": chunk.text,
                "position": chunk.position,
            }
            for chunk in store.chunks
        ],
    }
    return json.dumps(record, ensure_ascii=False).encode("utf-8")


def _parse_chunks_file(content: bytes) -> dict[str, Any]:
    """Validate chunks.json and turn its chunk entries back into Chunk records."""
    record = json.loads(content)
    if not isinstance(record, dict) or record.get("schema_version") != STORE_SCHEMA_VERSION:
        raise ValueError("chunks.json has an unsupported structure")
    if not isinstance(record.get("model"), str) or not isinstance(record.get("index_file"), str):
        raise ValueError("chunks.json is missing its model or index file name")
    if not isinstance(record.get("dimension"), int) or not isinstance(record.get("chunks"), list):
        raise ValueError("chunks.json is missing its dimension or chunks")
    record["chunks"] = [Chunk(**entry) for entry in record["chunks"]]
    return record
