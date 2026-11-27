"""Write the answer to one chat message from its evidence, and check the labels it cites.

The evidence labels table slags [S1], [S2] ... and paper passages [P1], [P2] ...
(agents/evidence_package.py); the answer cites those labels.
"""

import re
from collections.abc import Iterator
from typing import Protocol

from steel_slag_slm.config import AnswerSettings
from steel_slag_slm.llm.gateway import ReplyPiece
from steel_slag_slm.llm.prompts import PromptTemplate

# A label where the evidence defines it: first thing in a bracket, e.g. "[S1 EXP-0004 / Mix 20%]"
_DEFINED_LABEL = re.compile(r"\[([SP]\d+)\b")
# Any bracket in the answer; a label inside it may share it with others, e.g. "[S1, P2]"
_BRACKET = re.compile(r"\[([^\[\]]*)\]")
_LABEL = re.compile(r"\b[SP]\d+\b")


class TextModel(Protocol):
    """A model that streams a text answer (GatewayClient, or a fake in tests)."""

    def stream_text(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[ReplyPiece]: ...


def write_answer(
    question: str,
    evidence: str,
    model: TextModel,
    prompt: PromptTemplate,
    settings: AnswerSettings,
) -> Iterator[ReplyPiece]:
    """Stream the answer to the question, built only from the evidence, piece by piece.

    A thinking model's reasoning comes first, as thinking pieces; only the rest is the answer.
    """
    yield from model.stream_text(
        prompt.instruction,
        prompt.render_input(question=question.strip(), evidence=evidence.strip()),
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
    )


def defined_labels(evidence: str) -> set[str]:
    """The S#/P# labels the evidence defines."""
    return set(_DEFINED_LABEL.findall(evidence))


def cited_labels(answer: str) -> list[str]:
    """The S#/P# labels the answer cites, in order of first use, each once.

    Brackets without an S#/P# label, such as "[source_a; source_b]", are ignored.
    """
    cited = (label for group in _BRACKET.findall(answer) for label in _LABEL.findall(group))
    return list(dict.fromkeys(cited))  # drops repeats and keeps first-use order


def unknown_citations(answer: str, evidence: str) -> list[str]:
    """Labels the answer cites that the evidence never defines, in order of first use.

    A cheap check for made-up citations.
    """
    defined = defined_labels(evidence)
    return [label for label in cited_labels(answer) if label not in defined]
