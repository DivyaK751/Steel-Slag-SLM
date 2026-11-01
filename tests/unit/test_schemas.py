import pytest
from pydantic import ValidationError

from steel_slag_slm.schemas import RelevanceDecision


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
