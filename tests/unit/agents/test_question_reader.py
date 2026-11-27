from typing import Any

import pytest

from steel_slag_slm.agents.form_examples import FormExample
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
        "message_type": "question", "values_only": True,
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
        message_type="question", values_only=True, properties=[STRENGTH],
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


def test_a_follow_up_is_values_only_only_if_both_questions_are():
    previous = QuestionForm(message_type="question", values_only=False, properties=[STRENGTH])
    follow_up = QuestionForm(message_type="follow_up", values_only=True)
    assert not merge_follow_up(previous, follow_up).values_only


def test_a_composition_read_as_filters_becomes_the_slag_to_match():
    model = FakeModel({
        "message_type": "question",
        "filters": [{"column": "feedstock_chemistry.mgo_pct", "op": "=", "value": 8},
                    {"column": "feedstock_chemistry.cao_pct", "op": ">", "value": 40}],
    })  # fmt: skip
    form = read_question(
        "8% MgO and CaO above 40: expansion risk?", model, load_prompt("read_question"),
        form_columns(),
    )  # fmt: skip
    assert form.chemistry == {"mgo_pct": 8.0}
    assert [(f.column, f.op) for f in form.filters] == [("feedstock_chemistry.cao_pct", ">")]


def test_the_examples_given_are_shown_to_the_model():
    example = FormExample("good morning", QuestionForm(message_type="chat"))
    _, model = _read({"message_type": "chat"}, examples=[example])
    shown = 'Message: good morning\nForm: {"message_type":"chat","values_only":false'
    assert shown in model.calls[0][1]


def test_the_schema_makes_the_model_give_every_field():
    _, model = _read({"message_type": "chat"})
    required = set(model.calls[0][2]["required"])
    assert {"values_only", "about_my_slag", "chemistry"} <= required


def test_chemistry_the_user_did_not_give_is_dropped():
    profile = SlagProfile(steel_slag_type="EAF", oxides={"cao_pct": 38.0})
    form, _ = _read(
        {"message_type": "question",
         "chemistry": {"cao_pct": 38, "mgo_pct": 9, "sio2_pct": 0, "al2o3_pct": 20}},
        profile=profile,
    )  # fmt: skip
    # 38 is the stored profile's, 20 is in the question; 9 is made up and 0 is no value
    assert form.chemistry == {"cao_pct": 38.0, "al2o3_pct": 20.0}


def test_a_slag_type_outside_the_list_is_retried_with_the_allowed_values():
    ld = {"column": SLAG_TYPE, "op": "=", "value": "LD"}
    bof = {"column": SLAG_TYPE, "op": "=", "value": "BOF"}
    form, model = _read_scripted(
        {"message_type": "question", "filters": [ld]},
        {"message_type": "question", "filters": [bof]},
    )
    assert form.filters[0].value == "BOF"
    assert "'LD' is not allowed" in model.users[1]
