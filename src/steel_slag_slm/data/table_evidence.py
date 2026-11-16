"""Render review-only evidence from explicit native table relationships."""

from typing import Literal

from docling_core.types.doc.common.reference import ProvenanceItem
from pydantic import BaseModel

from steel_slag_slm.data.table_inspection import InspectedTable


class RenderingIssue(BaseModel):
    """Identify a blocked table or zero-based body row without hiding missing evidence."""

    row_index: int | None = None
    reason: str


class EvidenceRow(BaseModel):
    """Keep a candidate passage with its zero-based row and native cell-list indices."""

    row_index: int
    cell_indices: list[int]
    text: str


class TableEvidence(BaseModel):
    """Keep unreviewed passages linked to the parent inspection table."""

    reference: str
    provenance: list[ProvenanceItem]
    review_status: Literal["unreviewed"] = "unreviewed"
    rows: list[EvidenceRow]
    issues: list[RenderingIssue]


def render_table_evidence(table: InspectedTable) -> TableEvidence:
    """Render complete rows using native header paths, without semantic repair.

    Expand only explicit row spans. Block missing headers, missing context, incomplete rows
    and body cells spanning columns. A candidate is not a correctness certification:
    OCR, units, factor codes and material identities still require source review.
    """
    result = TableEvidence(
        reference=table.reference, provenance=table.provenance, rows=[], issues=[]
    )
    data = table.data
    cells = data.table_cells
    grid: dict[tuple[int, int], int] = {}
    for index, cell in enumerate(cells):
        r0, r1 = cell.start_row_offset_idx, cell.end_row_offset_idx
        c0, c1 = cell.start_col_offset_idx, cell.end_col_offset_idx
        if not (
            0 <= r0 < r1 <= data.num_rows
            and 0 <= c0 < c1 <= data.num_cols
            and r1 - r0 == cell.row_span
            and c1 - c0 == cell.col_span
        ):
            raise ValueError(f"{table.reference}: invalid geometry for cell {index}")
        for row in range(r0, r1):
            for col in range(c0, c1):
                if (row, col) in grid:
                    raise ValueError(f"{table.reference}: overlapping cells at ({row}, {col})")
                grid[row, col] = index

    headers = sorted(
        (c for c in cells if c.column_header),
        key=lambda c: (c.start_row_offset_idx, c.start_col_offset_idx),
    )
    header_end = max((c.end_row_offset_idx for c in headers), default=0)
    if not headers or any(
        not c.column_header and c.start_row_offset_idx < header_end for c in cells
    ):
        result.issues.append(RenderingIssue(reason="Missing or interleaved column headers"))
        return result
    for upper in headers:
        for lower in headers:
            if (
                upper.end_row_offset_idx <= lower.start_row_offset_idx
                and (
                    max(upper.start_col_offset_idx, lower.start_col_offset_idx)
                    < min(upper.end_col_offset_idx, lower.end_col_offset_idx)
                )
                and not (
                    upper.start_col_offset_idx <= lower.start_col_offset_idx
                    and lower.end_col_offset_idx <= upper.end_col_offset_idx
                )
            ):
                result.issues.append(RenderingIssue(reason="Non-nested column headers"))
                return result
    # A full-width header above narrower headers is table-level text, not a column label.
    context_headers = [
        c
        for c in headers
        if c.col_span == data.num_cols
        and any(h.start_row_offset_idx >= c.end_row_offset_idx for h in headers)
    ]
    context = [table.caption] if table.caption.strip() else []
    context.extend(c.text for c in context_headers if c.text.strip() and c.text not in context)
    paths = [
        [
            c.text
            for c in headers
            if c not in context_headers
            and c.text.strip()
            and c.start_col_offset_idx <= col < c.end_col_offset_idx
        ]
        for col in range(data.num_cols)
    ]
    if not context:
        result.issues.append(RenderingIssue(reason="Missing caption or full-width table context"))
    if any(not c.text.strip() for c in headers if c not in context_headers):
        result.issues.append(RenderingIssue(reason="Blank column header"))
    if not paths or any(not path for path in paths):
        result.issues.append(RenderingIssue(reason="Missing column header path"))
    if result.issues:
        return result

    prefix = "\n".join(context)
    notes = "\nFootnotes: " + " | ".join(table.footnotes) if table.footnotes else ""
    for row in range(header_end, data.num_rows):
        indices = [grid.get((row, col)) for col in range(data.num_cols)]
        if any(index is None or not cells[index].text.strip() for index in indices):
            result.issues.append(
                RenderingIssue(row_index=row, reason="Missing cell or row context")
            )
            continue
        row_indices = [index for index in indices if index is not None]
        if any(cells[index].col_span != 1 or cells[index].row_section for index in row_indices):
            result.issues.append(
                RenderingIssue(row_index=row, reason="Spanning or section body cell")
            )
            continue
        fields = [
            f"{' > '.join(path)}: {cells[index].text}"
            for path, index in zip(paths, row_indices, strict=True)
        ]
        result.rows.append(
            EvidenceRow(
                row_index=row,
                cell_indices=row_indices,
                text=f"{prefix}\n" + "\n".join(fields) + notes,
            )
        )
    if not result.rows and not result.issues:
        result.issues.append(RenderingIssue(reason="No body rows"))
    return result
