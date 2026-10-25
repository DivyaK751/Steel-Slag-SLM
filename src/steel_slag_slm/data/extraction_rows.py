"""Check the values Gemini extracted from a paper and lay them out as sheet-table rows."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from steel_slag_slm.data.extraction_tables import (
    ALLOWED_VALUES,
    KEY_HEADERS,
    LITERATURE_TABLE,
    PROVENANCE_TABLE,
    TABLES_BY_NAME,
    column_unit,
    to_column_name,
)
from steel_slag_slm.schemas import (
    ExtractedFact,
    PaperCitation,
    PaperExtraction,
    ParsedContentBlock,
)

_NUMBER = r"[-−]?\d+(?:\.\d+)?"
# A bare number, a bound (">40"), a range ("45-60", "3 to 5") or a mean with spread ("38.2 ± 1.1")
_NUMERIC_VALUE = re.compile(
    rf"^(?:[<>≤≥~]=?\s*)?{_NUMBER}(?:\s*(?:-|–|to|±)\s*{_NUMBER})?$", re.IGNORECASE
)
_NUMBER_IN_TEXT = re.compile(r"\d+(?:\.\d+)?")
_WORD = re.compile(r"[^\W_]+")
# An excerpt is placed on a page by its first words; longer quotes often cross a block break
_PAGE_MATCH_WORDS = 8
_HAS_PAGE = re.compile(r"\b(?:p\.|pp\.|page)\s*\d", re.IGNORECASE)
# A paper can study several slag types; its literature row lists them all in one cell
SLAG_TYPE_SEPARATOR = "; "


@dataclass(frozen=True, slots=True)
class ExtractionRows:
    """Rows ready to load, keyed by table name, plus the facts that failed a check and why."""

    rows_by_table: dict[str, list[dict[str, Any]]]
    rejected: list[str] = field(default_factory=list)
    confidence_lowered: int = 0  # High facts whose value isn't printed as such in the paper
    without_page: int = 0  # facts whose excerpt wasn't found on any page


def check_fact(fact: ExtractedFact, material_ids: set[str]) -> str | None:
    """Return why a fact can't be stored, or None if it passes every column rule."""
    headers = TABLES_BY_NAME[fact.sheet].headers
    if fact.field not in headers or fact.field in KEY_HEADERS:
        return "not a data column of this sheet"
    if fact.material_id not in material_ids:
        return f"unknown material {fact.material_id}"
    value = fact.value.strip()
    if not value:
        return "empty value"
    if column_unit(fact.field) and not _NUMERIC_VALUE.match(value):
        return f"not a number in {column_unit(fact.field)}"
    allowed = ALLOWED_VALUES.get(fact.field)
    if allowed and value not in allowed:
        return f"not one of {', '.join(allowed)}"
    return None


def is_derived(fact: ExtractedFact) -> bool:
    """Whether the stored value has a number not printed in the fact's quote or printed form.

    A conversion (0.20 -> 20), a calculation or a "corrected" typo all show up this way.
    """
    printed = {float(n) for n in _NUMBER_IN_TEXT.findall(f"{fact.reported_value} {fact.excerpt}")}
    return any(float(n) not in printed for n in _NUMBER_IN_TEXT.findall(fact.value))


def _normalise(text: str) -> str:
    """Lowercase words separated by single spaces, padded so matches stay whole-word."""
    return f" {' '.join(_WORD.findall(text.lower()))} "


def find_page(excerpt: str, pages: Sequence[tuple[int, str]]) -> int | None:
    """Page whose normalised text contains the excerpt's first words, or None if none does."""
    words = _WORD.findall(excerpt.lower())[:_PAGE_MATCH_WORDS]
    if not words:
        return None
    needle = f" {' '.join(words)} "
    return next((page for page, text in pages if needle in text), None)


