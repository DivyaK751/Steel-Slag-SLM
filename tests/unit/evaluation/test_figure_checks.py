from steel_slag_slm.evaluation.figure_checks import (
    check_reading,
    count_out_of_range,
    reading_labels,
    unsupported_labels,
)
from steel_slag_slm.schemas import (
    AxisReading,
    FigureReading,
    PointReading,
    ProcessStep,
    SeriesReading,
)

PDF_TEXT = "0 10 20 30 40 50 Compressive strength (MPa) Curing age (days) OPC 30% BOF"


def _plot(points: list[PointReading], series_name: str = "30% BOF") -> FigureReading:
    return FigureReading(
        figure_type="data_plot",
        summary="Strength rises with curing age.",
        x_axis=AxisReading(label="Curing age", unit="days", min_value=0, max_value=90),
        y_axis=AxisReading(label="Compressive strength", unit="MPa", min_value=0, max_value=50),
        series=[SeriesReading(name=series_name, points=points)],
    )


def test_reading_labels_collects_axes_units_series_phases_and_steps() -> None:
    reading = _plot([PointReading(x_category="7 d", y=20)])
    reading.phases.append("Merwinite")
    reading.steps.append(ProcessStep(id="s1", label="Grinding"))
    assert reading_labels(reading) == [
        "Curing age",
        "days",
        "Compressive strength",
        "MPa",
        "30% BOF",
        "7 d",
        "Merwinite",
        "Grinding",
    ]


def test_unsupported_labels_ignores_case_spacing_and_punctuation() -> None:
    assert unsupported_labels(["compressive  STRENGTH", "30 % BOF", "GGBS"], PDF_TEXT) == ["GGBS"]


def test_count_out_of_range_flags_values_beyond_the_reported_axes() -> None:
    reading = _plot(
        [
            PointReading(x=28, y=42.3),
            PointReading(x=90, y=52.0),  # within 5% slack of the 50 MPa top tick
            PointReading(x=28, y=80.0),  # far above the axis
        ]
    )
    assert count_out_of_range(reading) == (3, 1)


def test_check_reading_skips_label_checks_for_pictures_without_pdf_text() -> None:
    check = check_reading(_plot([PointReading(x=28, y=42.3)], series_name="Made up"), "")
    assert not check.has_pdf_text
    assert check.labels_checked == 0 and check.unsupported_labels == ()
    assert check.points_checked == 1


def test_check_reading_reports_labels_missing_from_the_pdf_text() -> None:
    check = check_reading(_plot([PointReading(x=28, y=42.3)], series_name="Made up"), PDF_TEXT)
    assert check.unsupported_labels == ("Made up",)
    assert check.labels_checked == 5
