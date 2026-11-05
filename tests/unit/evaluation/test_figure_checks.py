from steel_slag_slm.evaluation.figure_checks import (
    check_reading,
    count_out_of_range,
    figure_body_text,
    reading_labels,
    total_checks,
    unsupported_labels,
)
from steel_slag_slm.schemas import FigureReading, ProcessStep, TableColumn

PDF_TEXT = "0 5 10 15 Mix Weight Loss (%) O 30G 30L 12-Hour 180-Day"


def _bar_chart(rows: list[list[str]]) -> FigureReading:
    return FigureReading(
        figure_type="data_plot",
        summary="Weight loss by mix and curing time.",
        columns=[
            TableColumn(label="Mix", source="x_axis"),
            TableColumn(label="Legend", source="legend"),
            TableColumn(label="Weight Loss", unit="%", source="y_axis", axis_min=0, axis_max=15),
        ],
        rows=rows,
    )


def test_reading_labels_collects_columns_units_categories_phases_and_steps() -> None:
    reading = _bar_chart([["O", "12-Hour", "2.4"], ["30G", "12-Hour", "1.3"]])
    reading.phases.append("Merwinite")
    reading.steps.append(ProcessStep(id="s1", label="Grinding"))
    # The untitled legend's placeholder name is not a printed label, so it isn't checked
    assert reading_labels(reading) == [
        "Mix",
        "Weight Loss",
        "%",
        "O",
        "12-Hour",
        "30G",
        "Merwinite",
        "Grinding",
    ]


def test_unsupported_labels_ignores_case_spacing_and_punctuation() -> None:
    assert unsupported_labels(["weight  LOSS", "12 hour", "GGBS"], PDF_TEXT) == ["GGBS"]


def test_count_out_of_range_checks_numeric_cells_of_axis_columns_only() -> None:
    reading = _bar_chart(
        [
            ["O", "180-Day", "8.9"],
            ["30G", "180-Day", "15.5"],  # within 5% slack of the 15 % top tick
            ["30L", "180-Day", "40"],  # far above the axis
        ]
    )
    assert count_out_of_range(reading) == (3, 1)


def test_check_reading_counts_rows_without_one_cell_per_column() -> None:
    check = check_reading(_bar_chart([["O", "12-Hour", "2.4"], ["30G", "1.3"]]), PDF_TEXT)
    assert check.rows_malformed == 1
    assert check.values_checked == 1


def test_check_reading_skips_label_checks_for_pictures_without_pdf_text() -> None:
    check = check_reading(_bar_chart([["Made up", "12-Hour", "2.4"]]), "")
    assert not check.has_pdf_text
    assert check.labels_checked == 0 and check.unsupported_labels == ()
    assert check.values_checked == 1


def test_check_reading_reports_labels_missing_from_the_pdf_text() -> None:
    check = check_reading(_bar_chart([["Made up", "12-Hour", "2.4"]]), PDF_TEXT)
    assert check.unsupported_labels == ("Made up",)


def test_total_checks_adds_up_each_count() -> None:
    first = check_reading(_bar_chart([["O", "12-Hour", "2.4"]]), PDF_TEXT)
    second = check_reading(_bar_chart([["O", "12-Hour", "40"], ["bad"]]), "")
    totals = total_checks([first, second])
    assert (totals.figures_with_pdf_text, totals.values_checked) == (1, 2)
    assert (totals.values_out_of_range, totals.rows_malformed) == (1, 1)


def test_figure_body_text_drops_the_caption_so_caption_only_figures_are_not_checked() -> None:
    caption = "Figure 3. Mineralogical composition of un-reacted OPC."
    assert figure_body_text(f"{caption}", caption) == ""
    assert figure_body_text(f"0 10 20 MPa {caption}", caption) == "0 10 20 MPa"
    assert figure_body_text("0 10 20 MPa", "") == "0 10 20 MPa"
