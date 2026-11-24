import pytest

from steel_slag_slm.data.extraction_tables import TABLES_BY_NAME, data_columns
from steel_slag_slm.evaluation.extraction_metrics import (
    build_cells,
    same_value,
    score_cells,
    score_fill,
)
from steel_slag_slm.schemas import CellVerdict, JudgedTable, RowPair, TableJudgement

CHEMISTRY = TABLES_BY_NAME["sheet_02_feedstock_chemistry"]


def _judgement(*pairs: tuple[int, int, dict[str, str]]) -> TableJudgement:
    return TableJudgement(
        pairs=[
            RowPair(
                reference_row=reference,
                extracted_row=extracted,
                cells=[
                    CellVerdict(column=column, verdict=verdict, reason="r")  # type: ignore[arg-type]
                    for column, verdict in verdicts.items()
                ],
            )
            for reference, extracted, verdicts in pairs
        ]
    )


def _outcomes(cells: list) -> dict[str, str]:
    return {
        f"{cell.reference_row}/{cell.extracted_row} {cell.column}": cell.outcome for cell in cells
    }


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("0.30", "0.3", True),
        ("XRF", " xrf ", True),
        ("40.4", "40.42", False),
        ("12", "12 h", False),
    ],
)
def test_same_value_ignores_case_spacing_and_trailing_zeros(
    first: str, second: str, expected: bool
) -> None:
    assert same_value(first, second) is expected


def test_build_cells_decides_empty_and_identical_cells_without_the_judge() -> None:
    reference = [{"CaO_pct": "40.42", "SiO2_pct": "14.1", "MgO_pct": "4.5", "LOI_pct": "NA"}]
    extracted = [{"CaO_pct": "40.420", "SiO2_pct": "15.3", "Al2O3_pct": "7.02"}]
    judgement = _judgement((1, 1, {"SiO2_pct": "partial"}))

    cells, matched = build_cells("W1", CHEMISTRY, reference, extracted, judgement)

    assert matched == 1
    assert _outcomes(cells) == {
        "1/1 CaO_pct": "match",  # identical number, no verdict needed
        "1/1 SiO2_pct": "partial",  # differing values take the judge's verdict
        "1/1 MgO_pct": "missed",
        "1/1 Al2O3_pct": "extra",
    }  # LOI "NA" counts as empty on both sides


def test_build_cells_counts_unpaired_rows_as_missed_or_extra() -> None:
    reference = [{"CaO_pct": "40"}, {"CaO_pct": "37"}]
    extracted = [{"CaO_pct": "37"}, {"MgO_pct": "6"}]

    cells, matched = build_cells("W1", CHEMISTRY, reference, extracted, _judgement((2, 1, {})))

    assert matched == 1
    assert _outcomes(cells) == {
        "2/1 CaO_pct": "match",
        "1/None CaO_pct": "missed",
        "None/2 MgO_pct": "extra",
    }


def test_build_cells_drops_pairs_that_reuse_a_row_or_name_no_row() -> None:
    reference = [{"CaO_pct": "40"}]
    extracted = [{"CaO_pct": "40"}, {"CaO_pct": "41"}]
    judgement = _judgement((1, 1, {}), (1, 2, {}), (3, 1, {}))

    cells, matched = build_cells("W1", CHEMISTRY, reference, extracted, judgement)

    assert matched == 1
    assert _outcomes(cells) == {"1/1 CaO_pct": "match", "None/2 CaO_pct": "extra"}


def test_a_differing_value_without_a_verdict_is_a_mismatch() -> None:
    cells, _ = build_cells(
        "W1", CHEMISTRY, [{"CaO_pct": "40"}], [{"CaO_pct": "41"}], _judgement((1, 1, {}))
    )
    assert cells[0].outcome == "mismatch"
    assert cells[0].reason == "judge gave no verdict"


def test_score_cells_gives_partial_half_credit() -> None:
    reference = [{"CaO_pct": "40", "SiO2_pct": "14", "MgO_pct": "4", "Al2O3_pct": "7"}]
    extracted = [{"CaO_pct": "40", "SiO2_pct": "15", "MgO_pct": "4.5", "Fe2O3_pct": "20"}]
    judgement = _judgement((1, 1, {"SiO2_pct": "mismatch", "MgO_pct": "partial"}))
    cells, _ = build_cells("W1", CHEMISTRY, reference, extracted, judgement)

    scores = score_cells(cells)

    # match 1 + partial 0.5 over 4 values each side; 3 values on both sides
    assert scores.precision == pytest.approx(1.5 / 4)
    assert scores.recall == pytest.approx(1.5 / 4)
    assert scores.f1 == pytest.approx(1.5 / 4)
    assert scores.accuracy == pytest.approx(1.5 / 3)
    assert scores.adjudicated_precision is None


def test_adjudicated_precision_credits_values_the_paper_supports() -> None:
    cells, _ = build_cells("W1", CHEMISTRY, [], [{"CaO_pct": "40", "MgO_pct": "4"}], None)
    cells[0].adjudication = "supported"
    cells[1].adjudication = "unsupported"

    scores = score_cells(cells)

    assert scores.precision == 0.0
    assert scores.adjudicated_precision == pytest.approx(0.5)


def test_score_fill_measures_filled_cells_against_every_data_column() -> None:
    columns = len(data_columns(CHEMISTRY))
    tables = [
        JudgedTable("W1", CHEMISTRY.table, 2, 1, 6, 4, matched_rows=1),
        JudgedTable("W1", "sheet_09_citations_provenance", 3, 0, 9, 0, matched_rows=None),
    ]

    fill = score_fill(tables)

    assert fill.extracted_fill_rate == pytest.approx(4 / columns)
    assert fill.row_recall == pytest.approx(1 / 2)  # provenance rows aren't paired
    assert fill.row_precision == pytest.approx(1.0)


def test_numbers_within_two_percent_match_even_if_judged_before() -> None:
    reference = [{"CaO_pct": "42.5", "SiO2_pct": "14", "Notes": "x"}]
    extracted = [{"CaO_pct": "43.0", "SiO2_pct": "14.5", "Notes": "y"}]
    cells, _ = build_cells("W1", CHEMISTRY, reference, extracted, _judgement((1, 1, {})))
    by_column = {cell.column: cell for cell in cells}
    # 42.5 vs 43.0 is 1.2% apart; 14 vs 14.5 is 3.4%; text never gets a tolerance
    assert by_column["CaO_pct"].outcome == "match"
    assert by_column["SiO2_pct"].outcome == "mismatch"
    assert by_column["Notes"].outcome == "mismatch"
    # A cell stored as a mismatch before the tolerance existed scores as a match
    by_column["CaO_pct"].outcome = "mismatch"
    assert score_cells([by_column["CaO_pct"]]).match == 1


def test_data_accuracy_leaves_out_notes_columns() -> None:
    reference = [{"CaO_pct": "40", "SiO2_pct": "14", "Notes": "fresh slag"}]
    extracted = [{"CaO_pct": "40", "SiO2_pct": "20", "Notes": "aged slag"}]
    judgement = _judgement((1, 1, {"SiO2_pct": "mismatch", "Notes": "mismatch"}))
    cells, _ = build_cells("W1", CHEMISTRY, reference, extracted, judgement)

    scores = score_cells(cells)

    assert scores.accuracy == pytest.approx(1 / 2)  # CaO and SiO2 only
    assert scores.precision == pytest.approx(1 / 3)  # notes still count in precision
