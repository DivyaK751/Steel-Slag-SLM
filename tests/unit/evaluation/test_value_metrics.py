import pytest

from steel_slag_slm.data.extraction_tables import TABLES_BY_NAME
from steel_slag_slm.evaluation.value_metrics import (
    ValueScores,
    count_values,
    leading_number,
    match_values,
    sum_counts,
    value_columns,
)

CONCRETE = "sheet_06_concrete_performance"


def test_value_columns_keep_numbers_and_labels_but_not_free_text_or_ids() -> None:
    columns = value_columns(TABLES_BY_NAME[CONCRETE])
    assert "Compressive_Strength_28d_MPa" in columns
    assert "Strength_Notes" not in columns and "Mix_ID" not in columns
    assert "Carbonation_Time" in value_columns(TABLES_BY_NAME["sheet_08_carbon_uptake"])


@pytest.mark.parametrize(
    ("value", "number"),
    [("38.2 ± 1.1", 38.2), (">40", 40.0), ("45-60", 45.0), ("0.14 (L/S)", 0.14), ("BOF", None)],
)
def test_leading_number(value: str, number: float | None) -> None:
    assert leading_number(value) == number


def test_match_values_pairs_close_numbers_once_and_labels_by_text() -> None:
    # 41.5 is within 5% of 41.05; each value is used once, so only one 41 can match
    assert match_values(["41.05", "52.2", "EAF"], ["41.5", "41.0", "eaf", "70"]) == 2
    assert match_values(["41.05", "52.2"], ["41.5", "52.0"]) == 2
    assert match_values(["30"], ["33"]) == 0


def test_match_values_prefers_the_closest_pair() -> None:
    # 50.5 goes to 50.52 (exact), leaving 52.0 for 51.98
    assert match_values(["50.52", "51.98"], ["52.0", "50.5"]) == 2


def test_count_values_ignores_rows_and_counts_a_missing_paper_as_missed() -> None:
    reference = {
        ("W1", CONCRETE): [
            {"Compressive_Strength_28d_MPa": "41.05", "Strength_Notes": "x"},
            {"Compressive_Strength_28d_MPa": "52.2"},
        ],
        ("W2", CONCRETE): [{"Compressive_Strength_28d_MPa": "30"}],
    }
    # The same two values split into different rows still match in full
    extracted = {
        ("W1", CONCRETE): [
            {"Compressive_Strength_28d_MPa": "52.2", "Water_Binder_Ratio": "0.5"},
            {"Compressive_Strength_28d_MPa": "41.0"},
        ]
    }
    counts = count_values(reference, extracted, ["W1", "W2"])
    scores = sum_counts(counts)
    assert (scores.reference, scores.extracted, scores.matched) == (3, 3, 2)
    assert {(c.source_id, c.column) for c in counts} == {
        ("W1", "Compressive_Strength_28d_MPa"),
        ("W1", "Water_Binder_Ratio"),
        ("W2", "Compressive_Strength_28d_MPa"),
    }


def test_value_scores() -> None:
    scores = ValueScores(reference=4, extracted=2, matched=2)
    assert scores.precision == 1.0 and scores.recall == 0.5
    assert scores.f1 == pytest.approx(2 / 3)
    assert ValueScores(0, 0, 0).f1 is None
    assert ValueScores(2, 2, 0).f1 == 0.0


def test_a_processing_step_list_counts_each_step() -> None:
    sheet = "sheet_03_processing"
    reference = {("W1", sheet): [{"Processing_Method": "Milling; Carbonation"}]}
    extracted = {("W1", sheet): [{"Processing_Method": "Milling"}]}
    scores = sum_counts(count_values(reference, extracted, ["W1"]))
    assert (scores.reference, scores.extracted, scores.matched) == (2, 1, 1)
