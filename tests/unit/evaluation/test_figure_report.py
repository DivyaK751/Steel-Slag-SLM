from pathlib import Path

from PIL import Image

from steel_slag_slm.evaluation.figure_checks import CheckTotals, FigureCheck
from steel_slag_slm.evaluation.figure_report import ReviewRow, write_review_page
from steel_slag_slm.schemas import FigureReading, TableColumn

TOTALS = CheckTotals(1, 4, 1, 3, 0, 0)


def _image(tmp_path: Path) -> Path:
    path = tmp_path / "figure_01.png"
    Image.new("RGB", (800, 600), "white").save(path)
    return path


def test_write_review_page_shows_reading_checks_and_failures(tmp_path: Path) -> None:
    image = _image(tmp_path)
    reading = FigureReading(
        figure_type="data_plot",
        summary="Strength <rises> with age",
        columns=[
            TableColumn(label="Age", unit="days", source="x_axis"),
            TableColumn(label="Strength", unit="MPa", source="y_axis"),
        ],
        rows=[["28", "42.3"]],
    )
    check = FigureCheck(True, 4, ("Made up",), 3, 0, 1)
    rows = [
        ReviewRow("W1 · Fig. 1", image, "Fig. 1 Strength", reading, check, 0.0021),
        ReviewRow("W1 · Fig. 2", image, "Fig. 2", None, None, 0.0, error="malformed answer"),
    ]
    page = tmp_path / "out" / "review.html"
    write_review_page(rows, ["Cost: total $0.0021"], TOTALS, page)
    text = page.read_text()
    assert "Strength &lt;rises&gt; with age" in text  # model text is escaped
    assert "not in PDF text: Made up" in text
    assert "malformed answer" in text
    assert "<th>Strength [MPa]" in text and "<td>42.3</td>" in text
    assert "1 rows with missing cells" in text
    assert "data:image/jpeg;base64," in text
