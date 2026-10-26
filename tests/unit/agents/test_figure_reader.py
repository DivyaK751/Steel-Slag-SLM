import asyncio
from pathlib import Path
from typing import Any

import pytest

from steel_slag_slm.agents import figure_reader
from steel_slag_slm.llm.prompts import PromptTemplate, load_prompt
from steel_slag_slm.schemas import FigureReading, FigureToRead, TokenUsage

PROMPT = PromptTemplate(
    "p",
    1,
    "d",
    "instruction",
    "{paper_id}|{figure_label}|{caption}|{pdf_text}",
    ("paper_id", "figure_label", "caption", "pdf_text"),
)


def _figure(number: str | None, caption: str = "", pdf_text: str = "") -> FigureToRead:
    return FigureToRead("W1", Path("figure_01.png"), number, caption, pdf_text, "line_chart")


def test_read_figure_sends_the_image_caption_and_pdf_text(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    async def fake_run(_runner: object, message: str, _schema: object, attachments: Any) -> Any:
        sent["message"], sent["attachments"] = message, attachments
        return FigureReading(figure_type="data_plot", summary="s"), TokenUsage(10, 5, 0)

    monkeypatch.setattr(figure_reader, "run_structured_with_usage", fake_run)
    figure = _figure("3", caption="Fig. 3 Strength", pdf_text="0 10 20 MPa")
    reading, usage = asyncio.run(
        figure_reader.read_figure(object(), PROMPT, figure, b"\x89PNG")  # type: ignore[arg-type]
    )
    assert sent["message"] == "W1|Fig. 3|Fig. 3 Strength|0 10 20 MPa"
    assert sent["attachments"][0].inline_data.mime_type == "image/png"
    assert reading.figure_type == "data_plot" and usage == TokenUsage(10, 5, 0)


def test_figure_label_names_unnumbered_figures() -> None:
    assert figure_reader.figure_label(_figure(None)) == "unnumbered figure"


def test_read_figure_prompt_loads() -> None:
    prompt = load_prompt("read_figure")
    assert set(prompt.variables) == {"paper_id", "figure_label", "caption", "pdf_text"}
    assert "<pdf_text>" in prompt.input_template
