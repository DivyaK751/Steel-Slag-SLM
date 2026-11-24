from typing import Any

import pytest

from steel_slag_slm.data.parsed_manifest import ParsedManifest, table_text_sha256
from steel_slag_slm.data.table_context import table_context, tables_to_describe

TABLE = (
    "Table 2. Carbonation of the binders.\n\n"
    "| Binder   |   ψ (%) |\n|----------|--------:|\n| SSD      |    12.1 |\n| SSW      |     9.8 |"
)
CONTENTS = "| 1 Introduction ........ 3 |\n|---|\n| 2 Methods ........ 5 |"


def _block(kind: str, label: str, text: str, page: int = 1) -> dict[str, Any]:
    placement = {"page_number": page, "bbox": [10, 10, 100, 100], "coordinate_origin": "TOPLEFT"}
    return {"kind": kind, "label": label, "text": text, "placements": [placement]}


@pytest.fixture
def manifest(manifest_record: dict[str, Any]) -> ParsedManifest:
    blocks = [
        _block("text", "text", "The steel slag powder (SSD) was ground for 2 h."),
        _block("text", "text", "Results are unrelated to the table."),
        _block("text", "text", "ψ is the CO2 mineralization potential of each binder."),
        _block("text", "page_header", "Journal of Slag 12 (2021)"),
        _block("text", "text", "Carbonation results are listed below."),
        _block("table", "table", TABLE, page=2),
        _block("text", "caption", "Table 2. Carbonation of the binders.", page=2),
        _block("text", "footnote", "Values after 28 d of CO2 curing.", page=2),
        _block("text", "text", "As Table 2 shows, SSD binds more CO2 than SSW.", page=2),
        _block("text", "text", "Table 21 is a different table.", page=2),
        _block("table", "table", CONTENTS, page=2),
    ]
    return ParsedManifest.model_validate(
        {**manifest_record, "content_blocks": blocks, "tables": [TABLE, CONTENTS]}
    )


def test_context_ranks_notes_lead_in_citations_then_definitions(manifest: ParsedManifest) -> None:
    context = table_context(manifest.content_blocks, 5, max_chars=2000)

    assert context.split("\n\n") == [
        "Values after 28 d of CO2 curing.",
        "Carbonation results are listed below.",
        "As Table 2 shows, SSD binds more CO2 than SSW.",
        "The steel slag powder (SSD) was ground for 2 h.",
        "ψ is the CO2 mineralization potential of each binder.",
    ]


def test_context_keeps_whole_paragraphs_within_the_limit(manifest: ParsedManifest) -> None:
    context = table_context(manifest.content_blocks, 5, max_chars=80)

    assert context.split("\n\n") == [
        "Values after 28 d of CO2 curing.",
        "Carbonation results are listed below.",
    ]


def test_tables_to_describe_skips_contents_lists_and_compacts_the_table(
    manifest: ParsedManifest,
) -> None:
    [table] = tables_to_describe(manifest, prompt_version=1, context_max_chars=2000)

    assert (table.paper_id, table.block_index, table.label) == ("W1", 5, "Table 2")
    assert table.table.endswith("| Binder | ψ (%) |\n| SSD | 12.1 |\n| SSW | 9.8 |")
    assert table.table_sha256 == table_text_sha256(TABLE)
    assert "mineralization potential" in table.context


def test_tables_to_describe_skips_tables_described_by_the_same_prompt_version(
    manifest: ParsedManifest,
) -> None:
    stored = {
        "block_index": 5,
        "table_sha256": table_text_sha256(TABLE),
        "prompt_version": 1,
        "description": {"summary": "CO2 uptake of two slag binders."},
    }
    described = ParsedManifest.model_validate(
        {**manifest.model_dump(), "table_descriptions": [stored]}
    )

    assert tables_to_describe(described, prompt_version=1, context_max_chars=2000) == []
    assert len(tables_to_describe(described, prompt_version=2, context_max_chars=2000)) == 1
