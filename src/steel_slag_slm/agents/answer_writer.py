"""Write the answer to one chat message from its evidence, and check the labels it cites.

The evidence labels table slags [S1], [S2] ... and paper passages [P1], [P2] ...
(agents/evidence_package.py on feature/question-routing); the answer cites those labels.
"""

import re
from collections.abc import Iterator
from typing import Protocol

from steel_slag_slm.config import AnswerSettings
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
    ) -> Iterator[str]: ...


def write_answer(
    question: str,
    evidence: str,
    model: TextModel,
    prompt: PromptTemplate,
    settings: AnswerSettings,
) -> Iterator[str]:
    """Stream the answer to the question, built only from the evidence, piece by piece."""
    yield from model.stream_text(
        prompt.instruction,
        prompt.render_input(question=question.strip(), evidence=evidence.strip()),
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
    )


def unknown_citations(answer: str, evidence: str) -> list[str]:
    """Labels the answer cites that the evidence never defines, in order of first use.

    A cheap check for made-up citations: brackets without an S#/P# label, such as the
    evidence's "[source_a; source_b]" summaries, are ignored.
    """
    defined = set(_DEFINED_LABEL.findall(evidence))
    cited = (label for group in _BRACKET.findall(answer) for label in _LABEL.findall(group))
    # dict.fromkeys drops repeats and keeps first-use order
    return [label for label in dict.fromkeys(cited) if label not in defined]
