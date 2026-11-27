from steel_slag_slm.agents.question_form import (
    ColumnFilter,
    QuestionForm,
    add_seen_values,
    clean_form,
    form_columns,
    form_json_schema,
    off_vocabulary,
)

CEMENT = "concrete_performance.cement_type"
SLAG_TYPE = "feedstock_chemistry.steel_slag_type"


def _clean_filter(column: str, value: str, op: str = "=") -> ColumnFilter:
    seen = {("6 Concrete Performance", "Cement_Type"): ("CEM I 42.5", "OPC (Type I)")}
    columns = add_seen_values(form_columns(), seen)
    form = QuestionForm(
        message_type="question", filters=[ColumnFilter(column=column, op=op, value=value)]
    )
    return clean_form(form, columns).filters[0]


def test_a_text_filter_is_spelled_as_the_data_spells_it():
    assert _clean_filter(CEMENT, "cem i 42.5").value == "CEM I 42.5"
    assert _clean_filter(SLAG_TYPE, "bof").value == "BOF"


def test_a_text_filter_matching_no_value_looks_inside_the_values():
    fixed = _clean_filter(CEMENT, "OPC")
    assert (fixed.op, fixed.value) == ("contains", "OPC")


def test_number_filters_are_left_alone():
    form = QuestionForm(
        message_type="question",
        filters=[ColumnFilter(column="concrete_performance.scm_replacement_pct", op="=", value=20)],
    )
    assert clean_form(form, form_columns()).filters == form.filters


def _by_name():
    return {column.name: column for column in form_columns()}


def test_columns_come_from_the_sheets_with_units_and_vocabulary():
    columns = _by_name()
    strength = columns["concrete_performance.compressive_strength_28d_mpa"]
    assert (strength.sheet, strength.unit) == ("6 Concrete Performance", "MPa")
    assert "BOF" in columns["feedstock_chemistry.steel_slag_type"].allowed_values
    assert "feedstock_chemistry.cao_pct" in columns


def test_id_columns_and_the_provenance_sheet_are_not_askable():
    names = set(_by_name())
    assert not any(name.endswith(".source_id") or name.endswith(".mix_id") for name in names)
    assert not any(name.startswith("citations_provenance.") for name in names)


def test_schema_limits_columns_and_oxides_to_real_names():
    schema = form_json_schema(form_columns())
    property_names = schema["properties"]["properties"]["items"]["enum"]
    assert "concrete_performance.compressive_strength_28d_mpa" in property_names
    filter_column = schema["$defs"]["ColumnFilter"]["properties"]["column"]
    assert filter_column["enum"] == property_names
    assert "cao_pct" in schema["properties"]["chemistry"]["propertyNames"]["enum"]


def test_a_filter_value_outside_its_columns_list_is_reported():
    form = QuestionForm(
        message_type="question",
        filters=[ColumnFilter(column="concrete_performance.steel_slag_type", op="=", value="LD"),
                 ColumnFilter(column="feedstock_chemistry.steel_slag_type", op="=", value="bof")],
    )  # fmt: skip
    problems = off_vocabulary(form, form_columns())
    assert len(problems) == 1 and "'LD' is not allowed" in problems[0] and "BOF" in problems[0]


def test_clean_form_drops_unknown_columns_oxides_and_repeats():
    form = QuestionForm(
        message_type="question",
        properties=[
            "concrete_performance.compressive_strength_28d_mpa",
            "concrete_performance.compressive_strength_28d_mpa",
            "made_up.column",
        ],
        filters=[
            ColumnFilter(column="feedstock_chemistry.steel_slag_type", op="=", value="BOF"),
            ColumnFilter(column="made_up.column", op=">", value=1.0),
        ],
        chemistry={"cao_pct": 40.0, "unobtainium_pct": 3.0},
    )

    cleaned = clean_form(form, form_columns())

    assert cleaned.properties == ["concrete_performance.compressive_strength_28d_mpa"]
    assert [f.column for f in cleaned.filters] == ["feedstock_chemistry.steel_slag_type"]
    assert cleaned.chemistry == {"cao_pct": 40.0}
