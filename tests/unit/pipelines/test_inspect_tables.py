from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock

import pymupdf
import pytest
from docling.datamodel.base_models import ConversionStatus
from docling.exceptions import ConversionError
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableData

from steel_slag_slm.config import TableInspectionCase, load_table_inspection_cases
from steel_slag_slm.pipelines.inspect_tables import TableInspectionReport, inspect_local_tables


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "W123 - title.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(source)
    return source


def _converter(status: ConversionStatus = ConversionStatus.SUCCESS) -> MagicMock:
    converter = MagicMock()
    converter.convert.return_value.status = status
    converter.convert.return_value.document = DoclingDocument(name="empty")
    return converter


@pytest.mark.parametrize("has_table", [False, True])
def test_inspection_records_tables_and_does_not_overwrite(tmp_path: Path, has_table: bool) -> None:
    source = _source(tmp_path)
    original = source.read_bytes()
    output = tmp_path / "inspection"
    case = TableInspectionCase(paper_id="W123", page=1, target="Table 1")
    converter = _converter()
    if has_table:
        converter.convert.return_value.document.add_table(
            data=TableData(num_rows=0, num_cols=0, table_cells=[])
        )
    reports = inspect_local_tables([case], tmp_path, output, converter)
    saved = TableInspectionReport.model_validate_json((output / "W123.json").read_text())
    assert saved == reports[0]
    assert saved.status == ("captured" if has_table else "no_tables")
    assert len(saved.tables) == int(has_table)
    assert saved.source_sha256 == sha256(original).hexdigest()
    assert saved.docling_version and saved.docling_core_version
    assert saved.source_filename == source.name
    assert saved.source_page_count == 1
    assert converter.convert.call_args.kwargs["page_range"] == (1, 1)
    assert source.read_bytes() == original
    with pytest.raises(FileExistsError):
        inspect_local_tables([case], tmp_path, output, converter)
    assert converter.convert.call_count == 1


def test_inspection_rejects_ambiguous_pdfs_and_longer_ids(tmp_path: Path) -> None:
    source = _source(tmp_path)
    case = TableInspectionCase(paper_id="W12", page=1, target="Table 1")
    with pytest.raises(ValueError, match="found 0"):
        inspect_local_tables([case], tmp_path, tmp_path / "out", _converter())
    source.with_name("W123 - duplicate.pdf").write_bytes(source.read_bytes())
    case = TableInspectionCase(paper_id="W123", page=1, target="Table 1")
    with pytest.raises(ValueError, match="found 2"):
        inspect_local_tables([case], tmp_path, tmp_path / "out", _converter())


def test_inspection_rejects_partial_conversion_and_out_of_bounds_pages(tmp_path: Path) -> None:
    _source(tmp_path)
    case = TableInspectionCase(paper_id="W123", page=2, target="Table 1")
    with pytest.raises(ValueError, match="exceeds"):
        inspect_local_tables([case], tmp_path, tmp_path / "out", _converter())
    case.page = 1
    with pytest.raises(ConversionError, match="conversion status"):
        inspect_local_tables(
            [case], tmp_path, tmp_path / "out", _converter(ConversionStatus.PARTIAL_SUCCESS)
        )
    assert not (tmp_path / "out/W123.json").exists()


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "null",
        "{}",
        "- {paper_id: W123, page: 0, target: Table 1}",
        "- {paper_id: W123, page: 1, target: Table 1}\n" * 2,
    ],
)
def test_case_loader_rejects_invalid_selections(tmp_path: Path, content: str) -> None:
    config = tmp_path / "cases.yaml"
    config.write_text(content)
    with pytest.raises(ValueError):
        load_table_inspection_cases(config)
