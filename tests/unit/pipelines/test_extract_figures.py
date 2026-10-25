import json
from pathlib import Path

import pymupdf
import pytest

import steel_slag_slm.pipelines.extract_figures as extract_figures
from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.data.figure_detection import DetectedLayout
from steel_slag_slm.schemas import CaptionBlock, Figure

SETTINGS = FigureExtractionSettings(
    output_dir=Path("unused"),
    render_dpi=72,
    caption_max_gap_pt=60,
    min_side_pt=72,
    min_split_height_pt=50,
    drop_types=["logo"],
    min_uncaptioned_side_pt=150,
)


def _pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "a page")
        return bytes(document.tobytes())


@pytest.fixture
def fake_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace Docling with one captioned figure, one logo and one small uncaptioned photo."""
    layout = DetectedLayout(
        figures=[
            Figure(1, (50, 100, 300, 300), "line_chart", 0.9),
            Figure(1, (10, 10, 100, 100), "logo", 0.9),
            Figure(1, (50, 500, 145, 625), "photograph", 0.6),
        ],
        captions=[CaptionBlock(1, (50, 310, 300, 330), "Fig. 1 Strength vs age", "1")],
    )
    monkeypatch.setattr(extract_figures, "detect_figures", lambda pdf, converter: layout)


def test_collect_pdf_paths_expands_folders_and_skips_other_files(tmp_path: Path) -> None:
    (tmp_path / "b.pdf").write_bytes(b"")
    (tmp_path / "a.pdf").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("")
    paths = extract_figures.collect_pdf_paths([tmp_path, tmp_path / "a.pdf"])
    assert [path.name for path in paths] == ["a.pdf", "b.pdf"]


@pytest.mark.usefixtures("fake_layout")
def test_extract_paper_figures_writes_kept_figures_and_their_metadata(tmp_path: Path) -> None:
    kept = extract_figures.extract_paper_figures(_pdf(), tmp_path / "W1", None, SETTINGS)  # type: ignore[arg-type]
    records = json.loads((tmp_path / "W1" / "figures.json").read_text())
    assert kept == 1
    assert records[0]["figure_number"] == "1"
    assert records[0]["caption"] == "Fig. 1 Strength vs age"
    assert (tmp_path / "W1" / records[0]["file"]).read_bytes().startswith(b"\x89PNG")


@pytest.mark.usefixtures("fake_layout")
def test_run_figure_extraction_continues_past_an_unreadable_pdf(tmp_path: Path) -> None:
    good, bad = tmp_path / "W1.pdf", tmp_path / "W2.pdf"
    good.write_bytes(_pdf())
    bad.write_bytes(b"not a pdf")
    outcomes = extract_figures.run_figure_extraction(
        [good, bad],
        tmp_path / "out",
        None,  # type: ignore[arg-type]
        SETTINGS,
    )
    assert outcomes == {"papers_done": 1, "papers_failed": 1, "figures_kept": 1}
