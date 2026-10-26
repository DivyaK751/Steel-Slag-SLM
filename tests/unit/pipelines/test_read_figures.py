import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import steel_slag_slm.pipelines.read_figures as read_figures
from steel_slag_slm.config import FigureReadingSettings, ModelPrices
from steel_slag_slm.evaluation.figure_checks import total_checks
from steel_slag_slm.exceptions import AgentOutputError
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import AxisReading, FigureReading, FigureToRead, TokenUsage

SETTINGS = FigureReadingSettings(
    model="fake-model",
    temperature=0.0,
    prompt_name="read_figure",
    max_concurrency=2,
    call_timeout_s=5,
    prices=ModelPrices(input_usd_per_million=1.0, output_usd_per_million=4.0),
    report_dir=Path("unused"),
)
PROMPT = PromptTemplate("read_figure", 1, "d", "i", "{paper_id}", ("paper_id",))
# read_figure is faked, so the runner is never used
UNUSED_RUNNER: Any = None


def _write_paper(figures_dir: Path, paper_id: str, numbers: list[str]) -> Path:
    paper_dir = figures_dir / paper_id
    paper_dir.mkdir(parents=True)
    records = []
    for position, number in enumerate(numbers, start=1):
        file_name = f"figure_{position:02d}.png"
        (paper_dir / file_name).write_bytes(b"\x89PNG")
        records.append(
            {
                "file": file_name,
                "figure_number": number,
                "caption": f"Fig. {number}",
                "figure_type": "line_chart",
                "pdf_text": "Strength (MPa)",
            }
        )
    (paper_dir / "figures.json").write_text(json.dumps(records))
    return paper_dir


@pytest.fixture
def fake_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini stand-in: figure 2 gets a malformed answer, every other figure a plot reading."""

    async def fake_read(
        _runner: object, _prompt: object, figure: FigureToRead, _png: bytes
    ) -> tuple[FigureReading, TokenUsage]:
        if figure.figure_number == "2":
            raise AgentOutputError("malformed")
        reading = FigureReading(
            figure_type="data_plot",
            summary="s",
            y_axis=AxisReading(label="Strength", unit="MPa", min_value=0, max_value=50),
        )
        return reading, TokenUsage(input_tokens=1000, output_tokens=250)

    monkeypatch.setattr(read_figures, "read_figure", fake_read)


def test_list_paper_dirs_finds_extracted_papers_in_order(tmp_path: Path) -> None:
    for paper_id in ["W2", "W1", "W3"]:
        _write_paper(tmp_path, paper_id, ["1"])
    (tmp_path / "not_a_paper").mkdir()
    assert [path.name for path in read_figures.list_paper_dirs(tmp_path, 2)] == ["W1", "W2"]


@pytest.mark.usefixtures("fake_reader")
def test_run_figure_reading_saves_readings_and_reports_failed_figures(tmp_path: Path) -> None:
    paper_dir = _write_paper(tmp_path, "W1", ["1", "2"])
    results = asyncio.run(
        read_figures.run_figure_reading(
            [paper_dir], runner=UNUSED_RUNNER, prompt=PROMPT, settings=SETTINGS
        )
    )
    saved = json.loads((paper_dir / "readings.json").read_text())
    assert [record["error"] is None for record in saved] == [True, False]
    assert saved[0]["reading"]["figure_type"] == "data_plot"
    assert saved[0]["cost_usd"] == pytest.approx((1000 * 1.0 + 250 * 4.0) / 1_000_000)
    assert total_checks([r.check for r in results if r.check]).labels_unsupported == 0


@pytest.mark.usefixtures("fake_reader")
def test_summarize_costs_averages_per_figure_paper_and_type(tmp_path: Path) -> None:
    paper_dirs = [_write_paper(tmp_path, "W1", ["1", "2"]), _write_paper(tmp_path, "W2", ["1"])]
    results = asyncio.run(
        read_figures.run_figure_reading(
            paper_dirs, runner=UNUSED_RUNNER, prompt=PROMPT, settings=SETTINGS
        )
    )
    summary = read_figures.summarize_costs(results)
    one_figure: Any = pytest.approx(0.002)
    assert (summary.papers, summary.figures_read, summary.figures_failed) == (2, 2, 1)
    assert summary.mean_per_figure_usd == one_figure
    assert summary.mean_per_paper_usd == one_figure
    assert summary.mean_per_type_usd == {"data_plot": one_figure}
    assert summary.total_usd == pytest.approx(0.004)
