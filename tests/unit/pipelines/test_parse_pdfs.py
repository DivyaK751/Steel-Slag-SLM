"""Exercise unified publication, migration and offline figure consumption."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pymupdf
import pytest
from docling.exceptions import ConversionError
from pydantic import ValidationError

from steel_slag_slm.config import FigureExtractionSettings, PdfParsingSettings
from steel_slag_slm.data import drive_storage, pdf_parsing, pdf_parsing_source
from steel_slag_slm.data.parsed_manifest import ParsedManifest, parser_revision
from steel_slag_slm.data.parsed_publication import publish_parse
from steel_slag_slm.data.pdf_parsing_source import PdfSource
from steel_slag_slm.pipelines.export_parsed import run_export_parsed
from steel_slag_slm.pipelines.parse_pdfs import run_pdf_parsing
from steel_slag_slm.pipelines.read_figures import list_paper_dirs, load_figures_to_read
from steel_slag_slm.schemas import CaptionBlock, Figure, ParsedDocument, RenderedFigure

SOURCE = PdfSource("W123", "https://drive.google.com/file/d/SOURCE123/view")
PARSING = PdfParsingSettings(
    source_table="project.dataset.papers",
    output_folder_name="_parsed",
    index_file_name="index.json",
)
FIGURES = FigureExtractionSettings(
    output_dir=Path("unused"),
    render_dpi=72,
    caption_max_gap_pt=60,
    min_side_pt=72,
    min_split_height_pt=50,
    drop_types=["logo"],
    min_uncaptioned_side_pt=150,
)


def _parsed() -> ParsedDocument:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    pixmap.clear_with(255)
    caption = CaptionBlock(1, (0, 20, 100, 30), "Fig. 1 Strength", "1")
    figure = Figure(1, (0, 0, 100, 10), "line_chart", 0.9, caption.text, "1", caption)
    item = RenderedFigure(
        figure,
        "figure_01.png",
        10,
        10,
        72,
        "Strength",
        pixmap.tobytes("png"),
    )
    return ParsedDocument(
        "Docling",
        "Docling+PyMuPDF",
        1,
        "Text",
        (),
        (),
        figures=(item,),
    )


def _prepare(monkeypatch: pytest.MonkeyPatch, drive: Any) -> str:
    monkeypatch.setattr(pdf_parsing_source, "list_pdf_sources", lambda *_: [SOURCE])
    monkeypatch.setattr(pdf_parsing, "build_docling_converter", lambda **_: MagicMock())
    monkeypatch.setattr(pdf_parsing, "parse_pdf_with_figures", lambda *_: _parsed())
    monkeypatch.setattr(drive_storage, "download_pdf", drive.download_file)
    drive.contents["SOURCE123"] = b"%PDF"
    return str(drive.ensure_subfolder(None, "root", "_parsed"))


def _run(**kwargs: Any) -> Any:
    return run_pdf_parsing(
        PARSING,
        MagicMock(),
        None,
        "root",
        figure_settings=FIGURES,
        **kwargs,
    )


def _entry(drive: Any, folder: str) -> dict[str, Any]:
    content = json.loads(drive.read_text_file(None, folder, "index.json"))
    return dict(content["papers"]["W123"])


def test_unified_pipeline_publishes_single_indexed_manifest(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    write = MagicMock(wraps=drive_storage.write_file)
    monkeypatch.setattr(drive_storage, "write_file", write)
    assert _run()["parsed"] == 1

    entry = _entry(fake_drive, folder)
    assert fake_drive.list_file_names(None, entry["paper_folder_id"]) == {
        "parsed.json": entry["json_file_id"],
    }
    manifest = ParsedManifest.model_validate_json(fake_drive.contents[entry["json_file_id"]])
    assert entry["content_schema_version"] == 4
    assert entry["parse_generation"] == manifest.parse_generation
    assert manifest.images[0].caption_placements[0].bbox == (0, 20, 100, 30)
    assert manifest.images[0].file_id in entry["image_file_ids"]
    assert (
        fake_drive.read_file_bytes(None, entry["paper_folder_id"], "parsed.json")
        == fake_drive.contents[entry["json_file_id"]]
    )
    assert sum(call.args[2] == "parsed.json" for call in write.call_args_list) == 1
    write.reset_mock()
    assert _run()["already_parsed"] == 1
    write.assert_not_called()


def test_reparse_replaces_manifest_and_preserves_old_images(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    _run()
    old = _entry(fake_drive, folder)
    old_manifest = fake_drive.contents[old["json_file_id"]]
    old_images = {file_id: fake_drive.contents[file_id] for file_id in old["image_file_ids"]}

    assert _run(reparse=True)["parsed"] == 1

    new = _entry(fake_drive, folder)
    assert old["parse_generation"] != new["parse_generation"]
    assert new["json_file_id"] == old["json_file_id"]
    assert fake_drive.contents[new["json_file_id"]] != old_manifest
    assert fake_drive.list_file_names(None, new["paper_folder_id"]) == {
        "parsed.json": new["json_file_id"],
    }
    assert all(fake_drive.contents[key] == value for key, value in old_images.items())


@pytest.mark.parametrize("failed_name", ["image", "manifest", "index"])
def test_upload_failure_preserves_old_index(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
    failed_name: str,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    _run()
    old_index = fake_drive.read_text_file(None, folder, "index.json")
    original = fake_drive.write_file

    def fail(
        service: Any,
        parent: str,
        name: str,
        content: bytes,
        mime: str,
    ) -> str:
        matches = (
            (failed_name == "image" and name.endswith(".png"))
            or (failed_name == "manifest" and name == "parsed.json")
            or (failed_name == "index" and name == "index.json")
        )
        if matches:
            raise RuntimeError("simulated upload failure")
        return str(original(service, parent, name, content, mime))

    monkeypatch.setattr(drive_storage, "write_file", fail)

    with pytest.raises(RuntimeError, match="upload failure"):
        _run(reparse=True)

    assert fake_drive.read_text_file(None, folder, "index.json") == old_index
    entry = _entry(fake_drive, folder)
    manifest = ParsedManifest.model_validate_json(fake_drive.contents[entry["json_file_id"]])
    if failed_name == "index":
        assert manifest.parse_generation != entry["parse_generation"]
    else:
        assert manifest.parse_generation == entry["parse_generation"]

    monkeypatch.setattr(drive_storage, "write_file", original)
    outcomes = _run()
    assert outcomes["parsed" if failed_name == "index" else "already_parsed"] == 1
    entry = _entry(fake_drive, folder)
    manifest = ParsedManifest.model_validate_json(fake_drive.contents[entry["json_file_id"]])
    assert manifest.parse_generation == entry["parse_generation"]


def test_failed_reparse_keeps_previous_success(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    _run()
    old = _entry(fake_drive, folder)
    monkeypatch.setattr(
        pdf_parsing,
        "parse_pdf_with_figures",
        MagicMock(side_effect=ConversionError("bad")),
    )

    assert _run(reparse=True)["failed"] == 1
    assert _entry(fake_drive, folder) == old


def test_figure_only_paper_is_published(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    _prepare(monkeypatch, fake_drive)
    monkeypatch.setattr(
        pdf_parsing,
        "parse_pdf_with_figures",
        lambda *_: replace(_parsed(), markdown=""),
    )

    assert _run()["parsed"] == 1


def test_empty_pdf_skip_requires_explicit_retry(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    _prepare(monkeypatch, fake_drive)
    fake_drive.contents["SOURCE123"] = b""
    assert _run()["skipped"] == 1

    fake_drive.contents["SOURCE123"] = b"%PDF"
    assert _run()["skipped"] == 1
    assert _run(retry_skipped=True)["parsed"] == 1


def test_revision_ignores_local_output_path() -> None:
    assert parser_revision(FIGURES) == parser_revision(
        FIGURES.model_copy(update={"output_dir": Path("elsewhere")})
    )
    assert parser_revision(FIGURES) != parser_revision(
        FIGURES.model_copy(update={"render_dpi": 200})
    )


def test_version_three_index_is_migrated(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    _run()
    entry = _entry(fake_drive, folder)
    entry["content_schema_version"] = 3
    fake_drive.write_file(
        None,
        folder,
        "index.json",
        json.dumps({"schema_version": 1, "papers": {"W123": entry}}).encode(),
        "application/json",
    )

    assert _run()["parsed"] == 1
    assert _entry(fake_drive, folder)["content_schema_version"] == 4


def test_pipeline_limits_pending_papers(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    _prepare(monkeypatch, fake_drive)
    monkeypatch.setattr(
        pdf_parsing_source,
        "list_pdf_sources",
        lambda *_: [SOURCE, PdfSource("W124", SOURCE.drive_link)],
    )

    outcomes = _run(max_papers=1)

    assert outcomes["parsed"] == 1
    assert outcomes["deferred"] == 1


def test_pipeline_rejects_nonpositive_limit() -> None:
    with pytest.raises(ValueError, match="max_papers"):
        _run(max_papers=0)


def test_export_and_reader_use_same_manifest(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
    tmp_path: Path,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    _run()

    outcomes = run_export_parsed(PARSING, None, folder, tmp_path)

    assert outcomes["exported"] == 1
    assert list_paper_dirs(tmp_path, None) == [tmp_path / "W123"]
    figures = load_figures_to_read(tmp_path / "W123")
    assert len(figures) == 1
    assert figures[0].figure_id is not None
    assert figures[0].image_path.parent.name == "images"

    figures[0].image_path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        load_figures_to_read(tmp_path / "W123")


def test_corrupt_export_does_not_publish_local_manifest(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
    tmp_path: Path,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    _run()
    entry = _entry(fake_drive, folder)
    fake_drive.contents[entry["image_file_ids"][0]] = b"corrupt"

    with pytest.raises(ValueError, match="checksum"):
        run_export_parsed(PARSING, None, folder, tmp_path)

    assert not (tmp_path / "W123" / "parsed.json").exists()


@pytest.mark.parametrize("bad_value", ["../escape.png", "images/../escape.png"])
def test_manifest_rejects_unsafe_image_path(
    fake_drive: Any,
    bad_value: str,
) -> None:
    published = publish_parse(
        None,
        "paper",
        "images",
        SOURCE,
        b"%PDF",
        _parsed(),
        parser_revision(FIGURES),
    )
    raw = published.manifest.model_dump()
    raw["images"][0]["relative_path"] = bad_value

    with pytest.raises(ValidationError):
        ParsedManifest.model_validate(raw)
