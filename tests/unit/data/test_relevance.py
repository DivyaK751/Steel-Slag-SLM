import pytest

from steel_slag_slm.config import BASE_CONFIG_PATH, RelevanceRules
from steel_slag_slm.data.relevance import is_relevant, normalize_for_matching
from steel_slag_slm.schemas import Paper

import yaml


@pytest.fixture(scope="module")
def rules() -> RelevanceRules:
    return RelevanceRules(**yaml.safe_load(BASE_CONFIG_PATH.read_text())["relevance"])


def _paper(title: str, abstract: str = "") -> Paper:
    return Paper("W1", title, abstract, None, None, None, None)


# Titles and Yes/No verdicts from the team's "Feedback on papers" sheet
@pytest.mark.parametrize("title", [
    "An Overview of Utilization of Steel Slag",
    "Chemical, Mineralogical, and Morphological Properties of Steel Slag",
    "Aqueous Carbonation of Steel Slag: A Kinetics Study",
    "Mineral CO2 sequestration by steel slag carbonation",
    "Carbonation and hydration behavior of EAF and BOF steel slag binders",
    "Study on Cementitious Properties and Hydration Characteristics of Steel Slag",
    "Valorisation of stainless steel slags as a hydraulic binder",
])
def test_is_relevant_keeps_papers_the_team_marked_yes(rules: RelevanceRules, title: str) -> None:
    assert is_relevant(_paper(title), rules)


@pytest.mark.parametrize("title", [
    "Soil Modification by the Application of Steel Slag",
    "Properties of a Steel Slag–Permeable Asphalt Mixture and the Reaction of the "
    "Steel Slag–Asphalt Interface",
    "Microwave Absorption Ability of Steel Slag and Road Performance of Asphalt Mixtures "
    "Incorporating Steel Slag",
    "Evaluation of the Volume Stability and Resource Benefit of BOF Slag and Its Asphalt "
    "Mixture Based on Field Application",
    "Leaching and Geochemical Modelling of an Electric Arc Furnace (EAF) and Ladle Slag Heap",
])
def test_is_relevant_skips_papers_the_team_marked_no(rules: RelevanceRules, title: str) -> None:
    assert not is_relevant(_paper(title, abstract="steel slag in cement"), rules)


def test_is_relevant_uses_abstract_when_title_lacks_slag_terms(rules: RelevanceRules) -> None:
    paper = _paper(
        "Development of Cement-Free Binder Systems Based on Metallurgical Waste",
        abstract="BOF slag and EAF slag were hardened by forced carbonation.",
    )
    assert is_relevant(paper, rules)


def test_is_relevant_skips_blast_furnace_slag_but_not_electric_arc_furnace(
    rules: RelevanceRules,
) -> None:
    assert not is_relevant(_paper("Ground granulated blast-furnace slag in concrete"), rules)
    assert is_relevant(_paper("Electric arc furnace slag as a concrete aggregate"), rules)


def test_normalize_for_matching_treats_dashes_as_spaces() -> None:
    assert normalize_for_matching("Steel  Slag–Asphalt") == "steel slag asphalt"


def test_is_relevant_ignores_broad_terms_that_only_appear_in_the_abstract(
    rules: RelevanceRules,
) -> None:
    paper = _paper(
        "Rainfall infiltration simulation on an embankment",
        abstract="Characterization and utilization of steel slag fill.",
    )
    assert not is_relevant(paper, rules)
