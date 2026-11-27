"""Put the tool results for one message into the text the answer model reads.

Order: what to keep in mind, how well the similar slags match, table values with their
sources, then paper passages. Every item names where it came from, so the answer can cite it.
"""

from steel_slag_slm.agents.question_form import QuestionForm
from steel_slag_slm.agents.router import RoutePlan, ToolResults
from steel_slag_slm.rag.similar_slags import SimilarSlags
from steel_slag_slm.rag.slag_lookup import render_evidence

NO_EVIDENCE = "No evidence was found in the extraction tables or the papers for this question."
_REPLY_ONLY = {
    "chat": "Small talk: reply briefly and offer help with steel slag questions. No evidence.",
    "off_topic": "Off-topic: say you can only help with steel slag research. No evidence.",
}


def _notes(form: QuestionForm, results: ToolResults, similar: SimilarSlags | None) -> list[str]:
    notes = list(results.notes)
    if similar is not None:
        notes += [f"User's slag: {flag}" for flag in similar.profile.flags]
    if form.missing_fields:
        notes.append("Not given in the question: " + ", ".join(form.missing_fields))
    return notes


def _match_quality(similar: SimilarSlags) -> list[str]:
    lines = ["## How well the similar slags match"]
    for number, match in enumerate(similar.matches, start=1):
        distance = "not measurable" if match.distance is None else f"{match.distance:.2f}"
        phases = ", ".join(match.shared_phases) or "none shared or not reported"
        lines.append(
            f"- [S{number}] {match.match_label}; oxide distance {distance} (lower is closer); "
            f"shared phases: {phases}"
        )
    return lines


def _passages(results: ToolResults) -> list[str]:
    lines = ["## Paper passages"]
    for number, hit in enumerate(results.passages, start=1):
        chunk = hit.chunk
        where = ", ".join(
            part for part in (chunk.paper_title or chunk.paper_id, chunk.section) if part
        )
        lines.append(f"[P{number}] {where} ({chunk.paper_id}, {chunk.kind})\n{chunk.text}")
    return lines


def build_evidence(
    question: str,
    plan: RoutePlan,
    form: QuestionForm,
    results: ToolResults,
    max_chars: int,
) -> str:
    """The evidence text for one message, cut to max_chars by dropping the last passages.

    Table values come before passages and are never cut, since they carry the numbers.
    """
    head = [f"# Question\n{question.strip()}"]
    if plan.reply_only:
        return "\n\n".join(head + [_REPLY_ONLY.get(form.message_type, NO_EVIDENCE)])
    similar = results.similar
    notes = _notes(form, results, similar)
    if notes:
        head.append("## Keep in mind\n" + "\n".join(f"- {note}" for note in notes))
    tables: list[str] = []
    if similar is not None and similar.matches:
        tables.append("\n".join(_match_quality(similar)))
    if results.lookup is not None and results.lookup.slags:
        text = render_evidence(results.lookup)
        if similar is None:  # slags chosen by the question's filters, not by similarity
            text = text.replace("## Similar slags", "## Slags matching the question", 1)
        tables.append(text)
    if not tables and not results.passages:
        return "\n\n".join(head + [NO_EVIDENCE])
    sections = head + tables
    passages = _passages(results)
    while len(passages) > 1 and len("\n\n".join([*sections, "\n\n".join(passages)])) > max_chars:
        passages.pop()  # the lowest-ranked passage goes first
    if len(passages) > 1:
        sections.append("\n\n".join(passages))
    return "\n\n".join(sections)
