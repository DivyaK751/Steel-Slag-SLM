import json
from unittest.mock import Mock

import pytest

from steel_slag_slm.pipelines.scan_approved_tables import scan_approved_tables


def _manifest(paper_id: str) -> bytes:
    table = "Table 1. Composition [wt%].\n\n| Mix | CaO |\n| --- | --- |\n| S1 | 45.00 |"
    return json.dumps(
        {
            "openalex_id": paper_id,
            "source_drive_link": "https://example.test/paper",
            "source_sha256": "a" * 64,
            "content_schema_version": 4,
            "parser_revision": "a" * 64,
            "parse_generation": "b" * 32,
            "text_parser": "Docling",
            "image_parser": "Docling+PyMuPDF",
            "page_count": 1,
            "markdown": table,
            "tables": [table],
            "content_blocks": [
                {
                    "kind": "table",
                    "label": "table",
                    "text": table,
                    "placements": [
                        {"page_number": 1, "bbox": [1, 2, 3, 4], "coordinate_origin": "TOPLEFT"}
                    ],
                }
            ],
            "images": [],
        }
    ).encode()


def _drive(*methods: str) -> tuple[Mock, Mock]:
    service = Mock(spec=["files"])
    files = service.files.return_value = Mock(spec=["get", *methods])
    files.get.return_value.execute.return_value = {
        "id": "root",
        "name": "_parsed_approved",
        "mimeType": "application/vnd.google-apps.folder",
        "trashed": False,
    }
    return service, files


def test_scan_targets_one_paper_and_only_reads_drive() -> None:
    service, files = _drive("list", "get_media")
    files.list.return_value.execute.side_effect = [
        {"files": [{"name": "W2", "id": "other"}], "nextPageToken": "next"},
        {"files": [{"name": "W1", "id": "paper"}]},
        {"files": [{"name": "parsed.json", "id": "manifest"}]},
    ]
    files.get_media.return_value.execute.return_value = _manifest("W1")

    assert scan_approved_tables(service, "root", "W1") == {"ready": 1}
    files.get.assert_called_once_with(
        fileId="root", fields="id,name,mimeType,trashed", supportsAllDrives=True
    )
    assert files.list.call_args_list[1].kwargs["pageToken"] == "next"
    assert "'paper' in parents" in files.list.call_args.kwargs["q"]
    assert "name = 'parsed.json'" in files.list.call_args.kwargs["q"]
    files.get_media.assert_called_once_with(fileId="manifest", supportsAllDrives=True)


def test_scan_rejects_duplicate_folders_and_duplicate_manifests() -> None:
    service, files = _drive("list")
    files.list.return_value.execute.side_effect = [
        {
            "files": [
                {"name": "W1", "id": "first"},
                {"name": "W1", "id": "second"},
                {"name": "W2", "id": "third"},
            ]
        },
        {"files": [{"id": "manifest1"}, {"id": "manifest2"}]},
    ]
    assert scan_approved_tables(service, "root") == {"failed": 2}


def test_scan_never_reports_a_mismatched_manifest_as_ready() -> None:
    service, files = _drive("list", "get_media")
    files.list.return_value.execute.side_effect = [
        {"files": [{"name": "W1", "id": "paper"}]},
        {"files": [{"id": "manifest"}]},
    ]
    files.get_media.return_value.execute.return_value = _manifest("W2")
    assert scan_approved_tables(service, "root") == {"failed": 1}


def test_scan_rejects_empty_selection() -> None:
    service, files = _drive("list")
    files.list.return_value.execute.return_value = {"files": []}
    with pytest.raises(ValueError, match="No matching approved paper folders"):
        scan_approved_tables(service, "root", "W1")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "other"),
        ("name", "_parsed_v3"),
        ("mimeType", "application/vnd.google-apps.shortcut"),
        ("trashed", True),
    ],
)
def test_scan_rejects_wrong_root_before_listing_papers(field: str, value: str | bool) -> None:
    service, files = _drive("list")
    files.get.return_value.execute.return_value[field] = value
    with pytest.raises(ValueError, match="is not a live '_parsed_approved' folder"):
        scan_approved_tables(service, "root")
    files.list.assert_not_called()
