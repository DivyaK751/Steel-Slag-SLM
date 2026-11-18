from hashlib import sha256
from pathlib import Path

import pymupdf
import pytest
from docling_core.types.doc.base import Size
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableCell, TableData
from docling_core.types.doc.labels import DocItemLabel

from steel_slag_slm.pipelines.audit_table_corpus import CorpusCoverage, audit_table_corpus
from steel_slag_slm.pipelines.capture_table_corpus import FullDocumentCapture


def _capture(root: Path, paper_id: str = "W123", options: str = "settings") -> Path:
    pdf_dir = root / "pdfs"
    pdf_dir.mkdir(exist_ok=True)
    source = pdf_dir / f"{paper_id} - Title.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(source)
    document = DoclingDocument(name="test")
    document.add_page(page_no=1, size=Size(width=600, height=800))
    caption = document.add_text(label=DocItemLabel.CAPTION, text="Table 1")
    table = document.add_table(
        data=TableData(
            num_rows=2,
            num_cols=2,
            table_cells=[
                TableCell(
                    text=text,
                    column_header=row == 0,
                    start_row_offset_idx=row,
                    end_row_offset_idx=row + 1,
                    start_col_offset_idx=col,
                    end_col_offset_idx=col + 1,
                )
                for row, values in enumerate([["Mix", "Strength (MPa)"], ["SS", ">45.14"]])
                for col, text in enumerate(values)
            ],
        )
    )
    table.captions.append(caption.get_ref())
    report = FullDocumentCapture(
        paper_id=paper_id,
        source_filename=source.name,
        source_sha256=sha256(source.read_bytes()).hexdigest(),
        source_page_count=1,
        docling_version="test",
        docling_core_version="test",
        pipeline_options_sha256=options,
        document=document,
    )
    path = root / f"{paper_id}.json"
    path.write_text(report.model_dump_json())
    return path


def test_coverage_roundtrip_preserves_sources_and_refuses_overwrite(tmp_path: Path) -> None:
    path = _capture(tmp_path)
    before = path.read_bytes()
    output = tmp_path / "audit.json"
    report = audit_table_corpus(["W123"], tmp_path / "pdfs", tmp_path, output)
    assert CorpusCoverage.model_validate_json(output.read_bytes()) == report
    (paper,) = report.papers
    assert paper.capture_sha256 == sha256(before).hexdigest()
    assert paper.physical_pages == 1
    assert paper.tables[0].candidate_row_indices == [1]
    assert not paper.tables[0].issues and path.read_bytes() == before
    with pytest.raises(FileExistsError):
        audit_table_corpus(["W123"], tmp_path / "pdfs", tmp_path, output)


@pytest.mark.parametrize("problem", ["missing", "source", "settings", "geometry", "blocked"])
def test_coverage_explicitly_handles_invalid_or_blocked_inputs(
    tmp_path: Path, problem: str
) -> None:
    path = _capture(tmp_path)
    output = tmp_path / "audit.json"
    ids = ["W123"]
    if problem == "missing":
        path.unlink()
    elif problem == "source":
        source = tmp_path / "pdfs/W123 - Title.pdf"
        source.write_bytes(source.read_bytes() + b"\n")
    elif problem == "settings":
        _capture(tmp_path, "W456", "other settings")
        ids.append("W456")
    else:
        capture = FullDocumentCapture.model_validate_json(path.read_bytes())
        if problem == "geometry":
            capture.document.tables[0].data.table_cells[0].row_span = 2
        else:
            capture.document.tables[0].data.table_cells[0].text = ""
        path.write_text(capture.model_dump_json())
    if problem == "blocked":
        report = audit_table_corpus(ids, tmp_path / "pdfs", tmp_path, output)
        assert not report.papers[0].tables[0].candidate_row_indices
        assert report.papers[0].tables[0].issues
    else:
        with pytest.raises((ValueError, FileNotFoundError)):
            audit_table_corpus(ids, tmp_path / "pdfs", tmp_path, output)
        assert not output.exists()
