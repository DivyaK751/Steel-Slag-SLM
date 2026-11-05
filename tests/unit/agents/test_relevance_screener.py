import asyncio

from steel_slag_slm.agents.relevance_screener import (
    NON_ENGLISH_PREFIX,
    check_excerpt,
    render_paper,
    screen_paper,
)
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


def test_paper_from_row_accepts_keyword_list_or_comma_string() -> None:
    from steel_slag_slm.agents.relevance_screener import paper_from_row

    base = {"openalex_id": "W1", "title": None, "abstract": None, "year": None}
    assert paper_from_row({**base, "keywords": ["a", "b"]}).keywords == ("a", "b")
    assert paper_from_row({**base, "keywords": "a, b,"}).keywords == ("a", "b")
    assert paper_from_row({**base, "keywords": None}).title == ""


def test_screen_paper_answers_no_for_non_english_paper_without_calling_gemini() -> None:
    korean = "전기로 산화슬래그를 콘크리트용 잔골재로 사용하기 위해서는 에이징처리하여야 한다"
    # A runner of None would fail if the agent were called
    decision, usage = asyncio.run(
        screen_paper(None, load_prompt("screen_relevance"), _paper(abstract=korean))  # type: ignore[arg-type]
    )
    assert (decision.relevant, decision.relevance_score, decision.relevance_confidence) == (
        "No",
        0.0,
        "High",
    )
    assert decision.relevance_reason.startswith(NON_ENGLISH_PREFIX)
    assert (usage.input_tokens, usage.output_tokens) == (0, 0)
