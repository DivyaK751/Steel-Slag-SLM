"""Split a parsed paper into section-aware chunks of text, tables and figure captions."""

import logging
import re
import textwrap
from collections import Counter
from collections.abc import Iterable

from steel_slag_slm.schemas import Chunk, ChunkKind, ParsedPaper

logger = logging.getLogger(__name__)

IMAGE_PLACEHOLDER = "<!-- image -->"
# A line this many times or more is a running header or footer, so every copy is dropped
RUNNING_LINE_MIN_REPEATS = 3
RUNNING_LINE_MAX_CHARS = 120

_HEADING = re.compile(r"^#{1,6}\s+(?P<title>\S.*?)\s*$")
_CAPTION = re.compile(r"^(Fig\.?|Figure)\s*\d+", re.IGNORECASE)
_TABLE_SEPARATOR = re.compile(r"^\|?[\s|:-]+\|?$")

# A block is ("text", paragraph) or ("table", index into ParsedPaper.tables)
_Block = tuple[str, str | int]


def is_table_line(line: str) -> bool:
    """Return True for a line of a Markdown table."""
    return line.lstrip().startswith("|")


def _is_repeatable(line: str) -> bool:
    """Return True for a plain short line; headings and table rows are never running lines."""
    stripped = line.strip()
    return (
        bool(stripped)
        and not is_table_line(line)
        and _HEADING.match(stripped) is None
        and len(stripped) <= RUNNING_LINE_MAX_CHARS
    )


def clean_lines(markdown: str) -> list[str]:
    """Drop image placeholders and running headers or footers from the Markdown lines."""
    lines = [line.rstrip() for line in markdown.splitlines()]
    lines = [line for line in lines if line.strip() != IMAGE_PLACEHOLDER]
    counts = Counter(line.strip() for line in lines if _is_repeatable(line))
    repeated = {text for text, count in counts.items() if count >= RUNNING_LINE_MIN_REPEATS}
    return [line for line in lines if not (_is_repeatable(line) and line.strip() in repeated)]


def scan_sections(lines: Iterable[str]) -> list[tuple[str, list[_Block]]]:
    """Group the lines into (heading, blocks) sections in reading order.

    Table rows are not text: each table becomes a block that holds its position in
    ParsedPaper.tables, so the tables can be matched to their section.
    """
    sections: list[tuple[str, list[_Block]]] = [("", [])]
    paragraph: list[str] = []
    table_index = 0
    in_table = False

    def flush_paragraph() -> None:
        if paragraph:
            sections[-1][1].append(("text", " ".join(paragraph)))
            paragraph.clear()

    for line in lines:
        stripped = line.strip()
        if is_table_line(line):
            if not in_table:
                flush_paragraph()
                sections[-1][1].append(("table", table_index))
                table_index += 1
                in_table = True
            continue
        in_table = False
        heading = _HEADING.match(stripped)
        if heading:
            flush_paragraph()
            sections.append((heading.group("title"), []))
        elif stripped:
            paragraph.append(stripped)
        else:
            flush_paragraph()
    flush_paragraph()
    return sections


def split_long_text(text: str, max_chars: int) -> list[str]:
    """Break text into pieces of at most max_chars, at word boundaries where possible."""
    return textwrap.wrap(text, width=max_chars, break_long_words=True, break_on_hyphens=False)


def pack_paragraphs(paragraphs: list[str], max_chars: int) -> list[str]:
    """Join paragraphs into passages of at most max_chars without splitting a paragraph."""
    passages: list[str] = []
    current = ""
    for paragraph in paragraphs:
        for piece in split_long_text(paragraph, max_chars):
            if current and len(current) + 1 + len(piece) > max_chars:
                passages.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        passages.append(current)
    return passages


def split_table(table: str, max_chars: int) -> list[str]:
    """Return the table whole, or in row groups that each repeat the caption and header."""
    if len(table) <= max_chars:
        return [table]

    lines = table.splitlines()
    first_row = next((i for i, line in enumerate(lines) if is_table_line(line)), len(lines))
    caption, rows = lines[:first_row], lines[first_row:]
    # The header is the first row, plus its separator line when Markdown has one
    header_size = 2 if len(rows) > 1 and _TABLE_SEPARATOR.match(rows[1]) else 1
    prefix = caption + rows[:header_size]
    pieces: list[str] = []
    group: list[str] = []
    for row in rows[header_size:]:
        candidate = "\n".join([*prefix, *group, row])
        if group and len(candidate) > max_chars:
            pieces.append("\n".join([*prefix, *group]))
            group = []
        group.append(row)
    if group:
        pieces.append("\n".join([*prefix, *group]))
    return pieces or [table]


def _is_caption(paragraph: str) -> bool:
    return _CAPTION.match(paragraph) is not None


def _text_passages(paragraphs: list[str], max_chars: int) -> list[tuple[ChunkKind, str]]:
    """Pack body paragraphs into passages; each figure caption is kept as its own passage."""
    passages: list[tuple[ChunkKind, str]] = []
    body: list[str] = []
    for paragraph in paragraphs:
        if _is_caption(paragraph):
            passages.extend(("text", text) for text in pack_paragraphs(body, max_chars))
            body = []
            passages.extend(
                ("figure_caption", text) for text in pack_paragraphs([paragraph], max_chars)
            )
        else:
            body.append(paragraph)
    passages.extend(("text", text) for text in pack_paragraphs(body, max_chars))
    return passages


def build_chunks(paper: ParsedPaper, max_chars: int, table_max_chars: int) -> list[Chunk]:
    """Turn one paper into chunks in reading order, each tagged with its section.

    Raises:
        ValueError: If max_chars or table_max_chars is not positive.
    """
    if max_chars <= 0 or table_max_chars <= 0:
        raise ValueError("chunk sizes must be greater than zero")

    pieces: list[tuple[ChunkKind, str, str]] = []
    table_count = 0
    for section_title, blocks in scan_sections(clean_lines(paper.markdown)):
        paragraphs: list[str] = []
        for kind, value in blocks:
            if kind == "text":
                paragraphs.append(str(value))
                continue
            pieces.extend((k, section_title, t) for k, t in _text_passages(paragraphs, max_chars))
            paragraphs = []
            table_count += 1
            if int(value) < len(paper.tables):
                tables = split_table(paper.tables[int(value)], table_max_chars)
                pieces.extend(("table", section_title, table) for table in tables)
        pieces.extend((k, section_title, t) for k, t in _text_passages(paragraphs, max_chars))

    if table_count != len(paper.tables):
        logger.warning(
            "%s: %d table blocks in the Markdown but %d tables in parsed.json",
            paper.openalex_id,
            table_count,
            len(paper.tables),
        )
    return [
        Chunk(
            paper_id=paper.openalex_id,
            kind=kind,
            section=section,
            text=text,
            position=position,
        )
        for position, (kind, section, text) in enumerate(pieces)
    ]


def passage_for_embedding(chunk: Chunk) -> str:
    """Return the text to embed: the section heading followed by the chunk text."""
    return f"{chunk.section}\n{chunk.text}" if chunk.section else chunk.text
