"""Read the hand-extracted reference workbook (human_extracted_data/) into sheet rows."""

from collections import defaultdict
from collections.abc import Collection
from pathlib import Path

import openpyxl

from steel_slag_slm.data.extraction_tables import SHEET_TABLES

# The reference prefixes paper IDs (SRC-W123); the extraction tables use the bare OpenAlex ID
_SOURCE_PREFIX = "SRC-"

SheetRows = dict[tuple[str, str], list[dict[str, str | None]]]


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def read_reference_rows(path: Path, paper_ids: Collection[str]) -> SheetRows:
    """Return the rows of the given papers, keyed by (paper ID, table), in workbook order.

    Rows map workbook headers to values as text; a paper with no rows in a sheet is absent.

    Raises:
        ValueError: If a sheet's headers differ from the extraction tables' headers.
    """
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows: SheetRows = defaultdict(list)
    for sheet_table in SHEET_TABLES:
        values = workbook[sheet_table.sheet].iter_rows(values_only=True)
        headers = tuple(str(header) for header in next(values))
        if headers != sheet_table.headers:
            raise ValueError(f"{path.name}: sheet {sheet_table.sheet!r} has other headers")
        for row in values:
            record = dict(zip(headers, map(_text, row), strict=True))
            paper_id = (record["Source_ID"] or "").removeprefix(_SOURCE_PREFIX)
            if paper_id in paper_ids:
                rows[(paper_id, sheet_table.table)].append(record)
    workbook.close()
    return dict(rows)
