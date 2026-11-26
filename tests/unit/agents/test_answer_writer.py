from collections.abc import Iterator
from typing import Any

import pytest

from steel_slag_slm.agents.answer_writer import unknown_citations, write_answer
from steel_slag_slm.config import AnswerSettings
from steel_slag_slm.llm.prompts import PromptTemplate, load_prompt

SETTINGS = AnswerSettings(prompt_name="answer_question", temperature=0.2, max_tokens=900)
PROMPT = PromptTemplate(
    name="answer",
    version=1,
    description="test",
    instruction="Answer from the evidence.",
    input_template="Q: {question}\nE: {evidence}",
    variables=("question", "evidence"),
)
# Shaped like build_evidence's output: slags, rows, a summary, then passages
EVIDENCE = """# Question
What is the strength of BOF slag concrete?

## Similar slags
[S1] W123 MAT-0001 (BOF): Strength of slag concrete (2019). Wang, Li. 10.1/abc
[S2] W456 MAT-0003 (BOF): W456

## Compressive strength
Summary strength_28d: 38-45 MPa, 4 values, 2 studies [W123; W456]
- [S1 EXP-0004 / Mix 20%] strength_28d: 42.5 MPa (p. 6)

## Paper passages
[P1] Strength of slag concrete, Results (W123, text)
Strength rose with age."""


class _FakeModel:
    """Streams fixed pieces and records how it was called."""

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces
        self.calls: list[dict[str, Any]] = []

    def stream_text(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        self.calls.append(
            {"system": system, "user": user, "temperature": temperature, "max_tokens": max_tokens}
        )
        yield from self.pieces


def test_write_answer_sends_the_prompt_and_settings_and_yields_pieces_unchanged() -> None:
    model = _FakeModel(["It was ", "42.5 MPa [S1]."])

    pieces = list(write_answer("  Strength?\n", EVIDENCE + "\n", model, PROMPT, SETTINGS))

    assert pieces == ["It was ", "42.5 MPa [S1]."]
    assert model.calls == [
        {
            "system": "Answer from the evidence.",
            "user": f"Q: Strength?\nE: {EVIDENCE}",
            "temperature": 0.2,
            "max_tokens": 900,
        }
    ]


def test_answer_prompt_loads_with_question_and_evidence() -> None:
    prompt = load_prompt("answer_question")

    assert set(prompt.variables) == {"question", "evidence"}
    assert "<evidence>\nE\n</evidence>" in prompt.render_input(question="Q", evidence="E")


def test_unknown_citations_is_empty_when_every_label_is_in_the_evidence() -> None:
    answer = "42.5 MPa [S1] (Wang et al., 2019), rising with age [P1]; see also [S2]."

    assert unknown_citations(answer, EVIDENCE) == []


@pytest.mark.parametrize(
    ("answer", "unknown"),
    [
        ("Strength was 50 MPa [P9].", ["P9"]),
        ("Grouped [S1, P3] and chained [S4][P1].", ["P3", "S4"]),
        ("Repeated [S7] and again [S7], then [P8].", ["S7", "P8"]),
        ("Copied the row bracket [S5 EXP-0004 / Mix 20%].", ["S5"]),
    ],
)
def test_unknown_citations_lists_made_up_labels_in_order(answer: str, unknown: list[str]) -> None:
    assert unknown_citations(answer, EVIDENCE) == unknown


def test_unknown_citations_ignores_brackets_without_labels() -> None:
    answer = "Values ranged 38-45 MPa [W123; W456] across studies [2 studies]."

    assert unknown_citations(answer, EVIDENCE) == []


def test_unknown_citations_flags_every_label_when_the_evidence_has_none() -> None:
    reply_only = "# Question\nhi\n\nSmall talk: reply briefly. No evidence."

    assert unknown_citations("Hello! [S1]", reply_only) == ["S1"]
    assert unknown_citations("Hello! How can I help?", reply_only) == []
