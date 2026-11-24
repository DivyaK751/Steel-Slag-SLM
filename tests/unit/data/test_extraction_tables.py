import re
from pathlib import Path
from unittest.mock import MagicMock

from steel_slag_slm.config import ExtractionTablesSettings
from steel_slag_slm.data import extraction_tables

SETTINGS = ExtractionTablesSettings(
    dataset="steel_slag_extraction", location="US", export_dir=Path("reports/extraction")
)
BIGQUERY_COLUMN = re.compile(r"^[a-z_][a-z0-9_]*$")


def test_to_column_name_makes_workbook_headers_safe() -> None:
    assert extraction_tables.to_column_name("Source/Origin") == "source_or_origin"
    assert extraction_tables.to_column_name("CaO_pct") == "cao_pct"
    assert extraction_tables.to_column_name("Original_Source_Text_or_Excerpt") == (
        "original_source_text_or_excerpt"
    )


def test_every_sheet_table_has_unique_valid_columns_led_by_source_id() -> None:
    for sheet_table in extraction_tables.SHEET_TABLES:
        names = [field.name for field in extraction_tables.build_schema(sheet_table)]
        assert names[0] == "source_id"
        assert len(names) == len(set(names)), sheet_table.table
        assert all(BIGQUERY_COLUMN.match(name) for name in names), sheet_table.table
        # Workbook headers plus the two run fields; Source_ID becomes source_id
        assert len(names) == len(sheet_table.headers) + len(extraction_tables.RUN_FIELDS)


def test_build_schema_keeps_values_as_strings_and_requires_the_paper_id() -> None:
    schema = extraction_tables.build_schema(extraction_tables.SHEET_TABLES[1])
    by_name = {field.name: field for field in schema}
    assert by_name["source_id"].mode == "REQUIRED"
    assert by_name["cao_pct"].field_type == "STRING"  # ranges like "45-60" must survive
    assert by_name["extracted_at"].field_type == "TIMESTAMP"


def test_ensure_extraction_tables_creates_one_table_per_sheet() -> None:
    client = MagicMock(project="proj")
    table_ids = extraction_tables.ensure_extraction_tables(client, SETTINGS)
    assert len(table_ids) == 9
    assert table_ids[0] == "proj.steel_slag_extraction.sheet_01_scientific_literature"
    assert client.create_table.call_count == 9
    created = client.create_table.call_args_list[0].args[0]
    assert created.clustering_fields == ["source_id"]


def test_column_unit_reads_the_unit_from_the_header() -> None:
    assert extraction_tables.column_unit("CaO_pct") == "%"
    assert extraction_tables.column_unit("CO2_Uptake_pct_by_Mass") == "% by mass"
    assert extraction_tables.column_unit("Carbonation_Temperature_C") == "°C"
    assert extraction_tables.column_unit("Analysis_Method") is None


def test_replace_paper_rows_deletes_first_and_loads_literature_last() -> None:
    client = MagicMock()
    client.query.return_value.total_bytes_billed = 10_485_760
    rows = {
        "sheet_01_scientific_literature": [{"source_id": "W1"}],
        "sheet_02_feedstock_chemistry": [{"source_id": "W1"}],
        "sheet_03_processing": [],
    }
    assert extraction_tables.replace_paper_rows(client, "p.d", "W1", rows) == 10_485_760
    assert client.query.call_args.args[0].count("DELETE FROM") == 9
    loaded = [call.args[1] for call in client.load_table_from_json.call_args_list]
    assert loaded == ["p.d.sheet_02_feedstock_chemistry", "p.d.sheet_01_scientific_literature"]


def test_run_tables_add_a_run_id_and_cluster_on_it() -> None:
    schema = extraction_tables.build_schema(extraction_tables.SHEET_TABLES[1], with_run_id=True)
    assert schema[-1].name == "run_id" and schema[-1].mode == "REQUIRED"
    client = MagicMock(project="proj")
    extraction_tables.ensure_extraction_tables(client, SETTINGS, with_run_id=True)
    created = client.create_table.call_args_list[0].args[0]
    assert created.clustering_fields == ["run_id", "source_id"]


def test_replace_paper_rows_of_a_run_touches_only_that_run() -> None:
    client = MagicMock()
    client.query.return_value.total_bytes_billed = 0
    rows = {"sheet_01_scientific_literature": [{"source_id": "W1"}]}
    extraction_tables.replace_paper_rows(client, "p.runs", "W1", rows, run_id="v5-dev-1")
    assert client.query.call_args.args[0].count("AND run_id = @run_id") == 9
    assert client.load_table_from_json.call_args.args[0] == [
        {"source_id": "W1", "run_id": "v5-dev-1"}
    ]


def test_data_columns_leave_out_the_id_labels() -> None:
    chemistry = extraction_tables.TABLES_BY_NAME["sheet_02_feedstock_chemistry"]
    columns = extraction_tables.data_columns(chemistry)
    assert columns[0] == "Steel_Slag_Type"
    assert not {"Source_ID", "Material_ID", "Experiment_ID"} & set(columns)


def test_is_filled_treats_blank_and_na_as_empty() -> None:
    assert not any(map(extraction_tables.is_filled, [None, "", "  ", "NA", "n/a"]))
    assert all(map(extraction_tables.is_filled, ["0", "None", "XRF"]))
