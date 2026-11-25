"""Match the ages a question names ("14-day strength") to the columns that hold an age.

Some properties have one column per test age (compressive_strength_28d_mpa). An age with no
column of its own is mapped to the nearest one, with a note, and the papers are searched too,
since they may report that exact age.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from steel_slag_slm.agents.question_form import FormColumn, QuestionForm

_AGE_IN_COLUMN = re.compile(r"_(\d+)d_")  # e.g. compressive_strength_28d_mpa -> 28
# One unit may follow several numbers: "7 and 28 days", "3, 7 or 28-day"
_AGE_IN_TEXT = re.compile(
    r"\b(\d+(?:\s*(?:,|and|or|&)\s*\d+)*)\s*-?\s*(days?|d|weeks?|wks?|months?)\b", re.IGNORECASE
)
_DAYS_PER_UNIT = {"d": 1, "w": 7, "m": 30}  # by the unit's first letter


@dataclass(frozen=True, slots=True)
class AgeMatch:
    """The form with its age columns set to the ages asked for, and notes on any mapping."""

    form: QuestionForm
    notes: tuple[str, ...]  # one per age that had no column; empty when all matched


def ages_in_question(question: str) -> list[int]:
    """Ages the question names, in days, in the order they appear."""
    ages = [
        int(number) * _DAYS_PER_UNIT[unit[0].lower()]
        for numbers, unit in _AGE_IN_TEXT.findall(question)
        for number in re.findall(r"\d+", numbers)
    ]
    return list(dict.fromkeys(ages))


def _family(name: str) -> str | None:
    """The column's name with its age blanked out, or None if it holds no age."""
    return _AGE_IN_COLUMN.sub("_{age}d_", name, count=1) if _AGE_IN_COLUMN.search(name) else None


def _age_of(name: str) -> int:
    match = _AGE_IN_COLUMN.search(name)
    return int(match.group(1)) if match else 0


def match_ages(question: str, form: QuestionForm, columns: Sequence[FormColumn]) -> AgeMatch:
    """Replace the form's age columns with the ages the question asks for.

    An asked age with a column of its own uses it. Otherwise the nearest age's column is used
    (the lower one on a tie) and a note says so. Forms whose properties hold no age column,
    and questions that name no age, are returned unchanged.
    """
    asked = ages_in_question(question)
    families = {_family(p) for p in form.properties} - {None}
    if not asked or not families:
        return AgeMatch(form, ())
    by_family: dict[str, list[str]] = {}
    for column in columns:
        family = _family(column.name)
        if family is not None and family in families:
            by_family.setdefault(family, []).append(column.name)
    properties = [p for p in form.properties if _family(p) is None]
    notes: list[str] = []
    for names in by_family.values():
        for age in asked:
            nearest = min(names, key=lambda name: (abs(_age_of(name) - age), _age_of(name)))
            properties.append(nearest)
            if _age_of(nearest) != age:
                notes.append(
                    f"No {age}-day column; table values are for {_age_of(nearest)} days "
                    f"({nearest}), the nearest age. The papers may report {age} days."
                )
    form = form.model_copy(update={"properties": list(dict.fromkeys(properties))})
    return AgeMatch(form, tuple(dict.fromkeys(notes)))
