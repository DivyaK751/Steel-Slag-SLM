from steel_slag_slm.agents.question_form import ColumnFilter, QuestionForm, form_columns
from steel_slag_slm.agents.router import (
    RouteTools,
    add_paper_search,
    plan_route,
    run_route,
    slag_to_match,
)
from steel_slag_slm.data.slag_catalogue import SlagCatalogueEntry
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.similar_slags import ProfileCheck, SimilarSlag, SimilarSlags, SlagProfile
from steel_slag_slm.rag.slag_lookup import SheetRow, SlagEvidence, SlagLookup
from steel_slag_slm.schemas import Chunk

COLUMNS = form_columns()
STRENGTH = "concrete_performance.compressive_strength_28d_mpa"
CONCRETE = "6 Concrete Performance"
PROFILE = SlagProfile(steel_slag_type="EAF", oxides={"cao_pct": 38.0, "sio2_pct": 16.0})


def _form(**fields) -> QuestionForm:
    return QuestionForm(message_type=fields.pop("message_type", "question"), **fields)


def _plan(form: QuestionForm, stored: SlagProfile | None = PROFILE) -> list[str]:
    return [call.tool for call in plan_route(form, stored, COLUMNS).calls]


def test_chat_and_off_topic_messages_run_no_tools():
    plan = plan_route(_form(message_type="chat"), PROFILE, COLUMNS)
    assert plan.reply_only and plan.calls == ()


def test_a_question_about_my_slag_finds_similar_slags_then_looks_them_up():
    assert _plan(_form(about_my_slag=True)) == ["similar_slags", "lookup"]


def test_a_why_question_about_my_slag_also_searches_the_papers():
    form = _form(about_my_slag=True, wants_explanation=True)
    assert _plan(form) == ["similar_slags", "lookup", "paper_search"]


def test_a_value_question_looks_up_values_with_its_filters():
    assert _plan(_form(properties=[STRENGTH]), None) == ["lookup"]


def test_filters_alone_are_enough_for_a_lookup():
    over_40 = ColumnFilter(column=STRENGTH, op=">", value=40.0)
    assert _plan(_form(filters=[over_40]), None) == ["lookup"]


def test_a_why_question_with_no_columns_only_searches_the_papers():
    assert _plan(_form(wants_explanation=True), None) == ["paper_search"]


BOF = ColumnFilter(column="feedstock_chemistry.steel_slag_type", op="=", value="BOF")


def test_a_why_question_with_only_a_slag_type_filter_skips_the_lookup():
    # Lookup would return every value of every BOF slag
    assert _plan(_form(wants_explanation=True, filters=[BOF]), None) == ["paper_search"]


def test_a_slag_type_filter_with_its_columns_still_looks_values_up():
    form = _form(wants_explanation=True, filters=[BOF], properties=[STRENGTH])
    assert _plan(form, None) == ["lookup", "paper_search"]


def test_a_value_question_with_only_a_slag_type_filter_looks_values_up():
    assert _plan(_form(filters=[BOF]), None) == ["lookup"]


def test_paper_search_is_added_once_with_its_notes():
    plan = add_paper_search(plan_route(_form(properties=[STRENGTH]), None, COLUMNS), ["why"])
    again = add_paper_search(plan, ["and why not"])
    assert [call.tool for call in again.calls] == ["lookup", "paper_search"]
    assert again.notes == ("why", "and why not")


def test_chat_never_gets_a_paper_search():
    plan = plan_route(_form(message_type="chat"), None, COLUMNS)
    assert add_paper_search(plan, ["x"]) == plan


def test_my_slag_without_a_stored_composition_is_noted_and_searched():
    plan = plan_route(_form(about_my_slag=True), None, COLUMNS)
    assert [call.tool for call in plan.calls] == ["paper_search"]
    assert "no composition" in plan.notes[0]


def test_oxides_typed_in_the_question_override_the_stored_profile():
    form = _form(
        chemistry={"cao_pct": 50.0},
        filters=[ColumnFilter(column="feedstock_chemistry.steel_slag_type", op="=", value="BOF")],
    )
    slag = slag_to_match(form, PROFILE)
    assert slag is not None and (slag.steel_slag_type, slag.oxides) == ("BOF", {"cao_pct": 50.0})


def _entry(source: str) -> SlagCatalogueEntry:
    return SlagCatalogueEntry(source, "MAT-0001", None, "EAF", {}, (), 95.0, True, (), None)


def _lookup_of(entries) -> SlagLookup:
    rows = {CONCRETE: [SheetRow(CONCRETE, "W1", "MAT-0001", None, None,
                                {"Compressive_Strength_28d_MPa": "41.3"}, {})]}  # fmt: skip
    slags = [SlagEvidence(f"S{i}", e, None, rows) for i, e in enumerate(entries, start=1)]
    return SlagLookup(slags, [], [])


class Recorder:
    """Fake tools that record what they were asked."""

    def __init__(self, matches: int = 1) -> None:
        self.asked: list[tuple[str, object]] = []
        self.matches = matches

    def tools(self) -> RouteTools:
        def similar(profile):
            self.asked.append(("similar", profile.steel_slag_type))
            found = [SimilarSlag(_entry("W1"), True, 0, (), 0.4, "close")][: self.matches]
            check = ProfileCheck({}, (), 100.0, ())
            return SimilarSlags(check, found, None if found else "No EAF slag to compare")

        def lookup(entries):
            self.asked.append(("lookup", [e.source_id for e in entries]))
            return _lookup_of(entries)

        def search(query, top_k):
            self.asked.append(("search", query))
            return [SearchHit(0.9, Chunk("W9", "text", "Results", "Strength rose.", 0))]

        return RouteTools(similar, lambda: [_entry("W2")], lookup, search)


def test_running_my_slag_looks_up_exactly_the_similar_slags():
    recorder = Recorder()
    form = _form(about_my_slag=True, properties=[STRENGTH])
    results = run_route(
        "q", plan_route(form, PROFILE, COLUMNS), form, recorder.tools(), COLUMNS, 15
    )
    assert recorder.asked == [("similar", "EAF"), ("lookup", ["W1"])]
    assert results.lookup is not None and len(results.lookup.slags) == 1


def test_when_the_tables_have_nothing_the_papers_are_searched_with_the_question():
    recorder = Recorder(matches=0)
    form = _form(about_my_slag=True)
    results = run_route(
        "how reactive?", plan_route(form, PROFILE, COLUMNS), form, recorder.tools(), COLUMNS, 15
    )
    assert recorder.asked[-1] == ("search", "how reactive?")
    assert "No EAF slag to compare" in results.notes
    assert results.passages
