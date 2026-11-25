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

READ_ATTEMPTS = 2  # the first try and one retry
_PROBLEM_CHARS = 500  # enough of a validation error to show what was wrong


def describe_columns(columns: list[FormColumn]) -> str:
    """The column list for the prompt: one line per column, grouped under its sheet."""
    lines = []
    for sheet, sheet_columns in groupby(columns, key=lambda column: column.sheet):
        lines.append(f"[{sheet}]")
        for column in sheet_columns:
            if column.allowed_values:
                detail = f": one of {', '.join(column.allowed_values)}"
            elif column.seen_values:
                detail = f": values in the data: {', '.join(column.seen_values)}"
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
            "wants_explanation": form.wants_explanation or previous.wants_explanation,
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

    A failed try is retried once: an invalid form with the reason it failed in the prompt,
    so the model does not repeat it; a server error as it was.

    Raises:
        ValueError: If no try gives a valid form.
    """
    schema = form_json_schema(columns)
    problem = "none"
    for _ in range(READ_ATTEMPTS):
        user = prompt.render_input(
            columns=describe_columns(columns),
            profile=describe_profile(profile),
            previous_form=previous.model_dump_json() if previous else "none",
            question=question.strip(),
            problem=problem,
        )
        try:
            answer = model.complete_json(prompt.instruction, user, schema)
            form = clean_form(QuestionForm.model_validate(answer), columns)
        except ValidationError as error:
            problem = str(error)[:_PROBLEM_CHARS]
            continue
        except (OSError, ValueError) as error:  # unreachable server, or not JSON
            problem = f"no usable answer ({error})"[:_PROBLEM_CHARS]
            continue
        if form.message_type == "follow_up" and previous is not None:
            form = merge_follow_up(previous, form)
        return form
    raise ValueError(f"the reader gave no valid form in {READ_ATTEMPTS} tries: {problem}")
