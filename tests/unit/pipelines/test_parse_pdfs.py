"""Exercise unified publication, migration and offline figure consumption."""

import json
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
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
from steel_slag_slm.pipelines import parse_pdfs
from steel_slag_slm.pipelines.export_parsed import run_export_parsed
from steel_slag_slm.pipelines.parse_pdfs import (
    MAX_FAILURE_STREAK,
    format_parse_summary,
    run_pdf_parsing,
)
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

    # The failed paper is reported, not fatal, and stays as it was so the next run retries it
    assert _run(reparse=True)["failed"] == 1

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


def test_run_pdf_parsing_parses_only_the_requested_papers(
    monkeypatch: pytest.MonkeyPatch, fake_drive: Any
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    sources = [
        PdfSource(f"W{number}", f"https://drive.google.com/file/d/SOURCE{number}/view")
        for number in (123, 124, 125)
    ]
    monkeypatch.setattr(pdf_parsing_source, "list_pdf_sources", lambda *_: sources)
    for number in (123, 124, 125):
        fake_drive.contents[f"SOURCE{number}"] = b"%PDF"
    download = MagicMock(wraps=fake_drive.download_file)
    monkeypatch.setattr(drive_storage, "download_pdf", download)

    outcomes = _run(paper_ids=["W124", "W999"])

    download.assert_called_once_with(None, "SOURCE124")
    assert outcomes["parsed"] == 1
    assert outcomes["not_in_source_table"] == 1
    index = json.loads(fake_drive.read_text_file(None, folder, "index.json"))
    assert set(index["papers"]) == {"W124"}


def test_json_only_run_keeps_figure_metadata_without_uploading_images(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    assert _run(store_images=False)["parsed"] == 1

    entry = _entry(fake_drive, folder)
    assert entry["image_folder_id"] == "" and entry["image_file_ids"] == []
    assert fake_drive.list_file_names(None, entry["paper_folder_id"]) == {
        "parsed.json": entry["json_file_id"],
    }
    manifest = ParsedManifest.model_validate_json(fake_drive.contents[entry["json_file_id"]])
    assert manifest.images[0].file_id is None
    assert manifest.images[0].placements[0].bbox == (0, 0, 100, 10)
    assert manifest.images[0].caption == "Fig. 1 Strength"

    assert _run(store_images=False)["already_parsed"] == 1
    # A later run that wants images redoes the JSON-only parse
    assert _run()["parsed"] == 1


def test_export_skips_figures_parsed_without_images(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
    tmp_path: Path,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    _run(store_images=False)
    outcomes = run_export_parsed(PARSING, None, folder, tmp_path)

    assert outcomes["exported"] == 1
    assert (tmp_path / "W123" / "parsed.json").exists()
    assert not any((tmp_path / "W123" / "images").iterdir())


def _use_sources(monkeypatch: pytest.MonkeyPatch, fake_drive: Any, count: int) -> list[str]:
    """Serve `count` papers, each with its own PDF in the fake Drive; return their IDs."""
    sources = [
        PdfSource(f"W{n}", f"https://drive.google.com/file/d/PDF{n}/view") for n in range(count)
    ]
    for n in range(count):
        fake_drive.contents[f"PDF{n}"] = b"%PDF"
    monkeypatch.setattr(pdf_parsing_source, "list_pdf_sources", lambda *_: sources)
    return [source.openalex_id for source in sources]


def test_download_failure_moves_on_and_the_next_run_retries_only_that_paper(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _prepare(monkeypatch, fake_drive)
    _use_sources(monkeypatch, fake_drive, 3)
    download = fake_drive.download_file

    def flaky(service: Any, file_id: str) -> bytes:
        if file_id == "PDF1":
            raise TimeoutError("Drive API timed out")
        return bytes(download(service, file_id))

    monkeypatch.setattr(drive_storage, "download_pdf", flaky)
    with caplog.at_level("WARNING"):
        outcomes = _run()
    assert (outcomes["parsed"], outcomes["failed"]) == (2, 1)
    assert "W1  failed: could not download the PDF" in caplog.text

    monkeypatch.setattr(drive_storage, "download_pdf", download)
    outcomes = _run()
    assert (outcomes["parsed"], outcomes["already_parsed"]) == (1, 2)


def test_unparsable_pdf_is_indexed_so_reruns_skip_it(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    monkeypatch.setattr(
        pdf_parsing, "parse_pdf_with_figures", MagicMock(side_effect=RuntimeError("bad"))
    )

    assert _run()["skipped"] == 1
    assert _entry(fake_drive, folder)["skip_reason"] == "parse_failed"
    assert _run()["skipped"] == 1

    monkeypatch.setattr(pdf_parsing, "parse_pdf_with_figures", lambda *_: _parsed())
    assert _run(retry_skipped=True)["parsed"] == 1


def test_run_stops_when_every_paper_keeps_failing(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    _prepare(monkeypatch, fake_drive)
    _use_sources(monkeypatch, fake_drive, MAX_FAILURE_STREAK + 5)
    download = MagicMock(side_effect=ConnectionError("offline"))
    monkeypatch.setattr(drive_storage, "download_pdf", download)

    with pytest.raises(RuntimeError, match="in a row"):
        _run()
    assert download.call_count == MAX_FAILURE_STREAK


def test_parallel_workers_parse_and_index_every_paper(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    paper_ids = _use_sources(monkeypatch, fake_drive, 6)
    # Threads stand in for worker processes, which wouldn't see this test's fakes
    monkeypatch.setattr(
        parse_pdfs,
        "_make_executor",
        lambda workers, _threads: ThreadPoolExecutor(
            workers, initializer=parse_pdfs._start_worker, initargs=(None,)
        ),
    )

    assert _run(workers=3)["parsed"] == 6
    index = json.loads(fake_drive.read_text_file(None, folder, "index.json"))
    assert sorted(index["papers"]) == sorted(paper_ids)


def test_pipeline_rejects_nonpositive_workers() -> None:
    with pytest.raises(ValueError, match="workers"):
        _run(workers=0)


def test_parse_summary_always_shows_parsed_skipped_failed_and_hides_other_zeros() -> None:
    summary = format_parse_summary(Counter(parsed=18, failed=2, deferred=954), "_parsed_approved")
    assert summary.splitlines() == [
        "Parsing summary (Drive folder _parsed_approved/)",
        "     18  Parsed in this run",
        "      0  Skipped, PDF unusable (rerun with --retry-skipped to try again)",
        "      2  Failed, retried automatically on the next run",
        "    954  Not started yet (beyond --max-papers)",
    ]


def test_parse_that_breaks_manifest_rules_is_skipped_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    broken = replace(_parsed(), page_count=0)  # page_count must be positive
    monkeypatch.setattr(pdf_parsing, "parse_pdf_with_figures", lambda *_: broken)

    assert _run()["skipped"] == 1
    assert _entry(fake_drive, folder)["skip_reason"] == "invalid_manifest"
    assert _run()["skipped"] == 1


class _CrashingPool(ThreadPoolExecutor):
    """Threads standing in for worker processes; a PDF reading b"%CRASH" kills its worker."""

    def __init__(self, workers: int) -> None:
        super().__init__(workers, initializer=parse_pdfs._start_worker, initargs=(None,))

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        if args[0] == b"%CRASH":
            future: Future[Any] = Future()
            future.set_exception(BrokenProcessPool("worker died"))
            return future
        return super().submit(fn, *args, **kwargs)


def test_worker_crash_skips_only_the_paper_that_crashes_alone_and_keeps_going(
    monkeypatch: pytest.MonkeyPatch,
    fake_drive: Any,
) -> None:
    folder = _prepare(monkeypatch, fake_drive)
    _use_sources(monkeypatch, fake_drive, 5)
    fake_drive.contents["PDF2"] = b"%CRASH"
    pools: list[int] = []

    def make_pool(workers: int, _threads: int, *, isolated: bool = False) -> _CrashingPool:
        pools.append(workers)
        return _CrashingPool(workers)

    monkeypatch.setattr(parse_pdfs, "_make_executor", make_pool)

    outcomes = _run(workers=2)

    assert (outcomes["parsed"], outcomes["skipped"]) == (4, 1)
    index = json.loads(fake_drive.read_text_file(None, folder, "index.json"))["papers"]
    assert index["W2"]["skip_reason"] == "worker_crashed"
    # The first pool, a solo pool for the crashing paper, then a fresh pool for the rest
    assert pools[:3] == [2, 1, 2]
    assert _run(workers=2)["skipped"] == 1
