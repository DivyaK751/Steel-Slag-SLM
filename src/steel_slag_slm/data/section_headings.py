"""Tell a parsed paper's real section headings from other text the parser labelled a header."""

import re

# A section title longer than this is a running header merged into the heading; cut it short
MAX_SECTION_CHARS = 150
# A real section starts with its number ("3.2 Utilization…") or is one of these standard names
_NUMBERED_HEADING = re.compile(r"^\d+(\.\d+)*\.?\s+\S")
STANDARD_HEADINGS = frozenset(
    {
        "abstract",
        "introduction",
        "background",
        "literature review",
        "materials and methods",
        "materials and method",
        "methods",
        "methodology",
        "experimental",
        "experimental procedure",
        "results",
        "results and discussion",
        "discussion",
        "conclusion",
        "conclusions",
        "summary",
        "references",
        "bibliography",
        "acknowledgements",
        "acknowledgments",
        "appendix",
    }
)


def is_section_heading(text: str) -> bool:
    """Return True for a real section heading: numbered, or a standard name such as Results."""
    cleaned = " ".join(text.split())
    if _NUMBERED_HEADING.match(cleaned):
        return True
    return cleaned.strip(" .:").lower() in STANDARD_HEADINGS
