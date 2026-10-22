"""Shared test helpers: an in-memory stand-in for the Drive folder and a keyword encoder."""

import itertools
from collections.abc import Sequence
from typing import Any

import numpy as np
import pytest

from steel_slag_slm.data import drive_storage

_ids = itertools.count(1)


class FakeDrive:
    """Keeps Drive folders and files in memory, with the same calls drive_storage makes."""

    def __init__(self) -> None:
        # parent id -> name -> (child id, is_folder)
        self.children: dict[str, dict[str, tuple[str, bool]]] = {}
        self.contents: dict[str, bytes] = {}

    def _create(self, parent_id: str, name: str, is_folder: bool) -> str:
        file_id = f"id-{next(_ids)}"
        self.children.setdefault(parent_id, {})[name] = (file_id, is_folder)
        return file_id

    def find_subfolder_id(self, _service: Any, parent_id: str, name: str) -> str | None:
        entry = self.children.get(parent_id, {}).get(name)
        return entry[0] if entry and entry[1] else None

    def ensure_subfolder(self, _service: Any, parent_id: str, name: str) -> str:
        return self.find_subfolder_id(None, parent_id, name) or self._create(parent_id, name, True)

    def write_file(
        self, _service: Any, folder_id: str, name: str, content: bytes, _mime: str
    ) -> str:
        entry = self.children.get(folder_id, {}).get(name)
        file_id = entry[0] if entry else self._create(folder_id, name, False)
        self.contents[file_id] = content
        return file_id

    def read_file_bytes(self, _service: Any, folder_id: str, name: str) -> bytes | None:
        entry = self.children.get(folder_id, {}).get(name)
        return self.contents[entry[0]] if entry else None

    def read_text_file(self, service: Any, folder_id: str, name: str) -> str:
        content = self.read_file_bytes(service, folder_id, name)
        return content.decode() if content is not None else ""

    def download_file(self, _service: Any, file_id: str) -> bytes:
        return self.contents[file_id]

    def list_file_names(self, _service: Any, folder_id: str) -> dict[str, str]:
        return {
            name: file_id
            for name, (file_id, is_folder) in self.children.get(folder_id, {}).items()
            if not is_folder
        }

    def delete_file(self, _service: Any, file_id: str) -> None:
        self.contents.pop(file_id)
        for entries in self.children.values():
            for name, (child_id, _) in list(entries.items()):
                if child_id == file_id:
                    del entries[name]


@pytest.fixture
def fake_drive(monkeypatch: pytest.MonkeyPatch) -> FakeDrive:
    """Route drive_storage calls to an in-memory Drive for the duration of one test."""
    drive = FakeDrive()
    for name in (
        "find_subfolder_id",
        "ensure_subfolder",
        "write_file",
        "read_file_bytes",
        "read_text_file",
        "download_file",
        "list_file_names",
        "delete_file",
    ):
        monkeypatch.setattr(drive_storage, name, getattr(drive, name))
    return drive


class KeywordEncoder:
    """Scores each text by which of a few keywords it mentions, so rankings are predictable."""

    KEYWORDS = ("lime", "sinter", "table", "cement")
    dimension = len(KEYWORDS)

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        self.seen.extend(texts)
        rows = np.array(
            [[float(keyword in text.lower()) for keyword in self.KEYWORDS] for text in texts],
            dtype=np.float32,
        )
        rows += 1e-3  # keeps a text with no keyword from becoming a zero vector
        return rows / np.linalg.norm(rows, axis=1, keepdims=True)


@pytest.fixture
def keyword_encoder() -> KeywordEncoder:
    return KeywordEncoder()
