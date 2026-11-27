"""Worked question-to-form examples, the ones most like each message chosen for its prompt.

A fixed handful of examples teaches the reader only the cases they show. A bank of examples
covering every kind of form, of which the few closest to the message are shown, teaches the
case at hand without a longer prompt.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from steel_slag_slm.agents.question_form import QuestionForm
from steel_slag_slm.llm.prompts import PROMPTS_DIR
from steel_slag_slm.rag.embeddings import TextEncoder


@dataclass(frozen=True, slots=True)
class FormExample:
    """One message and the form it should be read into."""

    message: str
    form: QuestionForm


def load_examples(name: str, prompts_dir: Path = PROMPTS_DIR) -> list[FormExample]:
    """Read prompts/<name>.yaml; every form is checked against QuestionForm.

    Raises:
        pydantic.ValidationError: If an example's form is not a valid QuestionForm.
    """
    raw = yaml.safe_load((prompts_dir / f"{name}.yaml").read_text(encoding="utf-8"))
    return [
        FormExample(item["message"], QuestionForm.model_validate(item["form"]))
        for item in raw["examples"]
    ]


def render_examples(examples: Sequence[FormExample]) -> str:
    """The examples as prompt text, one message and its form each."""
    if not examples:
        return "none"
    # Every field is shown, even empty ones: a model shown short forms leaves fields out, and
    # a left-out values_only or about_my_slag silently becomes false
    return "\n\n".join(
        f"Message: {example.message}\nForm: {example.form.model_dump_json()}"
        for example in examples
    )


class ExamplePicker:
    """Chooses the k examples whose messages are closest in meaning to a new message.

    Without an encoder, the first k examples of the bank are used, so the bank should start
    with its most general examples.
    """

    def __init__(
        self, examples: Sequence[FormExample], k: int, encoder: TextEncoder | None = None
    ) -> None:
        self._examples = list(examples)
        self._k = k
        self._encoder = encoder
        self._vectors: np.ndarray | None = None  # encoded on first use

    def pick(self, message: str) -> list[FormExample]:
        """The k closest examples, closest first."""
        if self._encoder is None or len(self._examples) <= self._k:
            return self._examples[: self._k]
        if self._vectors is None:
            self._vectors = self._encoder.encode([e.message for e in self._examples])
        query = self._encoder.encode([message])[0]
        order = np.argsort(-(self._vectors @ query), kind="stable")[: self._k]
        return [self._examples[i] for i in order]
