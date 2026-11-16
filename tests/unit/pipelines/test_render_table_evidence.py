from hashlib import sha256
from pathlib import Path

import pytest

from steel_slag_slm.config import TableInspectionCase
from steel_slag_slm.pipelines.inspect_tables import TableInspectionReport
from steel_slag_slm.pipelines.render_table_evidence import (
    EvidenceReport,
    render_local_table_evidence,
)


def test_local_rendering_links_exact_input_and_refuses_overwrite(tmp_path: Path) -> None:
    report = TableInspectionReport(
        case=TableInspectionCase(paper_id="W123", page=1, target="Table 1"),
        source_filename="W123.pdf",
        source_sha256="abc",
        source_page_count=1,
        docling_version="test",
        docling_core_version="test",
        status="no_tables",
        tables=[],
    )
    source = tmp_path / "inspection.json"
    source.write_text(report.model_dump_json())
    original = source.read_bytes()
    out = tmp_path / "out"
    (saved,) = render_local_table_evidence(tmp_path, out)
    assert saved == EvidenceReport.model_validate_json((out / "W123.json").read_text())
    assert saved.inspection_sha256 == sha256(original).hexdigest()
    assert saved.source_sha256 == "abc" and source.read_bytes() == original
    with pytest.raises(FileExistsError):
        render_local_table_evidence(tmp_path, out)
    source.with_name("duplicate.json").write_bytes(original)
    with pytest.raises(ValueError, match="Duplicate inspection"):
        render_local_table_evidence(tmp_path, tmp_path / "fresh")
    assert not (tmp_path / "fresh").exists()


def test_missing_or_invalid_reports_fail_before_writing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="No inspection"):
        render_local_table_evidence(tmp_path, out)
    (tmp_path / "invalid.json").write_text("{}")
    with pytest.raises(ValueError):
        render_local_table_evidence(tmp_path, out)
    assert not out.exists()
