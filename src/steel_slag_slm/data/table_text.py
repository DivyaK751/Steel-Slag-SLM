"""Read parsed tables as text: their "Table N" label, a compact form, and contents lists."""

import re

# Docling pads every cell to its column's width and adds a |---| line; neither carries content
_SEPARATOR_ROW = re.compile(r"^\s*\|?[\s|:-]+\|?\s*$")
_CELL_BOUNDARY = re.compile(r"(?<!\\)\|")  # "\|" is a pipe inside a cell, not a boundary
_TABLE_LABEL = re.compile(
    r"^\s*(?:table|tabla|tab\.)\s*[-.]?\s*(?P<number>\d+|[ivxl]+)\b", re.IGNORECASE
)
_CONTENTS_LIST = re.compile(r"\.{8,}|\bkazalo vsebine\b|\btable of contents\b", re.IGNORECASE)


def is_table_row(line: str) -> bool:
    """Return True for a line of a Markdown table."""
    return line.lstrip().startswith("|")


def compact_markdown_table(text: str) -> str:
    """Drop cell padding and separator rows from a Markdown table, keeping its caption lines.

    Cell text is kept exactly; only the spaces around it go, which cuts a table's tokens by
    about a fifth, so more of its rows fit in what the reranker reads.
    """
    lines: list[str] = []
    for line in text.splitlines():
        if not is_table_row(line):
            lines.append(line.rstrip())
        elif not _SEPARATOR_ROW.match(line):
            lines.append(f"| {' | '.join(_cells(line))} |")
    return "\n".join(lines).strip()


def _cells(row: str) -> list[str]:
    """Split a row into its cells; the outer pipes are optional in Markdown."""
    parts = _CELL_BOUNDARY.split(row.strip())
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [part.strip() for part in parts]


def table_label(text: str) -> str | None:
    """Return "Table N" from the caption a table block starts with, or None without one."""
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    if is_table_row(first_line):
        return None
    match = _TABLE_LABEL.match(first_line)
    return f"Table {match.group('number').upper()}" if match else None


def is_contents_list(text: str) -> bool:
    """Return True for a table of contents: dotted leader lines or a contents heading."""
    return _CONTENTS_LIST.search(text) is not None
