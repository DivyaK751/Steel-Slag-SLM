import json
from collections import Counter
from unittest.mock import MagicMock

import pytest

from steel_slag_slm.config import PdfParsingSettings
from steel_slag_slm.data import drive_storage, pdf_parsing, pdf_parsing_source
from steel_slag_slm.data.pdf_parsing_source import PdfSource
from steel_slag_slm.pipelines import parse_pdfs
from steel_slag_slm.schemas import (
    ContentPlacement,
    ParsedContentBlock,
    ImagePlacement,
    ParsedDocument,
    ParsedImage,
)


def _settings() -> PdfParsingSettings:
    return PdfParsingSettings(
        source_table="project.dataset.papers_trial",
        output_folder_name="_parsed",
        index_file_name="index.json",
    )


def _parsed_document() -> ParsedDocument:
    return ParsedDocument(
        text_parser="Docling",
        image_parser="PyMuPDF",
        page_count=1,
        markdown="# Sample",
        tables=("table",),
        images=(
            ParsedImage(
                xref=7,
                file_name="image_001.png",
                width=2,
                height=2,
                placements=(ImagePlacement(page_number=1, bbox=(1.0, 2.0, 3.0, 4.0)),),
                image_bytes=b"png-bytes",
            ),
        ),
        content_blocks=(
            ParsedContentBlock(
                kind="text",
                label="paragraph",
                text="Sample text",
                placements=(
                    ContentPlacement(
                        page_number=1,
                        bbox=(10.0, 20.0, 100.0, 40.0),
                        coordinate_origin="TOPLEFT",
                    ),
                ),
            ),
        ),
    )


def test_run_pdf_parsing_uploads_paper_folder_images_and_index(monkeypatch) -> None:
    source = PdfSource("W123", "https://drive.google.com/file/d/SOURCE123/view")
    parsed = _parsed_document()
    uploads: dict[tuple[str, str], bytes] = {}

    monkeypatch.setattr(pdf_parsing_source, "list_pdf_sources", lambda *_: [source])
    monkeypatch.setattr(
        drive_storage,
        "ensure_subfolder",
        lambda _service, parent_id, name: f"{parent_id}/{name}",
    )
    monkeypatch.setattr(drive_storage, "read_text_file", lambda *_: "")
    monkeypatch.setattr(drive_storage, "drive_file_id_from_link", lambda _: "source-file")
    monkeypatch.setattr(drive_storage, "download_pdf", lambda *_: b"%PDF")
    monkeypatch.setattr(pdf_parsing, "build_docling_converter", lambda: object())
    monkeypatch.setattr(pdf_parsing, "parse_pdf", lambda *_: parsed)

    def fake_write_file(
        _service: object,
        folder_id: str,
        file_name: str,
        content: bytes,
        _mime_type: str,
    ) -> str:
        uploads[(folder_id, file_name)] = content
        return f"{folder_id}:{file_name}"

    monkeypatch.setattr(drive_storage, "write_file", fake_write_file)

    outcomes = parse_pdfs.run_pdf_parsing(
        _settings(),
        MagicMock(),
        MagicMock(),
        "source-folder",
    )

    paper_folder = "source-folder/_parsed/W123"
    image_folder = f"{paper_folder}/images"

    assert outcomes == Counter(parsed=1)
    paper_record = json.loads(uploads[(paper_folder, "parsed.json")])
    assert paper_record["markdown"] == "# Sample"
    assert paper_record["tables"] == ["table"]
    assert paper_record["content_schema_version"] == 3
    assert paper_record["content_blocks"] == [
        {
            "kind": "text",
            "label": "paragraph",
            "text": "Sample text",
            "placements": [
                {
                    "page_number": 1,
                    "bbox": [10.0, 20.0, 100.0, 40.0],
                    "coordinate_origin": "TOPLEFT",
                }
            ],
        }
    ]
    assert paper_record["images"][0]["file_id"] == f"{image_folder}:image_001.png"
    assert "image_bytes" not in paper_record["images"][0]
    assert uploads[(image_folder, "image_001.png")] == b"png-bytes"

    index = json.loads(uploads[("source-folder/_parsed", "index.json")])
    assert index["papers"]["W123"] == {
        "source_drive_link": source.drive_link,
        "paper_folder_id": paper_folder,
        "json_file_id": f"{paper_folder}:parsed.json",
        "image_folder_id": image_folder,
        "image_file_ids": [f"{image_folder}:image_001.png"],
        "content_schema_version": 3,
    }


