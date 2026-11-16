"""Write local, review-only table passages from saved inspection reports."""

import logging
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from steel_slag_slm.data.table_evidence import TableEvidence, render_table_evidence
from steel_slag_slm.pipelines.inspect_tables import TableInspectionReport

logger = logging.getLogger(__name__)


class EvidenceReport(BaseModel):
    """Link candidate passages to the exact parent inspection and original PDF."""

    schema_version: Literal[1] = 1
    inspection_filename: str
    inspection_sha256: str
    paper_id: str
    source_sha256: str
    tables: list[TableEvidence]


def render_local_table_evidence(inspection_dir: Path, output_dir: Path) -> list[EvidenceReport]:
    """Validate all inputs and render reports before writing new files exclusively."""
    paths = sorted(inspection_dir.glob("*.json"))
    if not paths:
        raise ValueError(f"No inspection JSON reports in {inspection_dir}")
    reports: list[EvidenceReport] = []
    seen: set[str] = set()
    for path in paths:
        content = path.read_bytes()
        inspection = TableInspectionReport.model_validate_json(content)
        paper_id = inspection.case.paper_id
        if paper_id in seen:
            raise ValueError(f"Duplicate inspection for {paper_id}")
        if (output_dir / f"{paper_id}.json").exists():
            raise FileExistsError(f"Evidence already exists for {paper_id} in {output_dir}")
        seen.add(paper_id)
        tables = [render_table_evidence(table) for table in inspection.tables]
        for table in tables:
            if table.issues:
                logger.warning("%s %s: %s", paper_id, table.reference, table.issues)
        if not any(table.rows for table in tables):
            logger.warning("%s: no candidate evidence rows", paper_id)
        reports.append(
            EvidenceReport(
                inspection_filename=path.name,
                inspection_sha256=sha256(content).hexdigest(),
                paper_id=paper_id,
                source_sha256=inspection.source_sha256,
                tables=tables,
            )
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        with (output_dir / f"{report.paper_id}.json").open("x", encoding="utf-8") as destination:
            destination.write(report.model_dump_json(indent=2) + "\n")
    return reports
