from collections.abc import Sequence

from steel_slag_slm.agents.answer_citations import build_citations
from steel_slag_slm.agents.router import ToolResults
from steel_slag_slm.data.slag_catalogue import SlagCatalogueEntry
from steel_slag_slm.rag.search import SearchHit
from steel_slag_slm.rag.slag_lookup import Citation as LookupCitation
from steel_slag_slm.rag.slag_lookup import SlagEvidence, SlagLookup
from steel_slag_slm.schemas import Chunk, Citation, PaperCitation

ENTRY = SlagCatalogueEntry(
    "W1", "MAT-0001", None, "BOF", {"cao_pct": 43.4}, (), 95.0, True, (), None
)
SLAG_PAPER = LookupCitation(
    "W1", "BOF slag in carbonate cements", "L. Stefanini, J. Provis", "2024", "10.1/x"
)
PASSAGE = Chunk(
    "W9", "text", "Results", "Free lime expands.", 3, pages=(4, 5), paper_title="Fallback"
)
RESULTS = ToolResults(
    lookup=SlagLookup([SlagEvidence("S1", ENTRY, SLAG_PAPER, {})], [], []),
    passages=[SearchHit(0.9, PASSAGE)],
)
EVIDENCE = "[S1] W1 MAT-0001 (BOF): ...\n[P1] Fallback, Results (W9, text)\nFree lime expands."


def _registry(found: dict[str, PaperCitation]):
    asked: list[Sequence[str]] = []

    def paper_citations(paper_ids: Sequence[str]) -> dict[str, PaperCitation]:
        asked.append(paper_ids)
        return {paper_id: found[paper_id] for paper_id in paper_ids if paper_id in found}

    return paper_citations, asked


def test_cited_slags_and_passages_get_citations_in_order_of_first_use():
    registry, asked = _registry({"W9": PaperCitation("Free lime", ("A. Author",), 2021, "10.2/y")})

    citations = build_citations(
        "Expands [P1], as tables show [S1, P1].", EVIDENCE, RESULTS, registry
    )

    assert citations == [
        Citation(
            label="P1", paper_id="W9", title="Free lime", authors=["A. Author"],
            year=2021, doi="10.2/y", pages=[4, 5],
        ),
        Citation(
            label="S1", paper_id="W1", title="BOF slag in carbonate cements",
            authors=["L. Stefanini, J. Provis"], year=2024, doi="10.1/x",
        ),
    ]  # fmt: skip
    assert asked == [["W9"]]  # the registry is asked once, only for cited passages


def test_labels_the_evidence_does_not_define_get_no_citation():
    registry, _ = _registry({})

    citations = build_citations("Made up [P7] and [S3].", EVIDENCE, RESULTS, registry)

    assert citations == []


def test_a_passage_missing_from_the_registry_keeps_its_title_and_pages():
    registry, _ = _registry({})

    (citation,) = build_citations("Expands [P1].", EVIDENCE, RESULTS, registry)

    assert citation == Citation(label="P1", paper_id="W9", title="Fallback", pages=[4, 5])
