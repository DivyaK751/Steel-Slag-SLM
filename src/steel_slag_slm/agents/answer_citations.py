"""Turn the labels an answer cites into the citations the chat shows on hover.

[S#] labels are table slags (their paper's citation comes with the lookup); [P#] labels are
paper passages (their paper's title, authors, year and DOI come from the paper registry).
"""

from collections.abc import Callable, Sequence

from steel_slag_slm.agents.answer_writer import cited_labels, defined_labels
from steel_slag_slm.agents.router import ToolResults
from steel_slag_slm.rag.slag_lookup import SlagEvidence
from steel_slag_slm.schemas import Citation, PaperCitation

PaperCitations = Callable[[Sequence[str]], dict[str, PaperCitation]]


def _year(text: str | None) -> int | None:
    """A year written as text ("2024"), or None when it is missing or not a number."""
    return int(text) if text and text.strip().isdigit() else None


def _slag_citation(slag: SlagEvidence) -> Citation:
    paper = slag.citation
    if paper is None:
        return Citation(label=slag.label, paper_id=slag.entry.source_id)
    return Citation(
        label=slag.label,
        paper_id=slag.entry.source_id,
        title=paper.title,
        # One string as the sheet stores it; splitting it on commas would break "Last, F." names
        authors=[paper.authors] if paper.authors else [],
        year=_year(paper.year),
        doi=paper.doi,
    )


def build_citations(
    answer: str, evidence: str, results: ToolResults, paper_citations: PaperCitations
) -> list[Citation]:
    """One citation per label the answer cites and the evidence defines, in order of first use.

    Labels the evidence never defines (made up by the model) get no citation.
    """
    defined = defined_labels(evidence)
    labels = [label for label in cited_labels(answer) if label in defined]
    slags = {slag.label: slag for slag in (results.lookup.slags if results.lookup else [])}
    passages = {f"P{number}": hit.chunk for number, hit in enumerate(results.passages, start=1)}
    papers = paper_citations(sorted({passages[lb].paper_id for lb in labels if lb in passages}))

    citations = []
    for label in labels:
        if label in slags:
            citations.append(_slag_citation(slags[label]))
        elif label in passages:
            chunk = passages[label]
            paper = papers.get(chunk.paper_id)
            citations.append(
                Citation(
                    label=label,
                    paper_id=chunk.paper_id,
                    title=(paper.title if paper else None) or chunk.paper_title or None,
                    authors=list(paper.authors) if paper else [],
                    year=paper.year if paper else None,
                    doi=paper.doi if paper else None,
                    pages=list(chunk.pages),
                )
            )
    return citations
