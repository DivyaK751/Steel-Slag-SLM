"""Read the Drive index that maps each parsed paper to its output files."""

import json
from typing import Any

from steel_slag_slm.data import drive_storage

INDEX_SCHEMA_VERSION = 1


def load_index(
    drive_service: Any,
    output_folder_id: str,
    index_file_name: str,
) -> dict[str, dict[str, str | int | list[str]]]:
    """Read and validate the existing Drive index."""
    index_text = drive_storage.read_text_file(drive_service, output_folder_id, index_file_name)
    if not index_text:
        return {}

    try:
        stored_index = json.loads(index_text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Drive index {index_file_name!r} is not valid JSON") from error

    if (
        not isinstance(stored_index, dict)
        or stored_index.get("schema_version") != INDEX_SCHEMA_VERSION
        or not isinstance(stored_index.get("papers"), dict)
    ):
        raise ValueError(f"Drive index {index_file_name!r} has an unsupported structure")

    papers: dict[str, dict[str, str | int | list[str]]] = {}
    for paper_id, entry in stored_index["papers"].items():
        if not isinstance(paper_id, str) or not isinstance(entry, dict):
            raise ValueError(f"Drive index {index_file_name!r} contains an invalid paper entry")

        json_file_id = entry.get("json_file_id")
        image_file_ids = entry.get("image_file_ids")
        if (
            not isinstance(json_file_id, str)
            or not isinstance(image_file_ids, list)
            or not all(isinstance(file_id, str) for file_id in image_file_ids)
        ):
            raise ValueError(f"Drive index {index_file_name!r} contains an invalid paper entry")

        papers[paper_id] = {
            "json_file_id": json_file_id,
            "image_file_ids": image_file_ids,
        }

        status = entry.get("status")
        skip_reason = entry.get("skip_reason")
        if status is not None and (
            not isinstance(status, str) or status not in {"parsed", "skipped"}
        ):
            raise ValueError(f"Drive index {index_file_name!r} contains an invalid paper entry")
        if skip_reason is not None and not isinstance(skip_reason, str):
            raise ValueError(f"Drive index {index_file_name!r} contains an invalid paper entry")
        if status == "skipped" and not skip_reason:
            raise ValueError(f"Drive index {index_file_name!r} contains an invalid paper entry")
        if status != "skipped" and skip_reason is not None:
            raise ValueError(f"Drive index {index_file_name!r} contains an invalid paper entry")

        if status is not None:
            papers[paper_id]["status"] = status
        if skip_reason is not None:
            papers[paper_id]["skip_reason"] = skip_reason

        for key in ("paper_folder_id", "image_folder_id", "source_drive_link"):
            value = entry.get(key)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(
                        f"Drive index {index_file_name!r} contains an invalid paper entry"
                    )
                papers[paper_id][key] = value

        
        content_schema_version = entry.get("content_schema_version")
        if content_schema_version is not None:
            if not isinstance(content_schema_version, int) or isinstance(
                content_schema_version, bool
            ):
                raise ValueError(f"Drive index {index_file_name!r} contains an invalid paper entry")
            papers[paper_id]["content_schema_version"] = content_schema_version

    return papers
