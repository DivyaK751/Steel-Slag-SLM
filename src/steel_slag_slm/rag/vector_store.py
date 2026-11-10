"""A FAISS index that keeps the chunk behind each vector, so searches return text and metadata."""

import faiss
import numpy as np

from steel_slag_slm.schemas import Chunk


class VectorStore:
    """Inner-product FAISS index whose row i belongs to chunks[i]."""

    def __init__(self, index: faiss.Index, chunks: list[Chunk]) -> None:
        if index.ntotal != len(chunks):
            raise ValueError(
                f"index holds {index.ntotal} vectors but {len(chunks)} chunks were given"
            )
        self._index = index
        self._chunks = chunks

    @classmethod
    def empty(cls, dimension: int) -> "VectorStore":
        return cls(faiss.IndexFlatIP(dimension), [])

    @classmethod
    def from_index_bytes(cls, data: bytes, chunks: list[Chunk]) -> "VectorStore":
        """Rebuild a store from FAISS's serialized bytes and the chunks saved beside them."""
        index = faiss.deserialize_index(np.frombuffer(data, dtype=np.uint8))
        return cls(index, chunks)

    @property
    def dimension(self) -> int:
        return int(self._index.d)

    @property
    def chunks(self) -> list[Chunk]:
        return list(self._chunks)

    @property
    def size(self) -> int:
        return len(self._chunks)

    @property
    def paper_ids(self) -> set[str]:
        return {chunk.paper_id for chunk in self._chunks}

    @property
    def paper_generations(self) -> dict[str, str]:
        """Map each indexed paper to the parse generation its chunks came from."""
        return {chunk.paper_id: chunk.parse_generation for chunk in self._chunks}

    def remove_papers(self, paper_ids: set[str]) -> None:
        """Drop every chunk, and its vector, that belongs to one of the given papers."""
        keep = [row for row, chunk in enumerate(self._chunks) if chunk.paper_id not in paper_ids]
        if len(keep) == len(self._chunks):
            return
        # A flat index can't delete rows in place, so rebuild it from the rows that stay
        vectors = self._index.reconstruct_n(0, self._index.ntotal)
        index = faiss.IndexFlatIP(self.dimension)
        index.add(np.ascontiguousarray(vectors[keep], dtype=np.float32))
        self._index = index
        self._chunks = [self._chunks[row] for row in keep]

    def add(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        """Append chunks with their vectors, one row per chunk."""
        if vectors.shape != (len(chunks), self.dimension):
            raise ValueError(
                f"expected vectors of shape {(len(chunks), self.dimension)}, got {vectors.shape}"
            )
        if not chunks:
            return
        self._index.add(np.ascontiguousarray(vectors, dtype=np.float32))
        self._chunks.extend(chunks)

    def search(self, query_vector: np.ndarray, top_k: int) -> list[tuple[float, Chunk]]:
        """Return up to top_k (score, chunk) pairs, best first."""
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if self._index.ntotal == 0:
            return []
        query = np.ascontiguousarray(query_vector.reshape(1, -1), dtype=np.float32)
        scores, ids = self._index.search(query, min(top_k, self._index.ntotal))
        return [
            (float(score), self._chunks[int(row)])
            for score, row in zip(scores[0], ids[0], strict=True)
            if row >= 0
        ]

    def to_index_bytes(self) -> bytes:
        return faiss.serialize_index(self._index).tobytes()
