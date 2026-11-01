import json
from unittest.mock import MagicMock

import pytest

from steel_slag_slm.data import parsed_index, drive_storage


def test_load_index_preserves_content_schema_version(monkeypatch) -> None:
    stored_index = json.dumps(
        {
            "schema_version": 1,
            "papers": {
                "W123": {
                    "json_file_id": "json-id",
                    "image_file_ids": [],
                    "content_schema_version": 2,
                }
            },
        }
    )
    monkeypatch.setattr(drive_storage, "read_text_file", lambda *_: stored_index)

    papers = parsed_index.load_index(MagicMock(), "output-folder", "index.json")

    assert papers["W123"]["content_schema_version"] == 2


def test_load_index_rejects_boolean_content_schema_version(monkeypatch) -> None:
    stored_index = json.dumps(
        {
            "schema_version": 1,
            "papers": {
                "W123": {
                    "json_file_id": "json-id",
                    "image_file_ids": [],
                    "content_schema_version": True,
                }
            },
        }
    )
    monkeypatch.setattr(drive_storage, "read_text_file", lambda *_: stored_index)

    with pytest.raises(ValueError, match="invalid paper entry"):
        parsed_index.load_index(MagicMock(), "output-folder", "index.json")
