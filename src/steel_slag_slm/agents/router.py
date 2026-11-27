"""Turn a question form into tool calls with fixed rules, then run them.

The model only reads the question; code decides which tools run and with what input, so the
same form always gives the same calls and every routing decision can be tested.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, get_args

from steel_slag_slm.agents.question_form import FormColumn, QuestionForm
from steel_slag_slm.agents.table_filters import filter_lookup, is_slag_level, select_slags
from steel_slag_slm.data.slag_catalogue import SlagCatalogueEntry
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.similar_slags import SimilarSlags, SlagProfile
from steel_slag_slm.rag.slag_lookup import SlagLookup
from steel_slag_slm.schemas import SteelSlagType

ToolName = Literal["similar_slags", "lookup", "paper_search"]
_SLAG_TYPE_COLUMN = "steel_slag_type"
_UNKNOWN_TYPE = "Other Steel Slag"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool to run. lookup reads the similar slags when after_similar is set, otherwise
    the catalogue slags chosen by the form's filters."""

    tool: ToolName
    after_similar: bool = False


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """The tools to run for one message, in order, and what the answer should be told."""

    calls: tuple[ToolCall, ...]
    reply_only: bool  # chat or off-topic: the answer model replies without evidence
    profile: SlagProfile | None  # the slag to match: typed oxides, else the stored profile
    notes: tuple[str, ...]  # e.g. a missing slag composition


@dataclass(frozen=True, slots=True)
class RouteTools:
    """The tools the router calls, passed in so routing can be tested without data stores."""

    similar_slags: Callable[[SlagProfile], SimilarSlags]
    catalogue: Callable[[], Sequence[SlagCatalogueEntry]]
    lookup: Callable[[Sequence[SlagCatalogueEntry]], SlagLookup]
    paper_search: Callable[[str, int], list[SearchHit]]


@dataclass(slots=True)
class ToolResults:
    """Everything the tools returned for one message."""

    similar: SimilarSlags | None = None
    lookup: SlagLookup | None = None
    passages: list[SearchHit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _slag_type_filter(form: QuestionForm) -> str | None:
    for condition in form.filters:
        if condition.column.endswith(_SLAG_TYPE_COLUMN) and condition.value in get_args(
            SteelSlagType
        ):
            return str(condition.value)
    return None


def slag_to_match(form: QuestionForm, stored: SlagProfile | None) -> SlagProfile | None:
    """The slag the question is about: oxides typed in the question override the stored
    profile for this message; otherwise the stored profile, if the question is about it."""
    if form.chemistry:
        slag_type = _slag_type_filter(form) or (stored.steel_slag_type if stored else None)
        return SlagProfile.model_validate(
            {"steel_slag_type": slag_type or _UNKNOWN_TYPE, "oxides": form.chemistry}
        )
    if stored is not None and form.about_my_slag:
        return stored
    return None


def _needs_lookup(form: QuestionForm, columns: Sequence[FormColumn]) -> bool:
    """Lookup runs for asked-for columns or a filter on results. A question that is not
    values-only and whose only filters are slag type or chemistry goes to the papers alone:
    with no columns to keep, lookup would return every value of the chosen slags.

    A question that is not values-only and has no filters ("how can iron be recovered from
    slag?") also goes to the papers alone: nothing narrows the lookup, so it would read every
    catalogue slag and hand the answer model unrelated rows."""
    by_name = {column.name: column for column in columns}
    known_filters = [f for f in form.filters if f.column in by_name]
    if not form.values_only and not known_filters:
        return False
    if form.properties:
        return True
    slag_level = [is_slag_level(by_name[f.column]) for f in known_filters]
    if not slag_level:
        return False
    return not all(slag_level) or form.values_only


def plan_route(
    form: QuestionForm, stored: SlagProfile | None, columns: Sequence[FormColumn]
) -> RoutePlan:
    """Decide which tools run for this form.

    - chat or off-topic: no tools.
    - about the user's slag, or a composition given: similar slags, then lookup on them.
    - names columns or result filters: lookup on the catalogue slags the filters choose;
      a reasoning question with no filters skips it (nothing would narrow the lookup).
    - not values-only, or nothing above applies: paper search too.
    """
    if form.message_type in ("chat", "off_topic"):
        return RoutePlan((), True, None, ())
    profile = slag_to_match(form, stored)
    calls: list[ToolCall] = []
    notes: list[str] = []
    if form.about_my_slag and profile is None:
        notes.append("The question is about the user's slag, but no composition is stored")
    if profile is not None:
        calls += [ToolCall("similar_slags"), ToolCall("lookup", after_similar=True)]
    elif _needs_lookup(form, columns):
        calls.append(ToolCall("lookup"))
    if not form.values_only or not calls:
        calls.append(ToolCall("paper_search"))
    return RoutePlan(tuple(calls), False, profile, tuple(notes))


def add_paper_search(plan: RoutePlan, notes: Sequence[str]) -> RoutePlan:
    """Search the papers too (once), with notes saying why; chat and off-topic stay as is."""
    if plan.reply_only:
        return plan
    calls = plan.calls
    if all(call.tool != "paper_search" for call in calls):
        calls += (ToolCall("paper_search"),)
    return replace(plan, calls=calls, notes=plan.notes + tuple(notes))


def run_route(
    question: str,
    plan: RoutePlan,
    form: QuestionForm,
    tools: RouteTools,
    columns: Sequence[FormColumn],
    search_top_k: int,
) -> ToolResults:
    """Run the planned tools; when the tables give nothing, search the papers instead.

    Papers are searched with the form's stand-alone query, or the question itself if empty.
    """
    query = form.search_query.strip() or question
    results = ToolResults(notes=list(plan.notes))
    for call in plan.calls:
        if call.tool == "similar_slags" and plan.profile is not None:
            results.similar = tools.similar_slags(plan.profile)
            if results.similar.reason_if_empty:
                results.notes.append(results.similar.reason_if_empty)
        elif call.tool == "lookup":
            results.lookup = _lookup(call, form, tools, columns, results.similar)
        elif call.tool == "paper_search":
            results.passages = tools.paper_search(query, search_top_k)
    tables_empty = results.lookup is None or not results.lookup.slags
    if tables_empty and not results.passages and not plan.reply_only:
        if results.lookup is not None or results.similar is not None:
            results.notes.append("The extraction tables have no values for this question")
        results.passages = tools.paper_search(query, search_top_k)
    return results


def _lookup(
    call: ToolCall,
    form: QuestionForm,
    tools: RouteTools,
    columns: Sequence[FormColumn],
    similar: SimilarSlags | None,
) -> SlagLookup:
    if call.after_similar:
        slags = [match.entry for match in similar.matches] if similar else []
    else:
        slags = select_slags(tools.catalogue(), form.filters, columns)
    found = tools.lookup(slags)
    return filter_lookup(found, form.filters, form.properties, columns)