def test_run_pdf_parsing_skips_papers_already_in_the_new_layout(monkeypatch) -> None:
    source = PdfSource("W123", "https://drive.google.com/file/d/SOURCE123/view")
    stored_index = json.dumps(
        {
            "schema_version": 1,
            "papers": {
                "W123": {
                    "content_schema_version": 3,
                    "source_drive_link": source.drive_link,
                    "paper_folder_id": "paper-folder",
                    "json_file_id": "existing-json",
                    "image_folder_id": "image-folder",
                    "image_file_ids": [],
                }
            },
        }
    )

    monkeypatch.setattr(pdf_parsing_source, "list_pdf_sources", lambda *_: [source])
    monkeypatch.setattr(drive_storage, "ensure_subfolder", lambda *_: "output-folder")
    monkeypatch.setattr(drive_storage, "read_text_file", lambda *_: stored_index)
    monkeypatch.setattr(
        pdf_parsing,
        "build_docling_converter",
        lambda: (_ for _ in ()).throw(AssertionError("should not build converter")),
    )

    outcomes = parse_pdfs.run_pdf_parsing(
        _settings(),
        MagicMock(),
        MagicMock(),
        "source-folder",
    )

    assert outcomes == Counter(already_parsed=1)


def test_run_pdf_parsing_reprocesses_legacy_index_entries(monkeypatch) -> None:
    source = PdfSource("W123", "https://drive.google.com/file/d/SOURCE123/view")
    legacy_index = json.dumps(
        {
            "schema_version": 1,
            "papers": {
                "W123": {
                    "source_drive_link": source.drive_link,
                    "paper_folder_id": "old-paper-folder",
                    "json_file_id": "old-json",
                    "image_folder_id": "old-image-folder",
                    "image_file_ids": ["old-image"],
                }
            },
        }
    )
    parsed = ParsedDocument(
        text_parser="Docling",
        image_parser="PyMuPDF",
        page_count=1,
        markdown="# Reparsed",
        tables=(),
        images=(),
    )
    uploads: dict[tuple[str, str], bytes] = {}

    monkeypatch.setattr(pdf_parsing_source, "list_pdf_sources", lambda *_: [source])
    monkeypatch.setattr(
        drive_storage,
        "ensure_subfolder",
        lambda _service, parent_id, name: f"{parent_id}/{name}",
    )
    monkeypatch.setattr(drive_storage, "read_text_file", lambda *_: legacy_index)
    monkeypatch.setattr(drive_storage, "drive_file_id_from_link", lambda _: "source-file")
    monkeypatch.setattr(drive_storage, "download_pdf", lambda *_: b"%PDF")
    monkeypatch.setattr(pdf_parsing, "build_docling_converter", lambda: object())
    monkeypatch.setattr(pdf_parsing, "parse_pdf", lambda *_: parsed)

    def fake_write_file(
        _service: object,
        folder_id: str,
        file_name: str,
        content: bytes,
        _mime_type: str,
    ) -> str:
        uploads[(folder_id, file_name)] = content
        return f"{folder_id}:{file_name}"

    monkeypatch.setattr(drive_storage, "write_file", fake_write_file)

    outcomes = parse_pdfs.run_pdf_parsing(
        _settings(),
        MagicMock(),
        MagicMock(),
        "source-folder",
        max_papers=1,
    )

    paper_folder = "source-folder/_parsed/W123"
    assert outcomes["parsed"] == 1
    assert uploads[(paper_folder, "parsed.json")]
    updated_index = json.loads(uploads[("source-folder/_parsed", "index.json")])
    assert updated_index["papers"]["W123"]["paper_folder_id"] == paper_folder
    assert updated_index["papers"]["W123"]["content_schema_version"] == 3


