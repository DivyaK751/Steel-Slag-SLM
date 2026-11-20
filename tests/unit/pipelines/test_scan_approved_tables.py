import json
from unittest.mock import Mock

import pytest

from steel_slag_slm.data.approved_table_text import enrich_approved_json
from steel_slag_slm.pipelines.scan_approved_tables import scan_approved_tables

TABLE = "Table 1. Composition [wt%].\n\n| Mix | CaO |\n| --- | --- |\n| S1 | 45.00 |"


def _manifest(paper_id: str, *tables: str) -> bytes:
    tables = tables or (TABLE,)
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
            "markdown": "\n\n".join(tables),
            "tables": list(tables),
            "content_blocks": [
                {
                    "kind": "table",
                    "label": "table",
                    "text": table,
                    "placements": [
                        {"page_number": 1, "bbox": [1, 2, 3, 4], "coordinate_origin": "TOPLEFT"}
                    ],
                }
                for table in tables
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


def _apply_drive() -> tuple[Mock, Mock]:
    service, files = _drive("list", "get_media", "update")
    root = files.get.return_value.execute.return_value
    files.get.return_value.execute.side_effect = [
        root,
        root,
        {**root, "id": "paper", "name": "W1", "parents": ["root"]},
    ]
    files.list.return_value.execute.side_effect = [
        {"files": [{"name": "W1", "id": "paper"}]},
        {"files": [{"id": "manifest"}]},
        {"files": [{"id": "manifest"}]},
    ]
    return service, files


def test_apply_updates_verified_file_in_place_and_verifies_payload() -> None:
    service, files = _apply_drive()
    original = _manifest("W1")
    updated = enrich_approved_json(original)
    files.get_media.return_value.execute.side_effect = [original, original, updated]

    assert scan_approved_tables(service, "root", "W1", apply=True) == {"updated": 1}
    files.update.assert_called_once()
    args = files.update.call_args.kwargs
    # Only the existing file's contents change: no new file, no move between folders.
    assert set(args) == {"fileId", "media_body", "supportsAllDrives"}
    assert args["fileId"] == "manifest"
    assert args["media_body"].getbytes(0, len(updated)) == updated
    assert files.get_media.call_count == 3


def test_apply_writes_renderable_tables_even_when_another_table_is_blocked() -> None:
    service, files = _apply_drive()
    original = _manifest("W1", TABLE, "Table 2. Notes\n\nplain text")
    updated = enrich_approved_json(original)
    files.get_media.return_value.execute.side_effect = [original, original, updated]

    assert scan_approved_tables(service, "root", "W1", apply=True) == {"updated": 1}
    written = json.loads(files.update.call_args.kwargs["media_body"].getbytes(0, len(updated)))
    assert [len(table["rows"]) for table in written["contextual_tables"]] == [1, 0]


def test_apply_rerenders_rows_written_by_the_first_backfill_version() -> None:
    service, files = _apply_drive()
    old = json.loads(
        enrich_approved_json(_manifest("W1", TABLE, TABLE.replace("Table 1", "Table 2")))
    )
    old["contextual_tables"][1]["rows"] = []  # v1 rules rendered fewer rows
    original = json.dumps(old).encode()
    updated = enrich_approved_json(original)
    files.get_media.return_value.execute.side_effect = [original, original, updated]

    assert scan_approved_tables(service, "root", "W1", apply=True) == {"updated": 1}
    written = json.loads(updated)["contextual_tables"]
    assert [table["reference"] for table in written] == [
        "approved-markdown:0",
        "approved-markdown:1",
    ]
    assert [len(table["rows"]) for table in written] == [1, 1]


@pytest.mark.parametrize("reason", ["moved_folder", "different_file", "changed_contents"])
def test_apply_refuses_to_write_after_a_target_change(reason: str) -> None:
    service, files = _apply_drive()
    source = _manifest("W1")
    files.get_media.return_value.execute.return_value = source
    if reason == "moved_folder":
        root = {
            "id": "root",
            "name": "_parsed_approved",
            "trashed": False,
            "mimeType": "application/vnd.google-apps.folder",
        }
        files.get.return_value.execute.side_effect = [
            root,
            root,
            {**root, "id": "paper", "name": "W1", "parents": ["other"]},
        ]
    elif reason == "different_file":
        files.list.return_value.execute.side_effect = [
            {"files": [{"name": "W1", "id": "paper"}]},
            {"files": [{"id": "manifest"}]},
            {"files": [{"id": "other"}]},
        ]
    else:
        files.get_media.return_value.execute.side_effect = [source, source + b"\n"]

    assert scan_approved_tables(service, "root", "W1", apply=True) == {"failed": 1}
    files.update.assert_not_called()


@pytest.mark.parametrize(
    ("reference", "table", "expected"),
    [
        (None, TABLE.replace("45.00", "     "), "unsupported"),
        ("approved-markdown:0", TABLE, "up_to_date"),
        ("#/tables/0", TABLE, "other_rows"),
    ],
)
def test_apply_leaves_papers_without_new_rows_alone(
    reference: str | None, table: str, expected: str
) -> None:
    service, files = _apply_drive()
    source = _manifest("W1", table)
    if reference is not None:
        enriched = json.loads(enrich_approved_json(source))
        enriched["contextual_tables"][0]["reference"] = reference
        source = (json.dumps(enriched, ensure_ascii=False, indent=2) + "\n").encode()
    files.get_media.return_value.execute.return_value = source
    assert scan_approved_tables(service, "root", "W1", apply=True) == {expected: 1}
    files.update.assert_not_called()


def test_apply_fails_loudly_when_saved_content_does_not_match() -> None:
    service, files = _apply_drive()
    source = _manifest("W1")
    files.get_media.return_value.execute.side_effect = [source, source, source]
    with pytest.raises(RuntimeError, match="read-back did not match"):
        scan_approved_tables(service, "root", "W1", apply=True)
    files.update.assert_called_once()
