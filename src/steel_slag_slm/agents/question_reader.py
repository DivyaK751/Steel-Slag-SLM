"""Read a user's message into a QuestionForm with a small open model."""

import re
from collections.abc import Sequence
from itertools import groupby

from pydantic import ValidationError

from steel_slag_slm.agents.form_examples import FormExample, render_examples
from steel_slag_slm.agents.question_form import (
    FormColumn,
    QuestionForm,
    clean_form,
    form_json_schema,
    move_stated_oxides,
    off_vocabulary,
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


_NUMBER = re.compile(r"\d+(?:\.\d+)?")  # numbers only, e.g. "8" in "8% MgO" or "4.5"


def keep_stated_chemistry(
    form: QuestionForm, question: str, profile: SlagProfile | None
) -> QuestionForm:
    """Keep only oxide values the user really gave: a number in the message, or the stored
    profile's own value. A small model sometimes fills chemistry with zeros or made-up numbers,
    which would send an unrelated slag to similar_slags."""
    stated = {float(number) for number in _NUMBER.findall(question)}
    stored = profile.oxides if profile else {}
    chemistry = {
        oxide: value
        for oxide, value in form.chemistry.items()
        if value > 0 and (value in stated or stored.get(oxide) == value)
    }
    if chemistry == form.chemistry:
        return form
    return form.model_copy(update={"chemistry": chemistry})


def merge_follow_up(previous: QuestionForm, form: QuestionForm) -> QuestionForm:
    """Fill a follow-up's gaps from the previous form: what it changes wins, the rest carries.

    A filter replaces the previous filter on the same column; other previous filters stay.
    """
    changed = {f.column for f in form.filters}
    return form.model_copy(
        update={
            "message_type": "question",
            # values-only only if both are: a follow-up to a reasoning question still reasons
            "values_only": form.values_only and previous.values_only,
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
    examples: Sequence[FormExample] = (),
) -> QuestionForm:
    """Ask the model for the message's form, check it, and merge a follow-up with its parent.

    examples are worked messages and forms shown with this message, e.g. the ones most like it.

    A failed try is retried once: an invalid form, or a filter value outside its column's list,
    with the reason in the prompt, so the model does not repeat it; a server error as it was.
    On the last try a value outside the list is kept as a "contains" filter.

    Raises:
        ValueError: If no try gives a valid form.
    """
    schema = form_json_schema(columns)
    problem = "none"
    for attempt in range(1, READ_ATTEMPTS + 1):
        user = prompt.render_input(
            columns=describe_columns(columns),
            examples=render_examples(examples),
            profile=describe_profile(profile),
            previous_form=previous.model_dump_json() if previous else "none",
            question=question.strip(),
            problem=problem,
        )
        try:
            answer = model.complete_json(prompt.instruction, user, schema)
            read = QuestionForm.model_validate(answer)
            # A value outside a column's list ("LD" for slag type) gets one retry naming the
            # allowed values; checked here, not in the schema, which Ollama compiles too slowly
            wrong = off_vocabulary(read, columns)
            if wrong and attempt < READ_ATTEMPTS:
                problem = "; ".join(wrong)[:_PROBLEM_CHARS]
                continue
            form = clean_form(read, columns)
            form = keep_stated_chemistry(move_stated_oxides(form, columns), question, profile)
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
