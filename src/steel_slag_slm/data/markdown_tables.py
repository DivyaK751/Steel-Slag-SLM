"""Read Docling's saved GitHub-style Markdown tables back into header paths and body rows."""

from collections import Counter
from dataclasses import dataclass
from re import compile as re_compile

# Docling joins stacked native header rows with this separator when it flattens them.
HEADER_PATH_SEPARATOR = " - "
_DIVIDER_CELL = re_compile(r":?-+:?")
_UNESCAPED_PIPE = re_compile(r"(?<!\\)\|")
# A bare reported number (45.2, <0.1, 0,08, 3.3-6.4%, 45 ± 2); labels such as "7 d" have letters.
_VALUE_CELL = re_compile(r"[<>≤≥±~+\-−–]?\s*\.?\d[\d\s.,%±+\-−–/×()*^]*")


class TableShapeError(ValueError):
    """The saved Markdown cannot be read as a header row over aligned body rows."""


@dataclass(frozen=True, slots=True)
class MarkdownGrid:
    """A pipe table's header cells and body rows, in source order."""

    header: tuple[str, ...]
    body: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class ResolvedTable:
    """A grid with one unique label per value column.

    Attributes:
        context: Full-width header text (titles, captions Docling merged into the header).
        columns: Indices of the source columns kept, so a continuation can be read the same way.
    """

    context: tuple[str, ...]
    header: tuple[str, ...]
    body: tuple[tuple[str, ...], ...]
    columns: tuple[int, ...]


def split_cells(line: str) -> tuple[str, ...] | None:
    """Split one pipe-table line into stripped cells; None if it is not a table line."""
    line = line.strip()
    if len(line) < 2 or not line.startswith("|") or not line.endswith("|") or line.endswith("\\|"):
        return None
    parts = _UNESCAPED_PIPE.split(line[1:-1])
    # Docling escapes in-cell pipes as an HTML entity; GFM escapes them with a backslash.
    return tuple(part.strip().replace("\\|", "|").replace("&#124;", "|") for part in parts)


def parse_grid(lines: list[str]) -> MarkdownGrid:
    """Read a header line, a divider line and body lines into a grid.

    Body rows keep their own cell counts; callers decide what to do with misaligned rows.

    Raises:
        TableShapeError: If there is no header-plus-divider start or any line is not a pipe row.
    """
    rows = [split_cells(line) for line in lines]
    if len(rows) < 2 or any(row is None for row in rows):
        raise TableShapeError("No Markdown header and divider")
    header, divider, *body = (row for row in rows if row is not None)
    if len(divider) != len(header) or not all(_DIVIDER_CELL.fullmatch(cell) for cell in divider):
        raise TableShapeError("No Markdown header and divider")
    return MarkdownGrid(header=header, body=tuple(body))


def is_value_cell(cell: str) -> bool:
    """Return whether a cell reads as a reported value rather than a label."""
    return bool(_VALUE_CELL.fullmatch(cell))


def resolve_header(grid: MarkdownGrid) -> ResolvedTable:
    """Give each value column one unique, non-blank label, repairing flattened structure only.

    Columns blank throughout are dropped. Full-width titles and a header path shared by every
    column become context. A blank header row takes the first body row if it holds labels, and a
    blank label continues the spanning label on its left. A label row directly under repeated
    labels becomes the second part of their paths; labels still repeated then get an ordinal.
    The first (row label) column may stay blank. Body cells are never filled in.

    Raises:
        TableShapeError: If a value column has no label to inherit.
    """
    columns = _non_empty_columns(grid)
    header = [grid.header[column] for column in columns]
    body = [project_row(row, columns, len(grid.header)) for row in grid.body]
    context: list[str] = []
    while body and (_is_title(header) or not any(header)):
        if any(header):
            context.append(next(label for label in header if label))
        elif not _is_label_row(body[0]):
            break
        header = list(body.pop(0))
    prefix, header = _split_shared_prefix(header)
    context.extend([prefix] if prefix else [])

    for column in range(1, len(header)):
        if not header[column]:
            if not header[column - 1]:
                raise TableShapeError("Blank column header")
            header[column] = header[column - 1]
    if _has_duplicates(header) and body and _is_label_row(body[0]):
        header = [
            f"{label}{HEADER_PATH_SEPARATOR}{sub}" if sub and sub != label else label
            for label, sub in zip(header, body.pop(0), strict=False)
        ]
    return ResolvedTable(
        context=tuple(context),
        header=_number_duplicates(header),
        body=tuple(body),
        columns=columns,
    )


def project_row(row: tuple[str, ...], columns: tuple[int, ...], width: int) -> tuple[str, ...]:
    """Keep the given source columns of an aligned row; leave misaligned rows untouched."""
    return tuple(row[column] for column in columns) if len(row) == width else row


def _non_empty_columns(grid: MarkdownGrid) -> tuple[int, ...]:
    width = len(grid.header)
    aligned = [row for row in grid.body if len(row) == width]
    return tuple(
        column
        for column in range(width)
        if grid.header[column] or any(row[column] for row in aligned)
    )


def _is_title(header: list[str]) -> bool:
    """Return whether one label spans every column, as a table title does."""
    return len(header) > 1 and all(header) and len(set(header)) == 1


def _is_label_row(row: tuple[str, ...]) -> bool:
    """Return whether every non-blank cell after the first is a label, not a value."""
    cells = [cell for cell in row[1:] if cell]
    return bool(cells) and not any(is_value_cell(cell) for cell in cells)


def _split_shared_prefix(header: list[str]) -> tuple[str | None, list[str]]:
    labelled = [label for label in header if label]
    if len(labelled) < 2:
        return None, header
    paths = [label.split(HEADER_PATH_SEPARATOR) for label in labelled]
    shared: list[str] = []
    for segments in zip(*paths, strict=False):
        if len(set(segments)) != 1:
            break
        shared.append(segments[0])
    # Each column must keep its own label after the shared part is removed.
    if not shared or any(len(path) <= len(shared) for path in paths):
        return None, header
    cut = len(HEADER_PATH_SEPARATOR.join(shared)) + len(HEADER_PATH_SEPARATOR)
    return HEADER_PATH_SEPARATOR.join(shared), [label[cut:] for label in header]


def _has_duplicates(header: list[str]) -> bool:
    # Exact text only: Docling repeats a spanning label verbatim, while "HGE2" and "HgE2"
    # are different mixes.
    labels = [label for label in header if label]
    return len(set(labels)) != len(labels)


def _number_duplicates(header: list[str]) -> tuple[str, ...]:
    totals = Counter(label for label in header if label)
    seen: Counter[str] = Counter()
    numbered = []
    for label in header:
        if label and totals[label] > 1:
            seen[label] += 1
            label = f"{label} [{seen[label]}]"
        numbered.append(label)
    return tuple(numbered)
