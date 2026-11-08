"""Check that background figure reading keeps order, survives failures and respects its limits."""

from pathlib import Path
from typing import Any

import pytest

from steel_slag_slm.config import FigureReadingSettings, ModelPrices
from steel_slag_slm.pipelines import background_figure_reader
from steel_slag_slm.pipelines.background_figure_reader import (
    MAX_FAILURE_STREAK,
    BackgroundFigureReader,
)
from steel_slag_slm.schemas import FigureReading, FigureToRead, TokenUsage

SETTINGS = FigureReadingSettings(
    model="gemini-test",
    temperature=0.0,
    prompt_name="read_figure",
    max_concurrency=2,
    call_timeout_s=5,
    # 1M input tokens cost $1, so each fake call below costs exactly $0.001
    prices=ModelPrices(input_usd_per_million=1.0, output_usd_per_million=0.0),
)


def _figure(number: str) -> FigureToRead:
    return FigureToRead("W1", Path(f"f{number}.png"), number, "caption", "", "line_chart")


async def _fake_read(_runner: Any, _prompt: Any, figure: FigureToRead, png: bytes) -> Any:
    if png == b"bad":
        raise ValueError("unreadable answer")
    reading = FigureReading(figure_type="data_plot", summary=f"Figure {figure.figure_number}")
    return reading, TokenUsage(input_tokens=1000)


@pytest.fixture
def reader_factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(background_figure_reader, "read_figure", _fake_read)
    readers: list[BackgroundFigureReader] = []

    def make(max_run_usd: float | None = None) -> BackgroundFigureReader:
        readers.append(BackgroundFigureReader(Any, Any, SETTINGS, max_run_usd))  # type: ignore[arg-type]
        return readers[-1]

    yield make
    for reader in readers:
        reader.close()


def test_reads_figures_in_order_and_a_failed_figure_does_not_sink_the_paper(
    reader_factory: Any,
) -> None:
    reader = reader_factory()
    figures = [_figure("1"), _figure("2"), _figure("3")]

    result = reader.submit(figures, [b"png", b"bad", b"png"]).result(timeout=5)

    assert [r.summary if r else None for r in result.readings] == ["Figure 1", None, "Figure 3"]
    assert result.cost_usd == pytest.approx(0.002)
    assert reader.spent_usd == pytest.approx(0.002)
    assert reader.stop_reason is None


def test_stops_sending_figures_once_the_budget_is_spent(reader_factory: Any) -> None:
    reader = reader_factory(max_run_usd=0.001)
    reader.submit([_figure("1")], [b"png"]).result(timeout=5)

    assert reader.stop_reason is not None and "budget" in reader.stop_reason
    later = reader.submit([_figure("2")], [b"png"]).result(timeout=5)
    assert later.readings == [None] and later.cost_usd == 0.0


def test_stops_after_too_many_failures_in_a_row(reader_factory: Any) -> None:
    reader = reader_factory()
    figures = [_figure(str(n)) for n in range(MAX_FAILURE_STREAK)]

    reader.submit(figures, [b"bad"] * len(figures)).result(timeout=5)

    assert reader.stop_reason is not None and "in a row" in reader.stop_reason
