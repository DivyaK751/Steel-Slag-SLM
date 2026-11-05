from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from steel_slag_slm.schemas import (
    CaptionBlock,
    Figure,
    RelevanceDecision,
    RenderedFigure,
)


def _decision(score: float, confidence: str = "High") -> RelevanceDecision:
    return RelevanceDecision.model_validate(
        {
            "relevant": "Yes",
            "relevance_score": score,
            "relevance_confidence": confidence,
            "relevance_reason": "r",
        }
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.85, "High"),
        (0.84, "Medium"),
        (0.70, "Medium"),
        (0.69, "Low"),
        (0.50, "Low"),
        (0.30, "Low"),
        (0.29, "Medium"),
        (0.16, "Medium"),
        (0.15, "High"),
        (0.0, "High"),
    ],
)
def test_confidence_is_derived_from_score_not_model_choice(score: float, expected: str) -> None:
    assert _decision(score, confidence="High").relevance_confidence == expected


def test_score_outside_zero_to_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _decision(1.2)


def test_unknown_decision_label_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RelevanceDecision.model_validate(
            {
                "relevant": "Maybe",
                "relevance_score": 0.5,
                "relevance_confidence": "Low",
                "relevance_reason": "r",
            }
        )


def test_rendered_figure_retains_source_metadata_and_image_bytes() -> None:
    caption = CaptionBlock(
        page_number=2,
        bbox=(50.0, 310.0, 400.0, 330.0),
        text="Fig. 1 Strength",
        figure_number="1",
    )
    figure = Figure(
        page_number=2,
        bbox=(50.0, 100.0, 400.0, 300.0),
        figure_type="line_chart",
        type_confidence=0.9,
        caption=caption.text,
        figure_number=caption.figure_number,
        caption_block=caption,
    )

    rendered = RenderedFigure(
        figure=figure,
        file_name="figure_01.png",
        width=1000,
        height=600,
        render_dpi=200,
        pdf_text="Age Strength",
        image_bytes=b"png-bytes",
    )

    assert rendered.figure is figure
    assert rendered.figure.caption_block is caption
    assert rendered.file_name == "figure_01.png"
    assert (rendered.width, rendered.height) == (1000, 600)
    assert rendered.render_dpi == 200
    assert rendered.pdf_text == "Age Strength"
    assert rendered.image_bytes == b"png-bytes"


def test_rendered_figure_is_immutable() -> None:
    rendered = RenderedFigure(
        figure=Figure(1, (0.0, 0.0, 100.0, 100.0), "", 0.0),
        file_name="figure_01.png",
        width=100,
        height=100,
        render_dpi=72,
        pdf_text="",
        image_bytes=b"png-bytes",
    )

    with pytest.raises(FrozenInstanceError):
        rendered.width = 200  # type: ignore[misc]  # Exercise the frozen-field guard.
