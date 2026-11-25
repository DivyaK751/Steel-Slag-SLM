from typing import Any

import pytest

from steel_slag_slm.agents.question_form import (
    ColumnFilter,
    QuestionForm,
    add_seen_values,
    form_columns,
)
from steel_slag_slm.agents.question_reader import (
    describe_columns,
    describe_profile,
    merge_follow_up,
    read_question,
)
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.rag.similar_slags import SlagProfile

STRENGTH = "concrete_performance.compressive_strength_28d_mpa"
SLAG_TYPE = "concrete_performance.steel_slag_type"
REPLACEMENT = "concrete_performance.scm_replacement_pct"


class FakeModel:
    """Returns a fixed answer and records what it was asked."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user, schema))
        return self.answer


def _read(answer: dict[str, Any], **kwargs: Any) -> tuple[QuestionForm, FakeModel]:
    model = FakeModel(answer)
    form = read_question(
        "What 28-day strength at 20% BOF?", model, load_prompt("read_question"),
        form_columns(), **kwargs,
    )  # fmt: skip
    return form, model


def test_the_model_gets_every_column_the_profile_and_a_schema_of_real_names():
    profile = SlagProfile(steel_slag_type="EAF", oxides={"cao_pct": 38}, mineral_phases=["C2S"])
    _, model = _read({"message_type": "chat"}, profile=profile)
    _, user, schema = model.calls[0]
    assert STRENGTH in user and "EAF; cao_pct 38; phases: C2S" in user
    assert STRENGTH in schema["properties"]["properties"]["items"]["enum"]


def test_a_valid_answer_becomes_a_clean_form():
    form, _ = _read({
        "message_type": "question", "wants_explanation": False,
        "properties": [STRENGTH, "invented.column"],
        "filters": [{"column": SLAG_TYPE, "op": "=", "value": "BOF"}],
    })  # fmt: skip
    assert form.properties == [STRENGTH]
    assert form.filters[0].value == "BOF"


class ScriptedModel:
    """Gives each answer in turn; an exception in the script is raised instead."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.users: list[str] = []

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.users.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _read_scripted(*answers: Any) -> tuple[QuestionForm, ScriptedModel]:
    model = ScriptedModel(*answers)
    form = read_question("why?", model, load_prompt("read_question"), form_columns())
    return form, model


def test_an_invalid_answer_is_retried_with_the_reason_it_failed():
    form, model = _read_scripted({"message_type": "a poem"}, {"message_type": "chat"})
    assert form.message_type == "chat"
    assert "PROBLEM WITH YOUR LAST FORM (fix it this time): none" in model.users[0]
    assert "message_type" in model.users[1].split("PROBLEM WITH YOUR LAST FORM")[1]


def test_a_server_error_is_retried():
    form, _ = _read_scripted(TimeoutError("slow"), {"message_type": "chat"})
    assert form.message_type == "chat"


def test_two_failed_tries_are_reported():
    with pytest.raises(ValueError, match="no valid form in 2 tries"):
        _read_scripted({"message_type": "a poem"}, {"message_type": "a song"})


def test_a_follow_up_keeps_the_previous_question_and_changes_only_what_it_names():
    previous = QuestionForm(
        message_type="question", wants_explanation=True, properties=[STRENGTH],
        filters=[ColumnFilter(column=SLAG_TYPE, op="=", value="BOF"),
                 ColumnFilter(column=REPLACEMENT, op="=", value=20.0)],
        search_query="28-day strength of concrete with 20% BOF slag",
    )  # fmt: skip
    follow_up = QuestionForm(
        message_type="follow_up", filters=[ColumnFilter(column=SLAG_TYPE, op="=", value="EAF")]
    )

    merged = merge_follow_up(previous, follow_up)

    assert merged.message_type == "question"
    assert merged.properties == [STRENGTH]
    assert {(f.column, f.value) for f in merged.filters} == {
        (REPLACEMENT, 20.0),
        (SLAG_TYPE, "EAF"),
    }
    assert merged.search_query == previous.search_query


def test_columns_are_listed_under_their_sheet_with_units_or_allowed_values():
    text = describe_columns(form_columns())
    assert "[6 Concrete Performance]" in text
    assert f"- {STRENGTH} (MPa)" in text
    assert "- feedstock_chemistry.steel_slag_type: one of EAF, BOF" in text
    assert "- concrete_performance.cement_type (text)" in text


def test_the_values_seen_in_the_data_are_listed():
    seen = {("6 Concrete Performance", "Cement_Type"): ("CEM I 42.5", "OPC")}
    text = describe_columns(add_seen_values(form_columns(), seen))
    assert "- concrete_performance.cement_type: values in the data: CEM I 42.5, OPC" in text


def test_no_profile_is_said_plainly():
    assert describe_profile(None) == "none stored"
