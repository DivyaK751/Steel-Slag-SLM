import asyncio
from typing import Any

import pytest

from steel_slag_slm.agents import abstract_extractor
from steel_slag_slm.agents.abstract_extractor import judge_abstract, match_share
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import ExtractedAbstract, PaperForAbstract

ABSTRACT = (
    "Steel slag was ground and blended with Portland cement at replacement levels of ten "
    "to thirty percent, and the compressive strength of the mortars was measured."
)
PAGE_TEXT = (
    "Journal of Slag Research\nABSTRACT\nSteel slag was ground and blended with Port-\n"
    "land cement at replacement levels of ten to thirty percent, and the compressive\n"
    "strength of the mortars was measured.\nKeywords: slag\n" + "1. Introduction " * 20
)


def _result(abstract: str | None, found: bool = True) -> ExtractedAbstract:
    return ExtractedAbstract(found=found, abstract=abstract)


def test_match_share_ignores_line_breaks_hyphenation_and_case() -> None:
    assert match_share(ABSTRACT.upper(), PAGE_TEXT) == 1.0


def test_match_share_ignores_line_numbers_in_the_margin() -> None:
    # As PDFs give them: each margin number on a line of its own, even inside "Port-/land"
    lines = PAGE_TEXT.split("\n")
    numbered = "\n".join(f"{line}\n{number} " for number, line in enumerate(lines, 20))
    assert match_share(ABSTRACT, numbered) == 1.0


def test_judge_abstract_stores_a_verified_abstract() -> None:
    outcome = judge_abstract(_result(ABSTRACT), PAGE_TEXT)
    assert outcome.source == "pdf" and outcome.abstract == ABSTRACT


def test_judge_abstract_rejects_text_that_is_not_on_the_pages() -> None:
    invented = "This paper reviews blast furnace slag in asphalt mixtures for road pavements."
    outcome = judge_abstract(_result(invented), PAGE_TEXT)
    assert outcome.source == "pdf_mismatch" and outcome.abstract is None


def test_judge_abstract_flags_scanned_pages_it_cannot_check() -> None:
    outcome = judge_abstract(_result(ABSTRACT), pages_text="  \n ")
    assert outcome.source == "pdf_unverified" and outcome.abstract == ABSTRACT


def test_judge_abstract_flags_a_garbled_text_layer_it_cannot_check() -> None:
    garbled = "\x01\x02\x03\x04 \x05\x06" * 60
    outcome = judge_abstract(_result(ABSTRACT), pages_text=garbled)
    assert outcome.source == "pdf_unverified" and outcome.abstract == ABSTRACT


def test_judge_abstract_marks_papers_without_an_abstract() -> None:
    assert judge_abstract(_result(None, found=False), PAGE_TEXT).source == "not_found"
    assert judge_abstract(_result("   "), PAGE_TEXT).source == "not_found"


def test_extract_abstract_attaches_the_pages_as_a_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    async def fake_run_structured(
        _runner: object, message: str, _schema: object, attachments: Any
    ) -> Any:
        sent["message"], sent["attachments"] = message, attachments
        return _result(ABSTRACT)

    monkeypatch.setattr(abstract_extractor, "run_structured", fake_run_structured)
    prompt = PromptTemplate(
        "p", 1, "d", "instruction", "{openalex_id}|{title}", ("openalex_id", "title")
    )
    paper = PaperForAbstract("W1", "", "file-1")
    asyncio.run(abstract_extractor.extract_abstract(object(), prompt, paper, b"%PDF-1.7"))  # type: ignore[arg-type]
    assert sent["message"] == "W1|(not available)"
    assert sent["attachments"][0].inline_data.mime_type == "application/pdf"
