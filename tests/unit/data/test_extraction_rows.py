from datetime import UTC, datetime
from typing import Any

from steel_slag_slm.data.extraction_rows import build_rows, check_fact, find_page, is_derived
from steel_slag_slm.schemas import (
    ContentPlacement,
    ExtractedFact,
    ExtractedLiterature,
    ExtractedMaterial,
    PaperCitation,
    PaperExtraction,
    ParsedContentBlock,
)

LITERATURE = ExtractedLiterature(
    publication_type="Journal Article", journal_or_source="Cem Concr Res",
    country_or_region="China", study_objective="Test BOF slag as SCM.",
    abstract_or_key_finding="It works.", full_text_access="Open", relevant_to_scm="Yes",
    notes=None,
)  # fmt: skip
MATERIALS = [
    ExtractedMaterial(material_id="MAT-0001", steel_slag_type="BOF", description="Plant A"),
    ExtractedMaterial(material_id="MAT-0002", steel_slag_type="EAF", description="Plant B"),
]
CITATION = PaperCitation(title="Slag paper", authors=("A. One", "B. Two"), year=2020, doi="10.1/x")


def _fact(**overrides: Any) -> ExtractedFact:
    values: dict[str, Any] = {
        "sheet": "sheet_02_feedstock_chemistry", "field": "CaO_pct", "value": "45.2",
        "reported_value": "45.2 wt%", "material_id": "MAT-0001", "experiment_id": "EXP-0001",
        "mix_id": None, "location": "Table 1", "excerpt": "CaO 45.2", "confidence": "High",
    }  # fmt: skip
    return ExtractedFact(**{**values, **overrides})


def _block(page: int, text: str) -> ParsedContentBlock:
    placement = ContentPlacement(page_number=page, bbox=(0, 0, 1, 1), coordinate_origin="")
    return ParsedContentBlock(kind="text", label="text", text=text, placements=(placement,))


PAGES = (_block(1, "Introduction to slag."), _block(3, "Table 1 | CaO | 45.2 |"))


def _rows(*facts: ExtractedFact, materials: Any = None) -> Any:
    extraction = PaperExtraction(
        literature=LITERATURE, materials=materials or MATERIALS, facts=list(facts)
    )
    return build_rows(
        extraction, "W1", CITATION, "gemini-x", datetime(2026, 10, 23, tzinfo=UTC), PAGES
    )


def test_check_fact_accepts_numbers_ranges_and_bounds_in_unit_columns() -> None:
    for value in ("45.2", "45-60", ">40", "38.2 ± 1.1", "3 to 5"):
        assert check_fact(_fact(value=value), {"MAT-0001"}) is None, value


def test_check_fact_rejects_units_and_words_in_numeric_columns() -> None:
    assert check_fact(_fact(value="45 %"), {"MAT-0001"}) == "not a number in %"
    assert check_fact(_fact(value="about half"), {"MAT-0001"}) == "not a number in %"


def test_check_fact_enforces_controlled_vocabulary() -> None:
    fact = _fact(sheet="sheet_03_processing", field="Dry_or_Wet", value="Moist")
    assert check_fact(fact, {"MAT-0001"}) == "not one of Dry, Wet"
    assert check_fact(fact.model_copy(update={"value": "Wet"}), {"MAT-0001"}) is None


def test_check_fact_rejects_wrong_sheet_key_columns_and_unknown_materials() -> None:
    assert check_fact(_fact(field="D50_um"), {"MAT-0001"}) == "not a data column of this sheet"
    assert check_fact(_fact(field="Steel_Slag_Type", value="BOF"), {"MAT-0001"}) is not None
    assert check_fact(_fact(material_id="MAT-0009"), {"MAT-0001"}) == "unknown material MAT-0009"


