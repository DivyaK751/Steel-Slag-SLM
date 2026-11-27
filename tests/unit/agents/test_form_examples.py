from collections.abc import Sequence

import numpy as np

from steel_slag_slm.agents.form_examples import (
    ExamplePicker,
    FormExample,
    load_examples,
    render_examples,
)
from steel_slag_slm.agents.question_form import QuestionForm, clean_form, form_columns


def test_every_bank_example_names_only_real_columns_and_oxides():
    columns = form_columns()
    for example in load_examples("read_question_examples"):
        assert clean_form(example.form, columns) == example.form, example.message


def test_the_bank_covers_every_message_type():
    types = {example.form.message_type for example in load_examples("read_question_examples")}
    assert types == {"question", "follow_up", "chat", "off_topic"}


class KeywordEncoder:
    """Maps a text to a one-hot vector of the first keyword it contains."""

    KEYWORDS = ("strength", "hello", "carbon")

    @property
    def dimension(self) -> int:
        return len(self.KEYWORDS)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return np.array(
            [[float(word in text) for word in self.KEYWORDS] for text in texts], dtype=np.float32
        )


EXAMPLES = [
    FormExample("hello there", QuestionForm(message_type="chat")),
    FormExample("strength of slag", QuestionForm(message_type="question")),
    FormExample("carbon uptake", QuestionForm(message_type="question")),
]


def test_the_closest_examples_come_first():
    picker = ExamplePicker(EXAMPLES, k=1, encoder=KeywordEncoder())
    assert picker.pick("what carbon uptake?")[0].message == "carbon uptake"


def test_without_an_encoder_the_first_examples_are_used():
    assert ExamplePicker(EXAMPLES, k=2).pick("anything") == EXAMPLES[:2]


def test_no_examples_are_said_plainly():
    assert render_examples([]) == "none"
