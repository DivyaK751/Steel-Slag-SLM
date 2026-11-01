"""Turn text into unit-length vectors with a local sentence-transformers model."""

from collections.abc import Sequence
from typing import Protocol

import numpy as np


class TextEncoder(Protocol):
    """Anything that maps texts to unit-length vectors of a fixed dimension."""

    @property
    def dimension(self) -> int: ...

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class Embedder:
    """One sentence-transformers model. Unit-length output makes dot product equal cosine."""

    def __init__(self, model_name: str) -> None:
        # Imported here: torch is slow to load and only this class needs it
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    @property
    def dimension(self) -> int:
        return int(self._model.get_embedding_dimension())

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Return one float32 row per text."""
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return np.asarray(vectors, dtype=np.float32)
