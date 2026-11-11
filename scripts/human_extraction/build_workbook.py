"""Rebuild the human-extraction workbook and status tracker from the per-paper JSON files.

Each human_extracted_data/extractions/<openalex_id>.json holds
{"openalex_id", "status", "notes", "sheets": {tab name: [row dicts keyed by column header]}}.
The workbook copies the Gemini export layout (same tabs, headers and vocab sheets) so the two
can be compared cell by cell.

Run: uv run python scripts/human_extraction/build_workbook.py
"""

import csv
import json
import sys
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.workbook import Workbook

from steel_slag_slm.config import PROJECT_ROOT

OUTPUT_DIR = PROJECT_ROOT / "human_extracted_data"
EXTRACTIONS_DIR = OUTPUT_DIR / "extractions"
TRACKER_PATH = OUTPUT_DIR / "status_tracker.csv"
WORKBOOK_PATH = OUTPUT_DIR / "human_extracted_data.xlsx"
# A Gemini export, used only for its tab layout; its data rows are cleared
TEMPLATE_PATH = PROJECT_ROOT / "reports" / "extraction_sample_W2068066265.xlsx"
DATA_TABS = [
    "1 Scientific Literature",
    "2 Feedstock Chemistry",
    "3 Processing",
    "4 Particle Size",
    "5 Activation",
    "6 Concrete Performance",
    "7 Durability",
    "8 Carbon Uptake",
    "9 Citations & Provenance",
]


def _empty_template() -> tuple[Workbook, dict[str, list[str]]]:
    """Load the template with its data rows removed; return it and each tab's headers."""
    workbook = openpyxl.load_workbook(TEMPLATE_PATH)
    headers = {}
    for tab in DATA_TABS:
        sheet = workbook[tab]
        if sheet.max_row > 1:
            sheet.delete_rows(2, sheet.max_row)
        headers[tab] = [str(cell.value) for cell in sheet[1]]
    return workbook, headers


def _check_record(name: str, record: dict[str, Any], headers: dict[str, list[str]]) -> list[str]:
    """List every tab or column in one extraction that the workbook does not have."""
    problems = []
    for tab, rows in record["sheets"].items():
        if tab not in headers:
            problems.append(f"{name}: unknown tab {tab!r}")
            continue
        for row in rows:
            unknown = set(row) - set(headers[tab])
            if unknown:
                problems.append(f"{name}: {tab} has unknown columns {sorted(unknown)}")
    return problems


def _update_tracker(records: dict[str, dict[str, Any]]) -> tuple[int, int]:
    """Copy each extraction's status, cleaning and notes into the tracker; return (done, total).

    reference_cleaned is the date a paper's rows were checked against its PDF and brought
    to the current extraction rules (check_reference.py); blank means not yet.
    """
    with TRACKER_PATH.open() as tracker:
        rows = list(csv.DictReader(tracker))
    for row in rows:
        record = records.get(row["openalex_id"])
        if record:
            row["extraction_status"] = record.get("status", "Extracted")
            row["reference_cleaned"] = record.get("reference_cleaned", "")
            row["notes"] = record.get("notes", "")
    fieldnames = [name for name in rows[0] if name != "notes"] + ["notes"]
    with TRACKER_PATH.open("w", newline="") as tracker:
        writer = csv.DictWriter(tracker, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    done = sum(row["extraction_status"] == "Extracted" for row in rows)
    return done, len(rows)


def main() -> None:
    workbook, headers = _empty_template()
    records: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for path in sorted(EXTRACTIONS_DIR.glob("*.json")):
        record = json.loads(Path(path).read_text())
        records[record["openalex_id"]] = record
        problems += _check_record(path.name, record, headers)
    if problems:
        sys.exit("\n".join(problems))

    for record in records.values():
        for tab, rows in record["sheets"].items():
            for row in rows:
                workbook[tab].append([row.get(column) for column in headers[tab]])
    workbook.save(WORKBOOK_PATH)

    done, total = _update_tracker(records)
    print(f"Done {done}/{total}, left {total - done}")


if __name__ == "__main__":
    main()
