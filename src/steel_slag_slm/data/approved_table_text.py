"""Render contextual rows from Markdown already saved in approved parses."""

import json
from dataclasses import dataclass, replace
from re import compile as re_compile
from typing import Any

from docling_core.types.doc.base import BoundingBox, CoordOrigin
from docling_core.types.doc.common.reference import ProvenanceItem

from steel_slag_slm.data.markdown_tables import (
    MarkdownGrid,
    ResolvedTable,
    TableShapeError,
    is_value_cell,
    parse_grid,
    project_row,
    resolve_header,
)
from steel_slag_slm.data.parsed_manifest import ContentBlock, ParsedManifest, Placement
from steel_slag_slm.data.table_evidence import EvidenceRow, RenderingIssue, TableEvidence

# Same reference as the first backfill: consumers read the content-block index after the colon.
REFERENCE_PREFIX = "approved-markdown:"
_CAPTION = re_compile(r"(?i)^\s*(table|tabla|tab\.)\s*[-.]?\s*([0-9]+|[ivxl]+)\b")
GROUP_LABEL = "Group"  # prefixes a spanning row label so it reads apart from the caption
_LOOKBACK_BLOCKS = 3  # how far above a table an unlinked caption or heading may sit
_PROSE_LABELS = {"text", "section_header", "list_item"}
_MIN_VALUE_SHARE = 0.3  # uncaptioned grids with fewer measurements are usually prose or lists
_MAX_MEASUREMENT_CHARS = 20  # "45 MPa" or "28 d"; longer cells holding a digit are usually prose
_JUNK_PATTERNS = {
    "Table of contents": re_compile(r"\.{6,}|…{3,}"),
    "Reference list": re_compile(r"\[\d+\]|\bdoi\b|\bet al\."),
}


@dataclass(frozen=True, slots=True)
class _Layout:
    """What a following page's continuation of this table inherits."""

    context: tuple[str, ...]
    header: tuple[str, ...]
    source_header: tuple[str, ...]
    columns: tuple[int, ...]
    last_page: int


def uses_backfill_rows(tables: list[TableEvidence]) -> bool:
    """Return whether every table was written by this backfill, so it may be re-rendered."""
    return all(table.reference.startswith(REFERENCE_PREFIX) for table in tables)


def render_approved_tables(blocks: list[ContentBlock]) -> list[TableEvidence]:
    """Render one evidence record per table block, in document order.

    Each row passage holds the table's context lines (caption, else an unlinked caption or
    heading just above, else a page label), any group label row above it, and one
    `header: value` line per non-blank cell. Original strings are kept; nothing is inferred
    from prose. Tables and rows that cannot be rendered are listed as issues, never dropped.
    """
    tables: list[TableEvidence] = []
    previous: _Layout | None = None
    for index, block in enumerate(blocks):
        if block.kind != "table":
            continue
        evidence = TableEvidence(
            reference=f"{REFERENCE_PREFIX}{index}",
            provenance=[_provenance(placement) for placement in block.placements],
            rows=[],
            issues=[],
        )
        try:
            layout = _render_table(blocks, index, previous, evidence)
        except TableShapeError as error:
            evidence.issues.append(RenderingIssue(reason=str(error)))
            layout = None
        tables.append(evidence)
        previous = layout
    return tables


