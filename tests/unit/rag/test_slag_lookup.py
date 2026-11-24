from dataclasses import replace
from typing import Any

from steel_slag_slm.data.slag_catalogue import SlagCatalogueEntry
from steel_slag_slm.rag.slag_lookup import (
    build_lookup,
    index_locations,
    render_evidence,
    select_slag_rows,
)

SLAG = SlagCatalogueEntry(
    source_id="W1",
    material_id="MAT-0001",
    experiment_id="EXP-0001",
    steel_slag_type="EAF",
    oxides={"cao_pct": 38.0},
    mineral_phases=("Wüstite",),
    oxide_coverage_pct=95.0,
    is_comparable=True,
    flags=(),
    notes=None,
)
CITATION = {
    "source_id": "W1",
    "title": "EAF slag as SCM",
    "authors": "A. Author",
    "publication_year": "2023",
    "doi_or_url": "https://doi.org/10/x",
}


def _row(**values: Any) -> dict[str, Any]:
    return {"source_id": "W1", "material_id": "MAT-0001", "experiment_id": "EXP-0002", **values}


def test_select_slag_rows_prefers_the_slags_experiment_when_the_sheet_has_it() -> None:
    same = _row(experiment_id="EXP-0001")
    other = _row(experiment_id="EXP-0002")
    other_material = _row(material_id="MAT-0002", experiment_id="EXP-0001")
    assert select_slag_rows([same, other, other_material], SLAG) == [same]
    assert select_slag_rows([other, other_material], SLAG) == [other]


def test_index_locations_joins_distinct_places_for_one_value() -> None:
    provenance = [
        {**_row(), "fact_or_data_element": "Compressive_Strength_28d_MPa",
         "page_or_table_or_figure": where}
        for where in ("p. 6, Table 3", "p. 7, Fig. 2", "p. 6, Table 3")
    ]  # fmt: skip
    key = ("W1", "MAT-0001", "EXP-0002", "Compressive_Strength_28d_MPa")
    assert index_locations(provenance) == {key: "p. 6, Table 3; p. 7, Fig. 2"}


def test_build_lookup_keeps_sheet_names_headers_and_pages_and_drops_empty_cells() -> None:
    performance = _row(
        mix_id="M20",
        steel_slag_type="EAF",
        scm_replacement_pct="20",
        cement_type="NA",
        compressive_strength_28d_mpa="41",
        extracted_at="2026-11-01",
    )
    provenance = [
        {**_row(), "fact_or_data_element": "Compressive_Strength_28d_MPa",
         "page_or_table_or_figure": "p. 6, Table 3"}
    ]  # fmt: skip
    lookup = build_lookup(
        [SLAG], {"sheet_06_concrete_performance": [performance]}, [CITATION], provenance
    )
    [row] = lookup.slags[0].rows_by_sheet["6 Concrete Performance"]
    assert row.values == {"SCM_Replacement_pct": "20", "Compressive_Strength_28d_MPa": "41"}
    assert row.mix_id == "M20"
    assert row.locations == {"Compressive_Strength_28d_MPa": "p. 6, Table 3"}
    assert lookup.slags[0].citation is not None and lookup.slags[0].citation.year == "2023"
    assert "6 Concrete Performance" not in lookup.sheets_without_data
    assert "8 Carbon Uptake" in lookup.sheets_without_data


def test_build_lookup_summarises_numeric_columns_across_slags() -> None:
    second = replace(SLAG, material_id="MAT-0002")
    rows = [
        _row(compressive_strength_28d_mpa="32-40"),
        _row(material_id="MAT-0002", compressive_strength_28d_mpa="48"),
    ]
    lookup = build_lookup([SLAG, second], {"sheet_06_concrete_performance": rows}, [], [])
    [summary] = lookup.summaries
    assert (summary.column, summary.low, summary.high) == ("Compressive_Strength_28d_MPa", 32, 48)
    assert (summary.value_count, summary.study_count, summary.sources) == (2, 1, ("S1", "S2"))


def test_build_lookup_gives_rows_shared_by_two_slags_only_to_the_first() -> None:
    sibling = replace(SLAG, experiment_id="EXP-0009")
    rows = [_row(compressive_strength_28d_mpa="41")]
    lookup = build_lookup([SLAG, sibling], {"sheet_06_concrete_performance": rows}, [], [])
    assert [len(slag.rows_by_sheet) for slag in lookup.slags] == [1, 0]


def test_render_evidence_cites_every_row_and_states_missing_sheets() -> None:
    rows = [_row(mix_id="M20", compressive_strength_28d_mpa="41")]
    lookup = build_lookup([SLAG], {"sheet_06_concrete_performance": rows}, [CITATION], [])
    text = render_evidence(lookup)
    assert "[S1] W1 MAT-0001 (EAF): EAF slag as SCM (2023). A. Author. https://doi.org/10/x" in text
    assert "- [S1 EXP-0002 / M20] Compressive_Strength_28d_MPa: 41" in text
    assert "## 8 Carbon Uptake\nNo data for these slags." in text
