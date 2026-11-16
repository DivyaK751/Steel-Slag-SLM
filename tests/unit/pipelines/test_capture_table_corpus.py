import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pymupdf
import pytest
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.exceptions import ConversionError
from docling_core.types.doc.base import Size
from docling_core.types.doc.document import DoclingDocument

from steel_slag_slm.data.pdf_parsing import build_docling_converter
from steel_slag_slm.pipelines.capture_table_corpus import FullDocumentCapture, capture_local_corpus


def _inputs(tmp_path: Path) -> tuple[Path, MagicMock]:
    source = tmp_path / "W123 - Title.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.save(source)
    document = DoclingDocument(name="test")
    for page in [1, 2]:
        document.add_page(page_no=page, size=Size(width=600, height=800))
    converter = MagicMock()
    converter.format_to_options = build_docling_converter(include_figures=True).format_to_options
    converter.convert.return_value.status = ConversionStatus.SUCCESS
    converter.convert.return_value.document = document
    return source, converter


def test_full_capture_roundtrips_and_resumes_without_conversion(tmp_path: Path) -> None:
    source, converter = _inputs(tmp_path)
    original = source.read_bytes()
    paths = capture_local_corpus(["W123"], tmp_path, tmp_path / "out", converter)
    saved = FullDocumentCapture.model_validate_json(paths[0].read_bytes())
    assert saved.source_page_count == 2 and set(saved.document.pages) == {1, 2}
    assert saved.scope == "full_document"
    assert "page_range" not in converter.convert.call_args.kwargs
    before = paths[0].read_bytes()
    assert capture_local_corpus(["W123"], tmp_path, tmp_path / "out", converter) == paths
    assert converter.convert.call_count == 1
    assert paths[0].read_bytes() == before and source.read_bytes() == original
    source.write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="cached source"):
        capture_local_corpus(["W123"], tmp_path, tmp_path / "out", converter)


@pytest.mark.parametrize("status", [ConversionStatus.FAILURE, ConversionStatus.PARTIAL_SUCCESS])
def test_failed_conversion_cannot_be_cached(tmp_path: Path, status: ConversionStatus) -> None:
    _, converter = _inputs(tmp_path)
    converter.convert.return_value.status = status
    with pytest.raises(ConversionError):
        capture_local_corpus(["W123"], tmp_path, tmp_path / "out", converter)
    assert not (tmp_path / "out").exists()


def test_missing_sources_and_incomplete_document_fail(tmp_path: Path) -> None:
    _, converter = _inputs(tmp_path)
    with pytest.raises(ValueError, match="found 0"):
        capture_local_corpus(["W123", "W456"], tmp_path, tmp_path / "out", converter)
    converter.convert.assert_not_called()
    converter.convert.return_value.document.pages.pop(2)
    with pytest.raises(ValueError, match="every physical"):
        capture_local_corpus(["W123"], tmp_path, tmp_path / "out", converter)
    assert not (tmp_path / "out").exists()


def test_changed_settings_and_corrupt_caches_are_not_reused(tmp_path: Path) -> None:
    _, converter = _inputs(tmp_path)
    (path,) = capture_local_corpus(["W123"], tmp_path, tmp_path / "out", converter)
    converter.format_to_options[InputFormat.PDF].pipeline_options.do_ocr = False
    with pytest.raises(ValueError, match="parser settings"):
        capture_local_corpus(["W123"], tmp_path, tmp_path / "out", converter)
    path.write_text("{}")
    with pytest.raises(ValueError):
        capture_local_corpus(["W123"], tmp_path, tmp_path / "out", converter)
    assert converter.convert.call_count == 1


def test_identical_settings_resume_in_another_python_process(tmp_path: Path) -> None:
    _, converter = _inputs(tmp_path)
    capture_local_corpus(["W123"], tmp_path, tmp_path / "out", converter)
    code = (
        "import sys; from pathlib import Path; "
        "from steel_slag_slm.data.pdf_parsing import build_docling_converter; "
        "from steel_slag_slm.pipelines.capture_table_corpus import capture_local_corpus; "
        "p=Path(sys.argv[1]); "
        "capture_local_corpus(['W123'],p,p/'out',build_docling_converter(include_figures=True))"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "42"},
    )
    assert converter.convert.call_count == 1