def enrich_approved_json(content: bytes) -> bytes:
    """Return the manifest with only `contextual_tables` added or re-rendered.

    Raises:
        ValueError: If the manifest is invalid or holds contextual rows from another source.
    """
    original = json.loads(content)
    manifest = ParsedManifest.model_validate(original)
    if manifest.contextual_tables is not None and not uses_backfill_rows(
        manifest.contextual_tables
    ):
        raise ValueError("Manifest holds contextual_tables from another source; not replacing")
    tables = render_approved_tables(manifest.content_blocks)
    output: dict[str, Any] = {
        **original,
        "contextual_tables": [table.model_dump(mode="json") for table in tables],
    }
    encoded = (json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    ParsedManifest.model_validate_json(encoded)
    return encoded


def _render_table(
    blocks: list[ContentBlock], index: int, previous: _Layout | None, evidence: TableEvidence
) -> _Layout:
    block = blocks[index]
    if block.label == "document_index":
        raise TableShapeError("Table of contents")
    if not block.placements:
        raise TableShapeError("No source location")
    lines = block.text.splitlines()
    grid = parse_grid([line for line in lines if line.strip().startswith("|")])
    pages = [placement.page_number for placement in block.placements]
    caption = " ".join(line.strip() for line in lines if _is_prose(line))
    try:
        resolved: ResolvedTable | None = resolve_header(grid)
    except TableShapeError as error:
        resolved, shape_error = None, error  # a continuation's first line may be data

    context = [caption] if caption else []
    context += resolved.context if resolved else ()
    if not _has_caption(context):
        rows = _continuation_rows(blocks, index, previous, grid, min(pages))
        if previous is not None and rows is not None:
            _add_rows(evidence, list(previous.context), previous.header, rows)
            return replace(previous, last_page=max(pages))
    if resolved is None:
        raise shape_error
    if not _has_caption(context):
        context = _nearby_context(blocks, index) + context
    _reject_junk(grid, has_caption=_has_caption(context))
    context = context or [f"Table on page {min(pages)}"]
    # Keep row indices pointing at the saved Markdown body, before header rows were consumed.
    first_row = len(grid.body) - len(resolved.body)
    _add_rows(evidence, context, resolved.header, resolved.body, first_row)
    return _Layout(
        context=tuple(context),
        header=resolved.header,
        source_header=grid.header,
        columns=resolved.columns,
        last_page=max(pages),
    )


def _is_prose(line: str) -> bool:
    return bool(line.strip()) and not line.strip().startswith("|")


def _has_caption(context: list[str]) -> bool:
    return any(_CAPTION.match(line) for line in context)


def _continuation_rows(
    blocks: list[ContentBlock],
    index: int,
    previous: _Layout | None,
    grid: MarkdownGrid,
    first_page: int,
) -> tuple[tuple[str, ...], ...] | None:
    """Read an uncaptioned table straight after a same-width table as its next page's rows."""
    if previous is None or len(grid.header) != len(previous.source_header):
        return None
    if first_page <= previous.last_page:
        return None
    between = blocks[_previous_table(blocks, index) + 1 : index]
    if any(block.label in _PROSE_LABELS for block in between):
        return None
    repeated = _same_cells(grid.header, previous.source_header)
    if not repeated and not any(is_value_cell(cell) for cell in grid.header):
        return None
    # Without a repeated header, the "header" line Docling saved is the first data row.
    rows = grid.body if repeated else (grid.header, *grid.body)
    width = len(previous.source_header)
    return tuple(project_row(row, previous.columns, width) for row in rows)


def _same_cells(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    return [cell.casefold() for cell in first] == [cell.casefold() for cell in second]


def _previous_table(blocks: list[ContentBlock], index: int) -> int:
    return next(i for i in range(index - 1, -1, -1) if blocks[i].kind == "table")


def _nearby_context(blocks: list[ContentBlock], index: int) -> list[str]:
    """Return an unlinked caption, else a section heading, from just above the table."""
    above: list[ContentBlock] = []
    for block in reversed(blocks[max(0, index - _LOOKBACK_BLOCKS) : index]):
        if block.kind == "table":
            break  # anything further up belongs to the earlier table
        above.append(block)
    for block in above:
        if _CAPTION.match(block.text):
            return [block.text.strip()]
    # Unlinked caption blocks without a table number often belong to a nearby figure instead.
    headings = [block.text.strip() for block in above if block.label == "section_header"]
    return headings[:1]


def _reject_junk(grid: MarkdownGrid, *, has_caption: bool) -> None:
    text = " ".join(" ".join(row) for row in (grid.header, *grid.body))
    if _JUNK_PATTERNS["Table of contents"].search(text):
        raise TableShapeError("Table of contents")
    if has_caption:
        return
    if _JUNK_PATTERNS["Reference list"].search(text):
        raise TableShapeError("Reference list")
    cells = [cell for row in grid.body for cell in row if cell]
    numeric = sum(map(_is_measurement, cells))
    if not cells or numeric < _MIN_VALUE_SHARE * len(cells):
        raise TableShapeError("Uncaptioned grid without reported values")


def _is_measurement(cell: str) -> bool:
    short_with_digit = len(cell) <= _MAX_MEASUREMENT_CHARS and any(c.isdigit() for c in cell)
    return is_value_cell(cell) or short_with_digit


def _is_group_row(row: tuple[str, ...]) -> bool:
    """Return whether one label spans the row: alone in its first cell, or (as Docling
    copies spanned text) repeated in every cell."""
    if len(row) <= 2 or not row[0]:
        return False
    return not any(row[1:]) or len(set(row)) == 1


def _add_rows(
    evidence: TableEvidence,
    context: list[str],
    header: tuple[str, ...],
    body: tuple[tuple[str, ...], ...],
    first_row: int = 0,
) -> None:
    group: str | None = None
    has_row_labels = False
    for row_index, row in enumerate(body, start=first_row):
        if len(row) != len(header):
            evidence.issues.append(
                RenderingIssue(row_index=row_index, reason="Row width differs from header")
            )
            continue
        if not any(row) or _same_cells(row, header):
            continue  # blank spacer or a header repeated after a page break
        if _is_group_row(row):
            group = row[0]  # a label spanning the row groups the rows below it
            continue
        if not row[0] and has_row_labels:
            # Usually a merged label cell; the label is not copied down from the row above.
            evidence.issues.append(RenderingIssue(row_index=row_index, reason="Blank row label"))
            continue
        has_row_labels = has_row_labels or bool(row[0])
        fields = [
            f"{label}: {value}" if label else value
            for label, value in zip(header, row, strict=True)
            if value
        ]
        if len(fields) < 2:
            evidence.issues.append(RenderingIssue(row_index=row_index, reason="Row has no values"))
            continue
        lines = [*context, *([f"{GROUP_LABEL}: {group}"] if group else []), *fields]
        evidence.rows.append(
            EvidenceRow(row_index=row_index, cell_indices=[], text="\n".join(lines))
        )


def _provenance(placement: Placement) -> ProvenanceItem:
    left, top, right, bottom = placement.bbox
    return ProvenanceItem(
        page_no=placement.page_number,
        bbox=BoundingBox(
            l=left, t=top, r=right, b=bottom, coord_origin=CoordOrigin(placement.coordinate_origin)
        ),
        charspan=(0, 0),
    )
