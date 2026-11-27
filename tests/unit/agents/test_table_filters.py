import pytest

from steel_slag_slm.agents.question_form import ColumnFilter, form_columns
from steel_slag_slm.agents.table_filters import filter_lookup, select_slags, value_matches
from steel_slag_slm.data.slag_catalogue import SlagCatalogueEntry
from steel_slag_slm.rag.slag_lookup import SheetRow, SlagEvidence, SlagLookup

COLUMNS = form_columns()
CONCRETE = "6 Concrete Performance"
STRENGTH = "concrete_performance.compressive_strength_28d_mpa"
REPLACEMENT = "concrete_performance.scm_replacement_pct"


def _entry(source: str, slag_type: str, cao: float) -> SlagCatalogueEntry:
    return SlagCatalogueEntry(
        source, "MAT-0001", None, slag_type, {"cao_pct": cao}, (), 95.0, True, (), None
    )


def _row(sheet: str, **values: str) -> SheetRow:
    return SheetRow(sheet, "W1", "MAT-0001", None, None, dict(values), {})


@pytest.mark.parametrize(
    ("value", "op", "target", "expected"),
    [
        ("45.2", ">", 40.0, True),
        ("32-48", ">", 40.0, True),  # a range meets a bound if any of it does
        ("38-42", "=", 40.0, True),
        ("20.0", "=", 20.0, True),
        ("12", "between", [10.0, 15.0], True),
        ("BOF", "=", "bof", True),
        ("Water cured 28 d", "contains", "cured", True),
        ("not reported", ">", 1.0, False),
        (None, "=", "BOF", False),
    ],
)
def test_values_are_compared_as_ranges_or_case_free_text(value, op, target, expected):
    condition = ColumnFilter(column=STRENGTH, op=op, value=target)
    assert value_matches(value, condition) is expected


def test_slag_type_and_chemistry_filters_choose_catalogue_slags():
    catalogue = [_entry("W1", "BOF", 45.0), _entry("W2", "BOF", 35.0), _entry("W3", "EAF", 50.0)]
    filters = [
        ColumnFilter(column="concrete_performance.steel_slag_type", op="=", value="BOF"),
        ColumnFilter(column="feedstock_chemistry.cao_pct", op=">", value=40.0),
        ColumnFilter(column=STRENGTH, op=">", value=30.0),  # a result filter: ignored here
    ]
    assert [e.source_id for e in select_slags(catalogue, filters, COLUMNS)] == ["W1"]


def test_without_slag_filters_every_catalogue_slag_is_chosen():
    catalogue = [_entry("W1", "BOF", 45.0), _entry("W2", "EAF", 35.0)]
    assert len(select_slags(catalogue, [], COLUMNS)) == 2


def _lookup() -> SlagLookup:
    rows = {
        CONCRETE: [
            _row(CONCRETE, SCM_Replacement_pct="20", Compressive_Strength_28d_MPa="41.3"),
            _row(CONCRETE, SCM_Replacement_pct="40", Compressive_Strength_28d_MPa="30.1"),
            _row(CONCRETE, SCM_Replacement_pct="20", Workability_Slump_mm="80"),
        ],
        "7 Durability": [_row("7 Durability", Expansion_pct="0.4")],
    }
    slag = SlagEvidence("S1", _entry("W1", "BOF", 45.0), None, rows)
    return SlagLookup([slag], [], [])


def test_result_filters_and_properties_keep_only_the_rows_that_answer():
    filters = [ColumnFilter(column=REPLACEMENT, op="=", value=20.0)]

    narrowed = filter_lookup(_lookup(), filters, [STRENGTH], COLUMNS)

    (slag,) = narrowed.slags
    assert list(slag.rows_by_sheet) == [CONCRETE]
    assert [row.values["Compressive_Strength_28d_MPa"] for row in slag.rows_by_sheet[CONCRETE]] == [
        "41.3"
    ]
    assert "7 Durability" in narrowed.sheets_without_data
    (summary,) = [s for s in narrowed.summaries if s.column == "Compressive_Strength_28d_MPa"]
    assert (summary.low, summary.high) == (41.3, 41.3)


def test_slags_with_nothing_left_are_dropped():
    filters = [ColumnFilter(column=REPLACEMENT, op="=", value=60.0)]
    assert filter_lookup(_lookup(), filters, [STRENGTH], COLUMNS).slags == []
