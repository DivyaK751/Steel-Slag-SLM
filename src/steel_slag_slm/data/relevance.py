"""Decide whether a paper is worth extracting, using the team's feedback rules."""

import re

from steel_slag_slm.config import RelevanceRules
from steel_slag_slm.schemas import Paper


def normalize_for_matching(text: str) -> str:
    """Lowercase text, treat hyphens and dashes as spaces, and collapse whitespace."""
    text = re.sub(r"[-‐-―]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(normalize_for_matching(term) in text for term in terms)


def is_relevant(paper: Paper, rules: RelevanceRules) -> bool:
    """Return True if the paper covers steel slag in a use case the team extracts."""
    title = normalize_for_matching(paper.title)
    title_and_abstract = f"{title} {normalize_for_matching(paper.abstract)}"
    # Exclusions only look at the title: abstracts often mention asphalt or GGBFS in passing
    if _contains_any(title, rules.exclude_title_terms):
        return False
    if not _contains_any(title_and_abstract, rules.slag_terms):
        return False
    return _contains_any(title_and_abstract, rules.include_terms) or _contains_any(
        title, rules.include_title_terms
    )
