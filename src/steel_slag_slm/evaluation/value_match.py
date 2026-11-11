"""Normalize and match a reference value against printed text, naming which rule found it.

steel_slag_slm.evaluation.value_metrics.leading_number already parses the leading number out of
a printed value; this module adds the surface-form normalization the parsing-ceiling diagnostic
needs on top (unicode minus and dashes, subscript digits, spaced-out formulas, thousands
separators, stripped units) and three explicit match types, so the diagnostic can report how
often each rule did the work, as opposed to one blanket numeric tolerance.
"""

import re
import unicodedata
from typing import Literal

from steel_slag_slm.evaluation.value_metrics import leading_number

MatchType = Literal["exact", "rounded", "range"]

_SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
# Minus sign and the hyphen/dash variants PDF extraction sometimes substitutes for a plain "-"
_DASH_VARIANTS = "\u2212\u2010\u2011\u2012\u2013\u2014\u2015"
_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d),(?=\d{3}\b)")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d{1,2}\b)")
_RANGE = re.compile(r"^([-+]?\d+(?:\.\d+)?)\s*(?:-|to)\s*([-+]?\d+(?:\.\d+)?)$")
# Written differently across papers for the same quantity
_SYNONYMS = {
    "f-cao": "free cao",
    "free lime": "free cao",
}


def normalize_value(raw: str) -> str:
    """Fold a printed value or label to one comparable form.

    Normalizes unicode minus and dash variants to "-", subscript digits to plain digits, strips
    thousands separators, collapses whitespace, lowercases, and maps known synonyms
    (`f-CaO`, `free lime` -> `free cao`) to the same text.
    """
    text = unicodedata.normalize("NFKC", raw).translate(_SUBSCRIPT_DIGITS)
    for dash in _DASH_VARIANTS:
        text = text.replace(dash, "-")
    text = _THOUSANDS_SEPARATOR.sub("", text)
    text = _DECIMAL_COMMA.sub(".", text)
    text = " ".join(text.split()).casefold()
    return _SYNONYMS.get(text, text)


def normalize_formula(raw: str) -> str:
    """Fold a chemical formula to one form, closing the spaces PDF extraction sometimes inserts.

    "Fe2O3", "Fe₂O₃" and "Fe 2 O 3" all normalize to "fe2o3".
    """
    return re.sub(r"\s+", "", normalize_value(raw))


def _as_number(value: str) -> float | None:
    """The leading number of value, after normalization, ignoring any trailing unit."""
    return leading_number(normalize_value(value))


def _decimals(value: str) -> int:
    """How many digits follow the decimal point in value, 0 if there is none."""
    match = re.search(r"\.(\d+)", normalize_value(value))
    return len(match.group(1)) if match else 0


def match_type(reference: str, candidate: str) -> MatchType | None:
    """Classify how candidate matches reference, trying the more exact rules first.

    Tries, in order: normalized text or formula equality; numeric equality or agreement once
    rounded to the reference's own decimal places; the reference's number falling inside a range
    candidate prints (e.g. reference "45", candidate "42-48"). Returns None when none apply.
    """
    if normalize_value(reference) == normalize_value(candidate):
        return "exact"
    if normalize_formula(reference) == normalize_formula(candidate):
        return "exact"

    reference_number = _as_number(reference)
    if reference_number is None:
        return None

    range_match = _RANGE.match(normalize_value(candidate))
    if range_match:
        low, high = sorted(float(bound) for bound in range_match.groups())
        return "range" if low <= reference_number <= high else None

    candidate_number = _as_number(candidate)
    if candidate_number is None:
        return None
    if candidate_number == reference_number:
        return "exact"
    places = _decimals(reference)
    if round(candidate_number, places) == round(reference_number, places):
        return "rounded"
    return None
