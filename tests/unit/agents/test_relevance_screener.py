from steel_slag_slm.agents.relevance_screener import check_excerpt, render_paper
from steel_slag_slm.llm.prompts import load_prompt
from steel_slag_slm.schemas import PaperForScreening, RelevanceDecision

ABSTRACT = "BOF slag was used as a binder.  Its free CaO content was measured."


def _paper(abstract: str = ABSTRACT, title: str = "BOF slag binder") -> PaperForScreening:
    return PaperForScreening("W1", title, abstract, 2020, ("slag", "cement"))


def _decision(excerpt: str | None) -> RelevanceDecision:
    return RelevanceDecision(
        relevant="Yes",
        relevance_score=0.9,
        relevance_confidence="High",
        relevance_reason="r",
        supporting_excerpt=excerpt,
    )


def test_check_excerpt_keeps_exact_quote_despite_whitespace_differences() -> None:
    decision = check_excerpt(_decision("Its free CaO content\nwas measured."), _paper())
    assert decision.supporting_excerpt == "Its free CaO content was measured."


def test_check_excerpt_drops_paraphrased_quote() -> None:
    assert check_excerpt(_decision("Free CaO was measured."), _paper()).supporting_excerpt is None


def test_check_excerpt_falls_back_to_title_when_abstract_missing() -> None:
    decision = check_excerpt(_decision("BOF slag binder"), _paper(abstract=""))
    assert decision.supporting_excerpt == "BOF slag binder"


def test_render_paper_marks_missing_abstract() -> None:
    text = render_paper(load_prompt("screen_relevance"), _paper(abstract=""))
    assert "abstract: (not available)" in text
    assert "keywords: slag, cement" in text
