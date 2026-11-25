from steel_slag_slm.agents.age_columns import ages_in_question, match_ages
from steel_slag_slm.agents.question_form import QuestionForm, form_columns

COLUMNS = form_columns()
STRENGTH = "concrete_performance.compressive_strength_{}d_mpa"
EXPANSION = "durability.expansion_pct"


def _form(*properties: str) -> QuestionForm:
    return QuestionForm(message_type="question", properties=list(properties))


def test_ages_are_read_in_days_from_days_weeks_and_months():
    text = "strength at 14 days, 28d, 2 weeks and 3 months?"
    assert ages_in_question(text) == [14, 28, 90]  # 2 weeks repeats 14 days
    assert ages_in_question("20% BOF slag at 1 day") == [1]
    assert ages_in_question("3, 7 or 28-day strength") == [3, 7, 28]


def test_an_age_with_its_own_column_uses_it():
    matched = match_ages("7-day strength?", _form(STRENGTH.format(28)), COLUMNS)
    assert matched.form.properties == [STRENGTH.format(7)]
    assert matched.notes == ()


def test_an_age_without_a_column_uses_the_nearest_with_a_note():
    matched = match_ages("14-day strength?", _form(STRENGTH.format(28), EXPANSION), COLUMNS)
    assert matched.form.properties == [EXPANSION, STRENGTH.format(7)]
    assert "No 14-day column" in matched.notes[0] and "7 days" in matched.notes[0]


def test_several_ages_each_get_a_column():
    matched = match_ages("strength at 7 and 28 days?", _form(STRENGTH.format(28)), COLUMNS)
    assert matched.form.properties == [STRENGTH.format(7), STRENGTH.format(28)]


def test_a_form_without_age_columns_or_a_question_without_ages_is_unchanged():
    assert match_ages("expansion after 14 days?", _form(EXPANSION), COLUMNS).notes == ()
    form = _form(STRENGTH.format(28))
    assert match_ages("strength at 20% BOF?", form, COLUMNS).form == form