def test_build_rows_gives_each_material_the_same_slag_type_in_every_sheet() -> None:
    rows = _rows(
        _fact(),
        _fact(sheet="sheet_04_particle_size", field="D50_um", value="12", experiment_id=None),
        _fact(material_id="MAT-0002", field="SiO2_pct", value="15"),
    ).rows_by_table
    chemistry = rows["sheet_02_feedstock_chemistry"]
    assert {(r["material_id"], r["steel_slag_type"]) for r in chemistry} == {
        ("MAT-0001", "BOF"),
        ("MAT-0002", "EAF"),
    }
    assert rows["sheet_04_particle_size"][0]["steel_slag_type"] == "BOF"
    # The paper's literature row lists every type, in vocabulary order
    [literature] = rows["sheet_01_scientific_literature"]
    assert literature["steel_slag_type"] == "EAF; BOF"


def test_build_rows_keeps_a_single_slag_type_on_the_literature_row() -> None:
    rows = _rows(_fact(), materials=MATERIALS[:1]).rows_by_table
    [literature] = rows["sheet_01_scientific_literature"]
    assert literature["steel_slag_type"] == "BOF" and "notes" not in literature


def test_build_rows_groups_one_experiment_into_one_row() -> None:
    rows = _rows(_fact(), _fact(field="SiO2_pct", value="12")).rows_by_table
    [row] = rows["sheet_02_feedstock_chemistry"]
    assert (row["cao_pct"], row["sio2_pct"], row["source_id"]) == ("45.2", "12", "W1")


def test_build_rows_keeps_the_first_of_two_conflicting_values() -> None:
    result = _rows(_fact(), _fact(value="50"))
    assert result.rows_by_table["sheet_02_feedstock_chemistry"][0]["cao_pct"] == "45.2"
    assert len(result.rejected) == 1 and "conflicts" in result.rejected[0]
    assert len(result.rows_by_table["sheet_09_citations_provenance"]) == 1


def test_build_rows_records_provenance_and_registry_citation() -> None:
    rows = _rows(_fact(value="20", reported_value="0.20 mass fraction")).rows_by_table
    [provenance] = rows["sheet_09_citations_provenance"]
    assert provenance["fact_or_data_element"] == "CaO_pct"
    assert (provenance["reported_value"], provenance["unit"]) == ("20", "%")
    assert provenance["provenance_notes"].startswith("As printed: 0.20 mass fraction;")
    assert provenance["confidence"] == "Medium"  # 20 is converted, not printed
    [literature] = rows["sheet_01_scientific_literature"]
    assert literature["title"] == "Slag paper" and literature["doi_or_url"] == "10.1/x"
    assert literature["authors"] == "A. One, B. Two"
    assert literature["data_extraction_status"] == "Extracted"
    assert literature["extraction_model"] == "gemini-x"


def test_is_derived_spots_numbers_the_paper_does_not_print() -> None:
    assert not is_derived(_fact(value="45.2", reported_value="45.2 wt%"))
    assert not is_derived(_fact(value="45-60", reported_value="45–60", excerpt="CaO 45-60"))
    assert is_derived(_fact(value="20", reported_value="0.20 mass fraction", excerpt="0.20"))
    assert not is_derived(_fact(field="Notes", value="ground finely", reported_value="ground"))


def test_find_page_matches_the_excerpt_start_as_whole_words() -> None:
    pages = [(1, " introduction to slag "), (3, " table 1 cao 45 2 ")]
    assert find_page("CaO 45.2", pages) == 3
    assert find_page("CaO 5.2", pages) is None  # "5 2" must not match inside "45 2"
    assert find_page("", pages) is None


def test_build_rows_adds_the_page_and_lowers_confidence_of_converted_values() -> None:
    result = _rows(
        _fact(excerpt="CaO | 45.2"),
        _fact(field="SiO2_pct", value="20", reported_value="0.20", excerpt="SiO2 was 0.20"),
    )
    cao, sio2 = result.rows_by_table["sheet_09_citations_provenance"]
    assert cao["page_or_table_or_figure"] == "p. 3, Table 1"
    assert cao["confidence"] == "High"
    assert sio2["page_or_table_or_figure"] == "Table 1"  # excerpt not on any page
    assert sio2["confidence"] == "Medium"
    assert "confidence lowered" in sio2["provenance_notes"]
    assert (result.confidence_lowered, result.without_page) == (1, 1)