def build_rows(
    extraction: PaperExtraction,
    source_id: str,
    citation: PaperCitation | None,
    model: str,
    extracted_at: datetime,
    content_blocks: Sequence[ParsedContentBlock] = (),
) -> ExtractionRows:
    """Turn one paper's extraction into rows for every sheet table.

    Each row's Steel_Slag_Type comes from its material, so a material has the same type in
    every sheet, and the literature row lists the paper's types in vocabulary order
    (e.g. "BOF; EAF"). Rules the model doesn't reliably follow are enforced here: each
    fact's page is looked up from its excerpt in content_blocks, and a High confidence drops to
    Medium when the value isn't printed as such.
    """
    slag_types = {m.material_id: m.steel_slag_type for m in extraction.materials}
    # A block's first placement is the page it starts on
    pages = [
        (block.placements[0].page_number, _normalise(block.text))
        for block in content_blocks
        if block.placements and block.text.strip()
    ]
    confidence_lowered = without_page = 0
    run_fields = {"extracted_at": extracted_at.isoformat(), "extraction_model": model}
    data_rows: dict[tuple[str, str, str | None, str | None], dict[str, str]] = {}
    provenance: list[dict[str, Any]] = []
    rejected: list[str] = []

    for fact in extraction.facts:
        reason = check_fact(fact, set(slag_types))
        if reason:
            rejected.append(f"{fact.sheet}.{fact.field}={fact.value!r}: {reason}")
            continue
        key = (fact.sheet, fact.material_id, fact.experiment_id, fact.mix_id)
        row = data_rows.setdefault(key, _key_fields(fact, slag_types[fact.material_id]))
        if fact.field in row and row[fact.field] != fact.value.strip():
            rejected.append(
                f"{fact.sheet}.{fact.field}={fact.value!r}: conflicts with {row[fact.field]!r}"
            )
            continue
        row[fact.field] = fact.value.strip()
        page = find_page(fact.excerpt, pages)
        lowered = fact.confidence == "High" and is_derived(fact)
        confidence_lowered += lowered
        without_page += page is None
        provenance.append(_provenance_row(fact, model, page, lowered))

    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for (sheet, *_), row in data_rows.items():
        rows_by_table.setdefault(sheet, []).append(row)
    rows_by_table[PROVENANCE_TABLE] = provenance
    used_types = {m.steel_slag_type for m in extraction.materials}
    unique_types = [
        slag_type for slag_type in ALLOWED_VALUES["Steel_Slag_Type"] if slag_type in used_types
    ]
    rows_by_table[LITERATURE_TABLE] = [_literature_row(extraction, citation, unique_types)]
    return ExtractionRows(
        {
            table: [_to_columns(row, source_id, run_fields) for row in rows]
            for table, rows in rows_by_table.items()
        },
        rejected,
        confidence_lowered,
        without_page,
    )


def _key_fields(fact: ExtractedFact, steel_slag_type: str) -> dict[str, str]:
    headers = TABLES_BY_NAME[fact.sheet].headers
    row = {"Material_ID": fact.material_id, "Steel_Slag_Type": steel_slag_type}
    if fact.experiment_id:
        row["Experiment_ID"] = fact.experiment_id
    if fact.mix_id and "Mix_ID" in headers:
        row["Mix_ID"] = fact.mix_id
    return row


def _provenance_row(
    fact: ExtractedFact, model: str, page: int | None, confidence_lowered: bool
) -> dict[str, Any]:
    location = fact.location
    if page is not None and not _HAS_PAGE.search(location):
        location = f"p. {page}, {location}"
    # The printed form shows any unit conversion the model made, for review
    notes = f"As printed: {fact.reported_value}"
    if confidence_lowered:
        notes += "; confidence lowered from High: value not printed as such in the paper"
    return {
        "Material_ID": fact.material_id,
        "Experiment_ID": fact.experiment_id,
        "Fact_or_Data_Element": fact.field,
        "Reported_Value": fact.value.strip(),
        "Unit": column_unit(fact.field),
        "Page_or_Table_or_Figure": location,
        "Extraction_Method": f"LLM ({model})",
        "Original_Source_Text_or_Excerpt": fact.excerpt,
        "Confidence": "Medium" if confidence_lowered else fact.confidence,
        "Provenance_Notes": notes,
    }


def _literature_row(
    extraction: PaperExtraction, citation: PaperCitation | None, slag_types: Sequence[str]
) -> dict[str, Any]:
    literature = extraction.literature
    return {
        "Title": citation.title if citation else None,
        "Authors": ", ".join(citation.authors) if citation else None,
        "Publication_Year": str(citation.year) if citation and citation.year else None,
        "Publication_Type": literature.publication_type,
        "Journal_or_Source": literature.journal_or_source,
        "DOI_or_URL": citation.doi if citation else None,
        "Country_or_Region": literature.country_or_region,
        "Steel_Slag_Type": SLAG_TYPE_SEPARATOR.join(slag_types) or None,
        "Study_Objective": literature.study_objective,
        "Abstract_or_Key_Finding": literature.abstract_or_key_finding,
        "Full_Text_Access": literature.full_text_access,
        "Data_Extraction_Status": "Extracted",
        "Relevant_To_SCM": literature.relevant_to_scm,
        "Notes": literature.notes,
    }


def _to_columns(row: dict[str, Any], source_id: str, run_fields: dict[str, str]) -> dict[str, Any]:
    """Rename workbook headers to column names and add the paper and run fields."""
    columns = {to_column_name(header): value for header, value in row.items() if value is not None}
    return {"source_id": source_id, **columns, **run_fields}
