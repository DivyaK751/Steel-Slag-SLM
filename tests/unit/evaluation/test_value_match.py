import pymupdf

from steel_slag_slm.evaluation.value_location import (
    find_in_raw_pdf,
    find_in_text,
    locate_in_parsed,
)
from steel_slag_slm.evaluation.value_match import (
    match_type,
    normalize_formula,
    normalize_value,
)
from steel_slag_slm.schemas import (
    ContentPlacement,
    ParsedContentBlock,
    ParsedPaper,
)


def test_normalize_value_handles_dashes_subscripts_and_separators() -> None:
    assert normalize_value("−3.1") == "-3.1"
    assert normalize_value("42–48") == "42-48"
    assert normalize_value("1,234.5 wt%") == "1234.5 wt%"
    assert normalize_value("27,71") == "27.71"


def test_normalize_formula_handles_subscripts_and_extraction_spacing() -> None:
    assert normalize_formula("Fe₂O₃") == "fe2o3"
    assert normalize_formula("Fe 2 O 3") == "fe2o3"


def test_match_type_reports_exact_rounded_and_range_matches() -> None:
    assert match_type("45.2 wt%", "45.2") == "exact"
    assert match_type("45.2", "45.24") == "rounded"
    assert match_type("45", "42–48") == "range"
    assert match_type("49", "42-48") is None


def test_find_in_text_does_not_match_a_number_inside_a_larger_number() -> None:
    assert find_in_text("10.6", "Measured 10.6 wt%") == "exact"
    assert find_in_text("10.6", "Measured 110.6 wt%") is None
    assert find_in_text("10.6", "Measured 10.65 wt%") is None


def test_find_in_text_matches_a_value_inside_a_printed_range() -> None:
    assert find_in_text("45", "Reported range: 42–48 wt%") == "range"


def test_locate_in_parsed_reports_source_and_page() -> None:
    paper = ParsedPaper(
        openalex_id="W1",
        markdown="",
        tables=(),
        content_blocks=(
            ParsedContentBlock(
                kind="table",
                label="Table 2",
                text="CaO | 45.2 wt%",
                placements=(
                    ContentPlacement(
                        page_number=4,
                        bbox=(0.0, 0.0, 100.0, 100.0),
                        coordinate_origin="top-left",
                    ),
                ),
            ),
        ),
    )

    location = locate_in_parsed(paper, "45.2")

    assert (location.source, location.match, location.page) == ("table", "exact", 4)


def test_locate_in_parsed_falls_back_to_legacy_tables() -> None:
    paper = ParsedPaper(
        openalex_id="W1",
        markdown="",
        tables=("CaO | 45.2 wt%",),
    )

    location = locate_in_parsed(paper, "45.2")

    assert location.source == "table"
    assert location.match == "exact"


def test_find_in_raw_pdf_returns_the_matching_page() -> None:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "First page")
        document.new_page().insert_text((72, 72), "CaO was 45.2 wt%")
        pdf_bytes = document.tobytes()

    assert find_in_raw_pdf("45.2", pdf_bytes) == 2
    assert find_in_raw_pdf("99.9", pdf_bytes) is None