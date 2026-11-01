"""Spot papers whose title or abstract is not in English, without calling an LLM.

OpenAlex marks almost every paper as "en", so the text itself is checked. Only clear cases
are caught here; the screening prompt still has a rule for the borderline ones.
"""

import unicodedata
from collections import Counter

from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException

from steel_slag_slm.schemas import PaperForScreening

# langdetect is random by default; a fixed seed gives the same answer on every run
DetectorFactory.seed = 0

# A few stray letters (µ, ℃, a Cyrillic author initial) appear in English papers too
MIN_FOREIGN_SCRIPT_LETTERS = 20
# Titles and short abstracts are too short for langdetect (ALL-CAPS English reads as German)
MIN_ABSTRACT_CHARS = 100
MIN_LANGUAGE_PROBABILITY = 0.9
_ALLOWED_SCRIPTS = ("LATIN", "GREEK")  # Greek letters are symbols in English papers


def _foreign_script(text: str) -> str | None:
    """The most common non-Latin script in text (e.g. "HANGUL"), if there is enough of it."""
    scripts = Counter(
        unicodedata.name(char, "UNKNOWN").split()[0]
        for char in text
        if char.isalpha() and not unicodedata.name(char, "").startswith(_ALLOWED_SCRIPTS)
    )
    if sum(scripts.values()) < MIN_FOREIGN_SCRIPT_LETTERS:
        return None
    return scripts.most_common(1)[0][0]


def _abstract_language(abstract: str) -> str | None:
    """langdetect's code for the abstract's language, when it is confidently not English."""
    if len(abstract) < MIN_ABSTRACT_CHARS:
        return None
    try:
        best = detect_langs(abstract)[0]
    except LangDetectException:  # no letters to go on, e.g. only numbers
        return None
    if best.lang != "en" and best.prob >= MIN_LANGUAGE_PROBABILITY:
        return str(best.lang)
    return None


def non_english_reason(paper: PaperForScreening) -> str | None:
    """Why the paper is clearly not (only) in English, or None if it may be English."""
    text = " ".join([paper.title, paper.abstract, *paper.keywords])
    script = _foreign_script(text)
    if script:
        return f"title, abstract or keywords contain {script.title()} script"
    language = _abstract_language(paper.abstract)
    if language:
        return f"abstract is in language '{language}'"
    return None
