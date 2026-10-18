import json
from collections import Counter
from unittest.mock import MagicMock

from steel_slag_slm.config import PdfParsingSettings
from steel_slag_slm.data import drive_storage, pdf_parsing, pdf_parsing_source
from steel_slag_slm.data.pdf_parsing_source import PdfSource
from steel_slag_slm.pipelines import parse_pdfs
from steel_slag_slm.schemas import ImagePlacement, ParsedDocument, ParsedImage


def test_run_pdf_parsing_uploads_paper_json_images_and_index(monkeypatch) -> None:
    source = PdfSource("W123", "https://drive.google.com/file/d/SOURCE123/view")
    parsed = ParsedDocument(
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
    )
    uploads: dict[str, bytes] = {}

    monkeypatch.setattr(pdf_parsing_source, "list_pdf_sources", lambda *_: [source])
    monkeypatch.setattr(drive_storage, "ensure_subfolder", lambda *_: "output-folder")
    monkeypatch.setattr(drive_storage, "read_text_file", lambda *_: "")
    monkeypatch.setattr(drive_storage, "drive_file_id_from_link", lambda _: "source-file")
    monkeypatch.setattr(drive_storage, "download_pdf", lambda *_: b"%PDF")
    monkeypatch.setattr(pdf_parsing, "build_docling_converter", lambda: object())
    monkeypatch.setattr(pdf_parsing, "parse_pdf", lambda *_: parsed)

    def fake_write_file(
        _service: object,
        _folder_id: str,
        file_name: str,
        content: bytes,
        _mime_type: str,
    ) -> str:
        uploads[file_name] = content
        return f"drive-{file_name}"

    monkeypatch.setattr(drive_storage, "write_file", fake_write_file)

    outcomes = parse_pdfs.run_pdf_parsing(
        PdfParsingSettings(
            source_table="project.dataset.papers_trial",
            output_folder_name="_parsed",
            index_file_name="index.json",
        ),
        MagicMock(),
        MagicMock(),
        "source-folder",
    )

    assert outcomes == Counter(parsed=1)
    paper_record = json.loads(uploads["W123.json"])
    assert paper_record["markdown"] == "# Sample"
    assert paper_record["tables"] == ["table"]
    assert paper_record["images"][0]["file_id"] == "drive-W123_image_001.png"
    assert "image_bytes" not in paper_record["images"][0]
    assert uploads["W123_image_001.png"] == b"png-bytes"

    index = json.loads(uploads["index.json"])
    assert index["papers"]["W123"] == {
        "json_file_id": "drive-W123.json",
        "image_file_ids": ["drive-W123_image_001.png"],
    }


def test_run_pdf_parsing_skips_papers_already_in_the_index(monkeypatch) -> None:
    source = PdfSource("W123", "https://drive.google.com/file/d/SOURCE123/view")
    stored_index = json.dumps(
        {
            "schema_version": 1,
            "papers": {
                "W123": {
                    "json_file_id": "existing-json",
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
        PdfParsingSettings(
            source_table="project.dataset.papers_trial",
            output_folder_name="_parsed",
            index_file_name="index.json",
        ),
        MagicMock(),
        MagicMock(),
        "source-folder",
    )

    assert outcomes == Counter(already_parsed=1)