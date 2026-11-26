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


def test_tables_to_describe_lists_every_table_and_compacts_it(manifest: ParsedManifest) -> None:
    table, contents = tables_to_describe(manifest, prompt_version=1, context_max_chars=2000)

    assert contents.block_index == 10  # a contents list still gets a description

    assert (table.paper_id, table.block_index, table.label) == ("W1", 5, "Table 2")
    assert table.table.endswith("| Binder | ψ (%) |\n| SSD | 12.1 |\n| SSW | 9.8 |")
    assert table.table_sha256 == table_text_sha256(TABLE)
    assert "mineralization potential" in table.context


def test_tables_to_describe_gives_the_title_and_the_section_the_table_sits_in(
    manifest_record: dict[str, Any],
) -> None:
    blocks = [
        _block("text", "section_header", "3. Results and discussion"),
        _block("text", "section_header", "ACCEPTED MANUSCRIPT"),  # a running header, not a section
        _block("text", "text", "Strength is listed below."),
        _block("table", "table", TABLE),
    ]
    manifest = ParsedManifest.model_validate(
        {**manifest_record, "content_blocks": blocks, "tables": [TABLE]}
    )

    [table] = tables_to_describe(manifest, 1, 2000, paper_title="Slag binders")

    assert (table.paper_title, table.section) == ("Slag binders", "3. Results and discussion")
    assert table.previous_table == ""


def _continuation_manifest(record: dict[str, Any], second_width: int) -> ParsedManifest:
    later = "| SSE | 7.0 |" if second_width == 2 else "| SSE | 7.0 | 1 |"
    blocks = [
        _block("table", "table", TABLE, page=2),
        _block("text", "page_footer", "Page 2", page=2),
        _block("table", "table", later, page=3),
        _block("text", "page_header", "Journal of Slag", page=4),
        _block("table", "table", "| SSF | 6.1 |", page=4),
    ]
    return ParsedManifest.model_validate(
        {
            **record,
            "page_count": 4,
            "content_blocks": blocks,
            "tables": [TABLE, later, "| SSF | 6.1 |"],
        }
    )


def test_an_uncaptioned_table_of_the_same_width_gets_the_caption_it_continues(
    manifest_record: dict[str, Any],
) -> None:
    first, second, third = tables_to_describe(_continuation_manifest(manifest_record, 2), 1, 2000)

    expected = "Table 2. Carbonation of the binders.\n| Binder | ψ (%) |"
    assert (first.previous_table, second.previous_table) == ("", expected)
    assert third.previous_table == expected  # followed back over two page breaks
    assert [table.label for table in (first, second, third)] == ["Table 2"] * 3


def test_an_uncaptioned_table_of_another_width_continues_nothing(
    manifest_record: dict[str, Any],
) -> None:
    tables = tables_to_describe(_continuation_manifest(manifest_record, 3), 1, 2000)

    assert [table.previous_table for table in tables] == ["", "", ""]


def test_a_caption_left_above_the_table_names_it_and_explains_it(
    manifest_record: dict[str, Any],
) -> None:
    bare = "| Fly ash | Zn (mg/kg) |\n|---|---|\n| FA2 | 8160 |"
    blocks = [
        _block("text", "text", "Heavy metals are listed in Table 3 for each fly ash."),
        _block("text", "section_header", "Table 3. Heavy metal contents of the fly ashes."),
        _block("table", "table", bare),
    ]
    manifest = ParsedManifest.model_validate(
        {**manifest_record, "content_blocks": blocks, "tables": [bare]}
    )

    [table] = tables_to_describe(manifest, 1, 2000)

    assert table.label == "Table 3"
    assert table.context.split("\n\n") == [
        "Table 3. Heavy metal contents of the fly ashes.",
        "Heavy metals are listed in Table 3 for each fly ash.",
    ]


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

    [contents] = tables_to_describe(described, prompt_version=1, context_max_chars=2000)
    assert contents.block_index == 10
    assert len(tables_to_describe(described, prompt_version=2, context_max_chars=2000)) == 2
