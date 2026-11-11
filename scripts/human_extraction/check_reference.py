"""List the cells of hand-extracted papers to review: rule breaks and possibly hidden values.

A reference row is scored against Gemini's rows, so it must follow the same rules:
numbers only in unit columns, fixed labels in vocabulary columns, one experimental
condition per row, and every reported value in its own column rather than in notes.
Words in a unit column or a label outside the vocabulary are always errors; a range or list
of conditions, or a series in notes, is a prompt to check the paper (a ramp such as
900-400 °C, or a range the paper states, is fine).

Run: uv run python scripts/human_extraction/check_reference.py W2014482815 [...]
     uv run python scripts/human_extraction/check_reference.py --uncleaned
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from steel_slag_slm.config import PROJECT_ROOT
from steel_slag_slm.data.extraction_rows import NUMERIC_VALUE
from steel_slag_slm.data.extraction_tables import (
    ALLOWED_VALUES,
    LIST_COLUMNS,
    column_unit,
    is_filled,
    split_list,
)

EXTRACTIONS_DIR = PROJECT_ROOT / "human_extracted_data" / "extractions"
# Test_Category labels the extraction prompt (v8+) allows
TEST_CATEGORIES = {
    "Soundness", "Sulfate attack", "Chloride penetration", "Freeze-thaw", "ASR",
    "Carbonation", "Water absorption", "Permeability", "Shrinkage", "Leaching",
    "Fire resistance", "Other",
}  # fmt: skip
# Columns that name one condition of a row: a range or list here usually means several
# conditions were collapsed into one row
CONDITION_COLUMNS = {
    "SCM_Replacement_pct", "Water_Binder_Ratio", "Activator_Dosage", "Activation_Time",
    "Activation_Temperature_C", "Liquid_to_Solid_Ratio", "Grinding_Time", "Test_Age",
    "Carbonation_Temperature_C", "Carbonation_Time", "CO2_Pressure", "CO2_Concentration",
}  # fmt: skip
_NUMBER = r"\d+(?:\.\d+)?"
# A number on its own, not a digit inside a formula such as CO2 or H2O
_FREE_NUMBER = rf"(?<![A-Za-z]){_NUMBER}(?![A-Za-z])"
_RANGE = re.compile(rf"^\s*~?{_NUMBER}\s*(?:-|–|to)\s*{_NUMBER}")
_LIST = re.compile(rf"{_FREE_NUMBER}[^;,/]*[;,/]\s*[^;,/]*{_FREE_NUMBER}")
# "500 °C: 7.95", "3 d 27", "28 d: 41.2" ... three or more in a note mean a hidden series
_SERIES_ITEM = re.compile(rf"{_NUMBER}\s*(?:°C|℃|h|d|min|MPa|%|bar)?\s*[:=]\s*{_NUMBER}")
_SKIP_TABS = ("1 ", "9 ")


def check_cell(column: str, value: str) -> str | None:
    """Why one reference cell needs fixing, or None if it follows the rules."""
    text = value.strip()
    if column_unit(column) and not NUMERIC_VALUE.match(text):
        return "unit column holds words"
    allowed = ALLOWED_VALUES.get(column)
    if allowed and column != "Steel_Slag_Type":
        items = split_list(text) if column in LIST_COLUMNS else [text]
        if any(item not in allowed for item in items):
            return "not a vocabulary label"
    if column == "Test_Category" and text not in TEST_CATEGORIES:
        return "not a test category"
    if column in CONDITION_COLUMNS and (_RANGE.match(text) or _LIST.search(text)):
        return "range or list of conditions"
    if column.endswith("Notes") and len(_SERIES_ITEM.findall(text)) >= 3:
        return "series of values in notes"
    return None


def check_paper(record: dict) -> Counter:
    """Count (sheet, column, problem, example value) over one paper's data rows."""
    problems: Counter = Counter()
    for tab, rows in record["sheets"].items():
        if tab.startswith(_SKIP_TABS):
            continue
        for row in rows:
            for column, value in row.items():
                if not is_filled(value):
                    continue
                problem = check_cell(column, str(value))
                if problem:
                    problems[(tab, column, problem, str(value)[:70])] += 1
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("paper_ids", nargs="*", help="OpenAlex IDs to check")
    parser.add_argument("--uncleaned", action="store_true", help="check every uncleaned paper")
    args = parser.parse_args()
    paths = sorted(EXTRACTIONS_DIR.glob("*.json"))
    records = {path.stem: json.loads(Path(path).read_text()) for path in paths}
    paper_ids = args.paper_ids or [
        paper for paper, record in records.items()
        if args.uncleaned and not record.get("reference_cleaned")
    ]  # fmt: skip
    for paper in paper_ids:
        problems = check_paper(records[paper])
        cleaned = records[paper].get("reference_cleaned") or "not cleaned"
        print(f"== {paper} ({cleaned}): {sum(problems.values())} cells to review")
        for (tab, column, problem, value), count in sorted(problems.items()):
            print(f"  {count:3d}x {tab[:18]:18s} {column:27s} {problem:28s} {value}")


if __name__ == "__main__":
    main()
