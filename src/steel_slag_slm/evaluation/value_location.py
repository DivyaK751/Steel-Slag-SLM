"""Find where a reference value sits in a paper's parsed output, or its raw PDF text.

Used by the parsing-ceiling diagnostic (docs/experiments/parsing_ceiling.md) to tell "the parser
dropped this value" apart from "this was never printed" or "the miss is downstream": a value
present and intact in parsed.json but still missed by extraction or retrieval is a different
failure than one the parser never captured at all.
"""

import re
from typing import Literal

from steel_slag_slm.data.pdf_pages import page_texts
from steel_slag_slm.evaluation.value_match import (
    MatchType,
    match_type,
    normalize_formula,
    normalize_value,
)
from steel_slag_slm.schemas import FigureReading, ParsedPaper

ParsedSource = Literal["text", "table", "figure_reading", "not_found"]

_NUMBER_TOKEN = re.compile(r"[-+]?\d[\d.]*")
_RANGE_TOKEN = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:-|to)\s*[-+]?\d+(?:\.\d+)?")


class ValueLocation:
    """Where a reference value was found, and which rule matched it."""

    __slots__ = ("source", "match", "page")

    def __init__(self, source: ParsedSource, match: MatchType | None, page: int | None = None):
        self.source = source
        self.match = match
        self.page = page  # known only for content-block text, tables, and figures

    def __repr__(self) -> str:
        return f"ValueLocation(source={self.source!r}, match={self.match!r}, page={self.page!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ValueLocation):
            return NotImplemented
        return (self.source, self.match, self.page) == (other.source, other.match, other.page)


def find_in_text(value: str, text: str) -> MatchType | None:
    """Search free text for value: a number as a whole token, or a formula as a substring.

    Numbers are matched as whole tokens, so "10.6" never matches inside "110.6" or "10.65".
    Checks ranges before single numbers, since "42-48" would otherwise also read as token "42".
    Returns the most exact match type found, or None.
    """
    normalized_text = normalize_value(text)
    if _as_number_or_none(value) is None:
        formula = normalize_formula(value)
        return "exact" if formula and formula in normalize_formula(text) else None

    for range_text in _RANGE_TOKEN.findall(normalized_text):
        if match_type(value, range_text) == "range":
            return "range"

    best: MatchType | None = None
    for token in _NUMBER_TOKEN.findall(normalized_text):
        result = match_type(value, token)
        if result == "exact":
            return "exact"
        if result == "rounded":
            best = "rounded"
    return best


def _as_number_or_none(value: str) -> float | None:
    from steel_slag_slm.evaluation.value_metrics import leading_number

    return leading_number(normalize_value(value))


def _best_figure_match(value: str, reading: FigureReading) -> MatchType | None:
    """The most exact match type value gets against the figure's summary or any reading cell."""
    cells = [reading.summary, *(cell for row in reading.rows for cell in row)]
    found = {find_in_text(value, cell) for cell in cells} - {None}
    for best in ("exact", "rounded", "range"):
        if best in found:
            return best
    return None


def locate_in_parsed(paper: ParsedPaper, value: str) -> ValueLocation:
    """Search a paper's text, tables and figure readings for a reference value.

    Checks page-numbered content blocks first, then legacy tables and Markdown. The latter
    fallback matters because older or partial parses may contain text absent from content blocks.
    """
    for block in paper.content_blocks:
        match = find_in_text(value, block.text)
        if match:
            page = block.placements[0].page_number if block.placements else None
            return ValueLocation(source=block.kind, match=match, page=page)

    for table in paper.tables:
        match = find_in_text(value, table)
        if match:
            return ValueLocation(source="table", match=match)

    match = find_in_text(value, paper.markdown)
    if match:
        return ValueLocation(source="text", match=match)

    for figure in paper.figures:
        if figure.reading is None:
            continue
        match = _best_figure_match(value, figure.reading)
        if match:
            page = figure.pages[0] if figure.pages else None
            return ValueLocation(source="figure_reading", match=match, page=page)

    return ValueLocation(source="not_found", match=None)


def find_in_raw_pdf(value: str, pdf_bytes: bytes) -> int | None:
    """The 1-based page where the raw PDF's text layer contains value, or None if none does."""
    for page_number, text in enumerate(page_texts(pdf_bytes), start=1):
        if find_in_text(value, text):
            return page_number
    return None
