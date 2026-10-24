"""Read a paper's text, tables and content blocks from its parsed.json."""

import json
from typing import Any

from steel_slag_slm.schemas import ContentPlacement, ParsedContentBlock, ParsedPaper


def parse_parsed_json(content: bytes) -> ParsedPaper:
    """Validate parsed.json bytes and return the paper's text, tables and content blocks.

    Papers parsed before content blocks existed have none; their Markdown is still returned.

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

    return ParsedPaper(
        openalex_id=paper_id,
        markdown=markdown,
        tables=tuple(tables),
        content_blocks=_parse_content_blocks(record.get("content_blocks", [])),
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
