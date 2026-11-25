from unittest.mock import MagicMock

from steel_slag_slm.data.column_values import read_seen_values, text_columns
from steel_slag_slm.data.extraction_tables import SHEET_TABLES

CONCRETE = SHEET_TABLES[5]


def _client(rows: list[dict]) -> MagicMock:
    client = MagicMock()
    client.query.return_value.result.return_value = [MagicMock(items=r.items) for r in rows]
    return client


def test_text_columns_have_no_unit_and_no_fixed_vocabulary():
    columns = text_columns(CONCRETE)
    assert "Cement_Type" in columns
    assert "Compressive_Strength_28d_MPa" not in columns  # has a unit
    assert "Steel_Slag_Type" not in columns  # has a vocabulary
    assert "Mix_ID" not in columns  # an ID, not data


def test_one_query_reads_every_text_column_of_the_given_sheets():
    client = _client([])
    read_seen_values(client, "ds", 12, [CONCRETE])
    sql = client.query.call_args.args[0]
    assert "`ds.sheet_06_concrete_performance`" in sql and "cement_type" in sql
    assert client.query.call_count == 1


def test_values_are_kept_most_common_first_without_blanks_or_short_lists_only():
    rows = [
        {"sheet": CONCRETE.sheet, "header": "Cement_Type", "value": "CEM I 42.5", "n": 9},
        {"sheet": CONCRETE.sheet, "header": "Cement_Type", "value": "NA", "n": 5},
        {"sheet": CONCRETE.sheet, "header": "Cement_Type", "value": "OPC", "n": 2},
    ] + [
        {"sheet": CONCRETE.sheet, "header": "Admixtures", "value": f"mix {i}", "n": 1}
        for i in range(3)
    ]
    seen = read_seen_values(_client(rows), "ds", 2, [CONCRETE])
    assert seen == {(CONCRETE.sheet, "Cement_Type"): ("CEM I 42.5", "OPC")}  # 3 admixtures > 2
