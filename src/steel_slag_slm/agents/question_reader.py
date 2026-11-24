"""Read a user's message into a QuestionForm with a small open model."""

from itertools import groupby

from pydantic import ValidationError

from steel_slag_slm.agents.question_form import (
    FormColumn,
    QuestionForm,
    clean_form,
    form_json_schema,
)
from steel_slag_slm.llm.local_model import JsonModel
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.rag.similar_slags import SlagProfile


def describe_columns(columns: list[FormColumn]) -> str:
    """The column list for the prompt: one line per column, grouped under its sheet."""
    lines = []
    for sheet, sheet_columns in groupby(columns, key=lambda column: column.sheet):
        lines.append(f"[{sheet}]")
        for column in sheet_columns:
            if column.allowed_values:
                detail = f": one of {', '.join(column.allowed_values)}"
            else:
                detail = f" ({column.unit})" if column.unit else " (text)"
            lines.append(f"- {column.name}{detail}")
    return "\n".join(lines)


def describe_profile(profile: SlagProfile | None) -> str:
    """The user's stored slag in one line, or a note that there is none."""
    if profile is None:
        return "none stored"
    oxides = ", ".join(f"{name} {value:g}" for name, value in profile.oxides.items())
    phases = ", ".join(profile.mineral_phases) or "not given"
    return f"{profile.steel_slag_type}; {oxides}; phases: {phases}"


def merge_follow_up(previous: QuestionForm, form: QuestionForm) -> QuestionForm:
    """Fill a follow-up's gaps from the previous form: what it changes wins, the rest carries.

    A filter replaces the previous filter on the same column; other previous filters stay.
    """
    changed = {f.column for f in form.filters}
    return form.model_copy(
        update={
            "message_type": "question",
            "intents": form.intents or previous.intents,
            "properties": form.properties or previous.properties,
            "filters": [f for f in previous.filters if f.column not in changed] + form.filters,
            "chemistry": {**previous.chemistry, **form.chemistry},
            "about_my_slag": form.about_my_slag or previous.about_my_slag,
            "search_query": form.search_query or previous.search_query,
        }
    )


def read_question(
    question: str,
    model: JsonModel,
    prompt: PromptTemplate,
    columns: list[FormColumn],
    profile: SlagProfile | None = None,
    previous: QuestionForm | None = None,
) -> QuestionForm:
    """Ask the model for the message's form, check it, and merge a follow-up with its parent.

    Raises:
        ValueError: If the model's answer is not a valid form.
    """
    user = prompt.render_input(
        columns=describe_columns(columns),
        profile=describe_profile(profile),
        previous_form=previous.model_dump_json() if previous else "none",
        question=question.strip(),
    )
    answer = model.complete_json(prompt.instruction, user, form_json_schema(columns))
    try:
        form = clean_form(QuestionForm.model_validate(answer), columns)
    except ValidationError as error:
        raise ValueError(f"the reader's form is not valid: {error}") from error
    if form.message_type == "follow_up" and previous is not None:
        form = merge_follow_up(previous, form)
    return form
