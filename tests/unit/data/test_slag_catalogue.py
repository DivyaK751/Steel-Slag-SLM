from datetime import UTC, datetime
from typing import Any

import pytest

from steel_slag_slm.config import (
    OxideRange,
    SlagCatalogueSettings,
    SlagReference,
    SlagTypeReference,
)
from steel_slag_slm.data.slag_catalogue import (
    build_entries,
    catalogue_schema,
    clean_oxides,
    heavy_metal_flags,
    oxide_coverage,
    range_flags,
    to_table_row,
)

SETTINGS = SlagCatalogueSettings(
    dataset="steel_slag_extraction",
    table="slag_catalogue",
    min_coverage_pct=90,
    max_range_width=2,
    heavy_metal_limits={"cr2o3_pct": 0.5, "v2o5_pct": 0.5},
)
EAF = SlagTypeReference(
    label="EAF slag",
    process="Scrap melting",
    oxides={
        "cao_pct": OxideRange(min=22, max=60),
        "total_fe2o3_pct": OxideRange(min=10, max=40),
    },
    mineral_phases=["Wüstite"],
)
REFERENCE = SlagReference(slag_types={"EAF": EAF}, phase_names={"Wüstite": ["wüstite", "RO phase"]})


def _row(**values: Any) -> dict[str, Any]:
    return {"source_id": "W1", "material_id": "MAT-0001", "experiment_id": "EXP-0001", **values}


def test_clean_oxides_fills_gaps_from_the_trace_list_and_totals_the_iron() -> None:
    row = _row(
        cao_pct="38",
        fe2o3_pct="5",
        mno_pct=None,
        trace_elements="FeO: 20%; MnO: 3.8 wt%; Cr: 2.1%; V2O5: 0.6 ± 0.3%",
    )
    oxides = clean_oxides(row, max_range_width=2)
    assert oxides == {
        "cao_pct": 38.0,
        "fe2o3_pct": 5.0,
        "mno_pct": 3.8,
        "feo_pct": 20.0,
        "total_fe2o3_pct": 27.22,
        "cr2o3_pct": 3.07,
        "v2o5_pct": 0.6,
    }


def test_oxide_coverage_counts_iron_once_and_uses_the_reported_total() -> None:
    oxides = {"cao_pct": 38, "sio2_pct": 16, "fe2o3_pct": 5, "total_fe2o3_pct": 27, "mgo_pct": 7}
    assert oxide_coverage(oxides, None) == 88.0
    assert oxide_coverage(oxides, 95.0) == pytest.approx(92.6)


def test_range_flags_name_each_oxide_outside_its_type_range() -> None:
    flags = range_flags({"cao_pct": 72, "total_fe2o3_pct": 25}, "EAF", EAF)
    assert flags == ["CaO 72% above typical EAF range 22-60%"]


def test_heavy_metal_flags_only_values_above_the_limit() -> None:
    flags = heavy_metal_flags({"cr2o3_pct": 0.8, "v2o5_pct": 0.5}, SETTINGS.heavy_metal_limits)
    assert flags == ["Cr2O3 0.8% above 0.5%: leaching risk, possible recovery value"]


def test_build_entries_marks_comparable_slags_and_reads_phases() -> None:
    complete = _row(
        steel_slag_type="EAF",
        cao_pct="40",
        sio2_pct="15",
        fe2o3_pct="30",
        mgo_pct="8",
        notes="XRD: RO phase",
    )
    sparse = _row(material_id="MAT-0002", steel_slag_type="Other Steel Slag", cao_pct="40")
    entries = build_entries([complete, sparse], REFERENCE, SETTINGS)
    assert [entry.is_comparable for entry in entries] == [True, False]
    assert entries[0].mineral_phases == ("Wüstite",)
    assert entries[0].flags == ()
    assert entries[1].oxide_coverage_pct == 40.0


def test_to_table_row_fills_every_schema_column() -> None:
    entry = build_entries([_row(steel_slag_type="EAF", cao_pct="40")], REFERENCE, SETTINGS)[0]
    row = to_table_row(entry, datetime(2026, 11, 19, tzinfo=UTC))
    assert list(row) == [field.name for field in catalogue_schema()]
    assert row["cao_pct"] == 40.0 and row["sio2_pct"] is None