def test_run_pdf_parsing_limits_papers_and_counts_deferred(monkeypatch) -> None:
    sources = [
        PdfSource(f"W{number}", f"https://drive.google.com/file/d/SOURCE{number}/view")
        for number in (123, 124, 125)
    ]
    parsed = ParsedDocument(
        text_parser="Docling",
        image_parser="PyMuPDF",
        page_count=1,
        markdown="# Sample",
        tables=(),
        images=(),
    )
    parsed_ids: list[str] = []

    monkeypatch.setattr(pdf_parsing_source, "list_pdf_sources", lambda *_: sources)
    monkeypatch.setattr(
        drive_storage,
        "ensure_subfolder",
        lambda _service, parent_id, name: f"{parent_id}/{name}",
    )
    monkeypatch.setattr(drive_storage, "read_text_file", lambda *_: "")
    monkeypatch.setattr(drive_storage, "drive_file_id_from_link", lambda link: link)
    monkeypatch.setattr(drive_storage, "download_pdf", lambda *_: b"%PDF")
    monkeypatch.setattr(pdf_parsing, "build_docling_converter", lambda: object())

    def fake_parse_pdf(pdf_bytes: bytes, converter: object) -> ParsedDocument:
        parsed_ids.append(pdf_bytes.decode())
        return parsed

    monkeypatch.setattr(pdf_parsing, "parse_pdf", fake_parse_pdf)

    def fake_write_file(
        _service: object,
        _folder_id: str,
        file_name: str,
        _content: bytes,
        _mime_type: str,
    ) -> str:
        return file_name

    monkeypatch.setattr(drive_storage, "write_file", fake_write_file)

    outcomes = parse_pdfs.run_pdf_parsing(
        _settings(),
        MagicMock(),
        MagicMock(),
        "source-folder",
        max_papers=1,
    )

    assert outcomes["parsed"] == 1
    assert outcomes["deferred"] == 2
    assert parsed_ids == ["%PDF"]


def test_run_pdf_parsing_parses_only_the_requested_papers(monkeypatch) -> None:
    sources = [PdfSource(f"W{number}", f"SOURCE{number}") for number in (123, 124, 125)]
    parsed = ParsedDocument(
        text_parser="Docling",
        image_parser="PyMuPDF",
        page_count=1,
        markdown="# Sample",
        tables=(),
        images=(),
    )
    downloaded: list[str] = []

    monkeypatch.setattr(pdf_parsing_source, "list_pdf_sources", lambda *_: sources)
    monkeypatch.setattr(
        drive_storage,
        "ensure_subfolder",
        lambda _service, parent_id, name: f"{parent_id}/{name}",
    )
    monkeypatch.setattr(drive_storage, "read_text_file", lambda *_: "")
    monkeypatch.setattr(drive_storage, "drive_file_id_from_link", lambda link: link)

    def fake_download_pdf(_service: object, file_id: str) -> bytes:
        downloaded.append(file_id)
        return b"%PDF"

    monkeypatch.setattr(drive_storage, "download_pdf", fake_download_pdf)
    monkeypatch.setattr(pdf_parsing, "build_docling_converter", lambda: object())
    monkeypatch.setattr(pdf_parsing, "parse_pdf", lambda *_: parsed)
    monkeypatch.setattr(drive_storage, "write_file", lambda _s, _f, name, *_: name)

    outcomes = parse_pdfs.run_pdf_parsing(
        _settings(),
        MagicMock(),
        MagicMock(),
        "source-folder",
        paper_ids=["W124", "W999"],
    )

    assert downloaded == ["SOURCE124"]
    assert outcomes["parsed"] == 1
    assert outcomes["not_in_source_table"] == 1


def test_run_pdf_parsing_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="max_papers must be greater than zero"):
        parse_pdfs.run_pdf_parsing(
            _settings(),
            MagicMock(),
            MagicMock(),
            "source-folder",
            max_papers=0,
        )
