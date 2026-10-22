"""Read the text and tables of a paper from the parsed.json written by parse-pdfs."""

import json

from steel_slag_slm.schemas import ParsedPaper


def parse_parsed_json(content: bytes) -> ParsedPaper:
    """Validate parsed.json bytes and return the paper's Markdown and tables.

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

    return ParsedPaper(openalex_id=paper_id, markdown=markdown, tables=tuple(tables))
