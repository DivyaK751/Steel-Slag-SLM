"""Read slag chemistry as papers report it: percent values, trace-element lists, phase names."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

# Molar-mass ratios that put iron, chromium and vanadium on one oxide basis
FEO_TO_FE2O3 = 1.111  # Fe2O3 / (2 FeO)
FE_TO_FE2O3 = 1.430  # Fe2O3 / (2 Fe)
CR_TO_CR2O3 = 1.462  # Cr2O3 / (2 Cr)
V_TO_V2O5 = 1.785  # V2O5 / (2 V)
_MG_PER_KG_TO_PCT = 1e-4

_NUMBER = r"\d+(?:\.\d+)?"
_UNIT = re.compile(r"\s*(?:wt\.?\s*%|mass\s*%|%|mg/kg)\s*$", re.IGNORECASE)
_PLAIN = re.compile(rf"^({_NUMBER})$")
_MEAN_SD = re.compile(rf"^({_NUMBER})\s*(?:±|\+/-)\s*{_NUMBER}$")
_RANGE = re.compile(rf"^({_NUMBER})\s*(?:-|–|to)\s*({_NUMBER})$")
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_NOT_PART_OF_A_NAME = r"[A-Za-z0-9]"

# trace_elements names (spaces removed, lower case) that combine both iron oxides already
_COMBINED_IRON_NAMES = {"feo+fe2o3", "fe2o3+feo", "feox", "t-fe(feo,fe2o3)"}
_TOTAL_FE_NAMES = {"t.fe", "t-fe", "tfe", "totalfe"}  # all iron as the element
# Notes saying the Fe2O3 column already holds all iron, so FeO must not be added to it
_FE2O3_IS_TOTAL = re.compile(
    r"total\s+(?:iron|fe)\s+(?:as|expressed as)\s+fe2o3|fe2o3\s*\(\s*total|fe2o3t\b", re.IGNORECASE
)


def parse_value_range(value: str) -> tuple[float, float] | None:
    """A reported number as (low, high): "41" -> (41, 41), "32-48" -> (32, 48); else None.

    "41.2 ± 5" gives its mean (41.2, 41.2); bounds (">40"), reversed ranges and text give None.
    """
    text = value.strip()
    if match := _PLAIN.match(text) or _MEAN_SD.match(text):
        number = float(match.group(1))
        return number, number
    if match := _RANGE.match(text):
        low, high = float(match.group(1)), float(match.group(2))
        return (low, high) if low <= high else None
    return None


def parse_percent(value: str | None, max_range_width: float) -> float | None:
    """Read one reported wt% value, or None when it isn't a usable number.

    "42.1" and "42.1 wt%" give 42.1; "41.2 ± 5.0" gives its mean; a range no wider than
    max_range_width gives its midpoint ("0.42-0.705" -> 0.56); wider ranges, bounds (">10")
    and blanks give None. mg/kg values are converted to wt%.
    """
    if value is None:
        return None
    text = value.strip().replace("~", "").replace("≈", "")
    scale = _MG_PER_KG_TO_PCT if text.lower().endswith("mg/kg") else 1.0
    number_range = parse_value_range(_UNIT.sub("", text))
    if number_range is None or number_range[1] - number_range[0] > max_range_width:
        return None
    return (number_range[0] + number_range[1]) / 2 * scale


def parse_trace_elements(text: str | None, max_range_width: float) -> dict[str, float]:
    """Read a "name: value; name: value" list into {normalised name: wt%}.

    Names lose their spaces and case ("FeO + Fe2O3" -> "feo+fe2o3"). Entries without a usable
    number ("P; S; Cr", "Co3O4: <L.Q.") are left out.
    """
    values: dict[str, float] = {}
    for entry in (text or "").split(";"):
        name, separator, raw_value = entry.partition(":")
        if not separator:
            continue
        number = parse_percent(raw_value, max_range_width)
        if number is not None:
            values[re.sub(r"\s+", "", name).lower()] = number
    return values


@dataclass(frozen=True, slots=True)
class IronReading:
    """The iron a slag reports, from its columns and its trace-element list."""

    fe2o3_pct: float | None  # the Fe2O3 column
    feo_pct: float | None
    combined_oxide_pct: float | None = None  # "FeO + Fe2O3" or "FeOx", already one number
    total_fe_pct: float | None = None  # "T.Fe": all iron as the element, metallic iron included


def read_iron(fe2o3_pct: float | None, trace: Mapping[str, float]) -> IronReading:
    """Gather the iron values of one slag from its Fe2O3 column and parsed trace elements."""
    combined = next((trace[name] for name in _COMBINED_IRON_NAMES if name in trace), None)
    total_fe = next((trace[name] for name in _TOTAL_FE_NAMES if name in trace), None)
    return IronReading(fe2o3_pct, trace.get("feo"), combined, total_fe)


def total_iron_as_fe2o3(iron: IronReading, notes: str | None) -> float | None:
    """All of a slag's iron expressed as Fe2O3, the basis of CNS's "FeO / Fe2O3" ranges.

    Uses, in order: a combined iron-oxide value; Fe2O3 + FeO x 1.111; T.Fe x 1.43 (which
    includes metallic iron, so slightly high). FeO isn't added when the notes say the Fe2O3
    column already holds all iron.
    """
    if iron.combined_oxide_pct is not None:
        return iron.combined_oxide_pct
    if iron.fe2o3_pct is not None or iron.feo_pct is not None:
        fe2o3 = iron.fe2o3_pct or 0.0
        if iron.feo_pct is None or (iron.fe2o3_pct is not None and fe2o3_is_total(notes)):
            return fe2o3
        return fe2o3 + iron.feo_pct * FEO_TO_FE2O3
    if iron.total_fe_pct is not None:
        return iron.total_fe_pct * FE_TO_FE2O3
    return None


def fe2o3_is_total(notes: str | None) -> bool:
    """Whether the notes say the Fe2O3 value already counts all iron."""
    return bool(notes and _FE2O3_IS_TOTAL.search(notes))


def oxide_or_element(
    trace: Mapping[str, float], oxide: str, element: str, factor: float
) -> float | None:
    """An oxide's wt% from the trace list, converting the bare element if only that is given."""
    if oxide in trace:
        return trace[oxide]
    return trace[element] * factor if element in trace else None


def compile_phase_patterns(phase_names: Mapping[str, list[str]]) -> dict[str, re.Pattern[str]]:
    """One case-insensitive pattern per standard phase, matching any of its names whole."""
    patterns = {}
    for phase, names in phase_names.items():
        alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        # Whole names only: "CaS" must not match inside "CaSO4", nor "C3S" inside "C3S2"
        patterns[phase] = re.compile(
            rf"(?<!{_NOT_PART_OF_A_NAME})(?:{alternatives})(?!{_NOT_PART_OF_A_NAME})",
            re.IGNORECASE,
        )
    return patterns


def find_mineral_phases(text: str, patterns: Mapping[str, re.Pattern[str]]) -> tuple[str, ...]:
    """The standard names of the phases a text mentions, in the order they first appear."""
    normalised = text.translate(_SUBSCRIPTS)
    positions = {
        phase: match.start()
        for phase, pattern in patterns.items()
        if (match := pattern.search(normalised))
    }
    return tuple(sorted(positions, key=positions.__getitem__))
