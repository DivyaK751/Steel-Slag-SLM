"""Read a paper's text, tables, content blocks and figures from its parsed.json."""

import json
import logging
from typing import Any

from pydantic import ValidationError

from steel_slag_slm.schemas import (
    ContentPlacement,
    FigureReading,
    PaperFigure,
    ParsedContentBlock,
    ParsedPaper,
)

logger = logging.getLogger(__name__)


def parse_parsed_json(content: bytes) -> ParsedPaper:
    """Validate parsed.json bytes and return the paper's text, tables, blocks and figures.

    Papers parsed before content blocks or figures existed have none; their Markdown is still
    returned. Table rows from contextual_tables, when present, are kept by table block.

    Raises:
        ValueError: If the content is not JSON or lacks the fields the indexer needs.
    """
    try:
        record = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("parsed.json is not valid JSON") from error

    if not isinstance(record, dict):
        raise ValueError("parsed.json must hold a JSON object")
    paper_id = record.get("openalex_id")
    markdown = record.get("markdown")
    tables = record.get("tables")
    if not isinstance(paper_id, str) or not isinstance(markdown, str):
        raise ValueError("parsed.json needs string fields openalex_id and markdown")
    if not isinstance(tables, list) or not all(isinstance(table, str) for table in tables):
        raise ValueError("parsed.json needs tables to be a list of strings")

    blocks = _parse_content_blocks(record.get("content_blocks", []))
    return ParsedPaper(
        openalex_id=paper_id,
        markdown=markdown,
        tables=tuple(tables),
        content_blocks=blocks,
        figures=_parse_figures(paper_id, record.get("images", [])),
        table_rows=_parse_table_rows(paper_id, blocks, record.get("contextual_tables", [])),
    )


def _parse_table_rows(
    paper_id: str, blocks: tuple[ParsedContentBlock, ...], raw_tables: Any
) -> dict[int, tuple[str, ...]]:
    """Return the rows contextual_tables renders for each table block, by block index.

    Each entry names its table block as "<source>:<block index>". Rows are extra search
    passages, so an entry that cannot be placed is logged and left out instead of failing
    the paper.
    """
    if not isinstance(raw_tables, list):
        logger.warning("%s: contextual_tables is not a list; rows left out", paper_id)
        return {}
    rows_by_block: dict[int, tuple[str, ...]] = {}
    for raw in raw_tables:
        reference = raw.get("reference") if isinstance(raw, dict) else None
        index_text = reference.rpartition(":")[2] if isinstance(reference, str) else ""
        index = int(index_text) if index_text.isdigit() else -1
        if not 0 <= index < len(blocks) or blocks[index].kind != "table":
            logger.warning("%s: table rows point to no table block (%r)", paper_id, reference)
            continue
        raw_rows = raw.get("rows", [])
        texts = [row.get("text") for row in raw_rows if isinstance(row, dict)]
        rows_by_block[index] = tuple(t.strip() for t in texts if isinstance(t, str) and t.strip())
    return rows_by_block


def _parse_figures(paper_id: str, raw_figures: Any) -> tuple[PaperFigure, ...]:
    if not isinstance(raw_figures, list):
        raise ValueError("parsed.json needs images to be a list")
    figures = (_parse_figure(paper_id, raw) for raw in raw_figures)
    # Older parses list embedded image objects with no caption or reading; nothing to search
    return tuple(f for f in figures if f.caption.strip() or f.figure_number or f.reading)


def _parse_figure(paper_id: str, raw: Any) -> PaperFigure:
    if not isinstance(raw, dict) or not isinstance(raw.get("placements"), list):
        raise ValueError("parsed.json has a figure without placements")
    number = raw.get("figure_number")
    pages = {p.get("page_number") for p in raw["placements"] if isinstance(p, dict)}
    reading = None
    if raw.get("reading") is not None:
        try:
            reading = FigureReading.model_validate(raw["reading"])
        except ValidationError:
            # A bad reading should not cost the paper its text; the caption is still indexed
            logger.warning("%s: ignoring an unreadable figure reading", paper_id)
    return PaperFigure(
        figure_number=number if isinstance(number, str) else None,
        caption=str(raw.get("caption") or ""),
        pages=tuple(sorted(page for page in pages if isinstance(page, int))),
        reading=reading,
    )


def _parse_content_blocks(raw_blocks: Any) -> tuple[ParsedContentBlock, ...]:
    if not isinstance(raw_blocks, list):
        raise ValueError("parsed.json needs content_blocks to be a list")
    return tuple(_parse_content_block(raw_block) for raw_block in raw_blocks)


def _parse_content_block(raw_block: Any) -> ParsedContentBlock:
    if not isinstance(raw_block, dict):
        raise ValueError("parsed.json has a content block that is not an object")
    kind = raw_block.get("kind")
    label = raw_block.get("label")
    text = raw_block.get("text")
    raw_placements = raw_block.get("placements")
    if kind not in ("text", "table") or not isinstance(label, str) or not isinstance(text, str):
        raise ValueError("parsed.json has a content block without kind, label or text")
    if not isinstance(raw_placements, list):
        raise ValueError("parsed.json has a content block without placements")
    placements = tuple(_parse_placement(raw) for raw in raw_placements)
    return ParsedContentBlock(kind=kind, label=label, text=text, placements=placements)


def _parse_placement(raw: Any) -> ContentPlacement:
    if not isinstance(raw, dict):
        raise ValueError("parsed.json has a placement that is not an object")
    page_number = raw.get("page_number")
    bbox = raw.get("bbox")
    if not isinstance(page_number, int) or not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("parsed.json has a placement without a page number and a 4-value bbox")
    coordinate_origin = raw.get("coordinate_origin", "")
    return ContentPlacement(
        page_number=page_number,
        bbox=tuple(float(value) for value in bbox),
        coordinate_origin=str(coordinate_origin),
    )
