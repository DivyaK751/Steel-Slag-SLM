"""From a user's message to the evidence the answer model reads: read, route, run, package."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache

from google.cloud import bigquery

from steel_slag_slm.agents.evidence_package import build_evidence
from steel_slag_slm.agents.question_form import FormColumn, QuestionForm
from steel_slag_slm.agents.question_reader import read_question
from steel_slag_slm.agents.router import RoutePlan, RouteTools, ToolResults, plan_route, run_route
from steel_slag_slm.config import (
    QuestionRoutingSettings,
    SimilarSlagSettings,
    SlagCatalogueSettings,
    SlagReference,
)
from steel_slag_slm.data.slag_catalogue import SlagCatalogueEntry, read_catalogue
from steel_slag_slm.llm.local_model import JsonModel
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.similar_slags import SimilarSlags, SlagProfile, find_similar_slags
from steel_slag_slm.rag.slag_lookup import SlagLookup, lookup_slags


@dataclass(frozen=True, slots=True)
class PreparedEvidence:
    """Every step's output for one message, so each can be inspected or evaluated."""

    form: QuestionForm
    plan: RoutePlan
    results: ToolResults
    evidence: str  # what the answer model reads


def build_route_tools(
    client: bigquery.Client,
    catalogue_settings: SlagCatalogueSettings,
    similar_settings: SimilarSlagSettings,
    reference: SlagReference,
    paper_search: Callable[[str, int], list[SearchHit]],
) -> RouteTools:
    """The router's tools on the real data: the catalogue and sheets in BigQuery, and the
    paper search passed in. The catalogue is read once, on first use."""
    table_id = f"{client.project}.{catalogue_settings.dataset}.{catalogue_settings.table}"

    @cache
    def catalogue() -> tuple[SlagCatalogueEntry, ...]:
        return tuple(read_catalogue(client, table_id))

    def similar(profile: SlagProfile) -> SimilarSlags:
        return find_similar_slags(
            profile, catalogue(), reference, catalogue_settings, similar_settings
        )

    def lookup(entries: Sequence[SlagCatalogueEntry]) -> SlagLookup:
        return lookup_slags(client, catalogue_settings.dataset, entries)

    return RouteTools(similar, catalogue, lookup, paper_search)


def prepare_evidence(
    question: str,
    model: JsonModel,
    prompt: PromptTemplate,
    columns: list[FormColumn],
    tools: RouteTools,
    settings: QuestionRoutingSettings,
    profile: SlagProfile | None = None,
    previous: QuestionForm | None = None,
) -> PreparedEvidence:
    """Read the message, choose and run the tools, and package their results.

    Raises:
        ValueError: If the reader model's answer is not a valid form.
    """
    form = read_question(question, model, prompt, columns, profile, previous)
    plan = plan_route(form, profile)
    results = run_route(question, plan, form, tools, columns, settings.search_top_k)
    evidence = build_evidence(question, plan, form, results, settings.evidence_max_chars)
    return PreparedEvidence(form, plan, results, evidence)
