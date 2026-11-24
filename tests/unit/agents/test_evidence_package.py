from steel_slag_slm.agents.evidence_package import NO_EVIDENCE, build_evidence
from steel_slag_slm.agents.question_form import QuestionForm
from steel_slag_slm.agents.router import RoutePlan, ToolCall, ToolResults
from steel_slag_slm.data.slag_catalogue import SlagCatalogueEntry
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.similar_slags import ProfileCheck, SimilarSlag, SimilarSlags
from steel_slag_slm.rag.slag_lookup import SheetRow, SlagEvidence, SlagLookup
from steel_slag_slm.schemas import Chunk

CONCRETE = "6 Concrete Performance"
FORM = QuestionForm(message_type="question", missing_fields=["replacement level"])
PLAN = RoutePlan((ToolCall("lookup"), ToolCall("paper_search")), False, None, ())
ENTRY = SlagCatalogueEntry(
    "W1", "MAT-0001", None, "EAF", {"cao_pct": 38.0}, (), 95.0, True, (), None
)


def _lookup() -> SlagLookup:
    strength = "Compressive_Strength_28d_MPa"
    row = SheetRow(
        CONCRETE, "W1", "MAT-0001", None, None, {strength: "41.3"}, {strength: "Table 4"}
    )
    return SlagLookup([SlagEvidence("S1", ENTRY, None, {CONCRETE: [row]})], [], [])


def _hit(n: int, text: str) -> SearchHit:
    return SearchHit(1.0 / n, Chunk(f"W{n}", "text", "Results", text, 0, paper_title=f"Paper {n}"))


def test_chat_gets_a_reply_instruction_and_no_evidence():
    plan = RoutePlan((), True, None, ())
    text = build_evidence("thanks!", plan, QuestionForm(message_type="chat"), ToolResults(), 9000)
    assert "Small talk" in text and "##" not in text


def test_table_values_come_before_passages_and_every_item_has_its_source():
    results = ToolResults(lookup=_lookup(), passages=[_hit(1, "Strength rose with slag.")])

    text = build_evidence("28-day strength?", PLAN, FORM, results, 9000)

    assert text.index("Compressive_Strength_28d_MPa: 41.3") < text.index("[P1] Paper 1")
    assert "Table 4" in text
    assert "## Slags matching the question" in text  # chosen by filters, not similarity
    assert "Not given in the question: replacement level" in text


def test_similar_slags_say_how_close_they_are():
    match = SimilarSlag(ENTRY, True, 0, ("Wüstite",), 0.59, "same type, close chemistry")
    similar = SimilarSlags(ProfileCheck({}, (), 100.0, ("Cr2O3 above 0.5%",)), [match], None)
    results = ToolResults(similar=similar, lookup=_lookup())

    text = build_evidence("my slag?", PLAN, FORM, results, 9000)

    assert "[S1] same type, close chemistry; oxide distance 0.59" in text
    assert "User's slag: Cr2O3 above 0.5%" in text
    assert "## Similar slags" in text


def test_the_lowest_ranked_passages_are_dropped_to_fit():
    passages = [_hit(n, "x" * 400) for n in range(1, 6)]
    text = build_evidence("q", PLAN, FORM, ToolResults(passages=passages), 1200)
    assert "[P1]" in text and "[P5]" not in text


def test_nothing_found_is_said_plainly():
    assert NO_EVIDENCE in build_evidence("q", PLAN, FORM, ToolResults(), 9000)
