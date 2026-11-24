"""Find the catalogue slags most similar to the user's slag: same type, oxides, mineral phases."""

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, field_validator

from steel_slag_slm.config import SimilarSlagSettings, SlagCatalogueSettings, SlagReference
from steel_slag_slm.data.slag_catalogue import (
    CATALOGUE_OXIDES,
    COVERAGE_OXIDES,
    SlagCatalogueEntry,
    heavy_metal_flags,
    oxide_coverage,
    oxides_out_of_range,
)
from steel_slag_slm.data.slag_chemistry import (
    IronReading,
    compile_phase_patterns,
    find_mineral_phases,
    total_iron_as_fe2o3,
)
from steel_slag_slm.schemas import SteelSlagType

_MIN_VALUES_FOR_SPREAD = 3
OTHER_GROUP = "Other"


class SlagProfile(BaseModel):
    """The user's slag, as collected at the start of the chat."""

    steel_slag_type: SteelSlagType
    oxides: dict[str, float]  # wt% by catalogue column, e.g. {"cao_pct": 38, "feo_pct": 3}
    mineral_phases: list[str] = []  # any names papers use, e.g. "C2S" or "RO phase"
    cooling: str | None = None  # e.g. "air-cooled"; passed on, not matched on

    @field_validator("oxides")
    @classmethod
    def _known_oxides(cls, oxides: dict[str, float]) -> dict[str, float]:
        unknown = set(oxides) - set(CATALOGUE_OXIDES)
        if unknown:
            raise ValueError(f"unknown oxide columns {sorted(unknown)}; use {CATALOGUE_OXIDES}")
        return oxides


@dataclass(frozen=True, slots=True)
class ProfileCheck:
    """The user's slag on the catalogue's basis, with anything unusual about it."""

    oxides: dict[str, float]  # with total_fe2o3_pct added
    mineral_phases: tuple[str, ...]  # standard names
    oxide_coverage_pct: float
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SimilarSlag:
    """One catalogue slag that matches the user's slag, and how well."""

    entry: SlagCatalogueEntry
    is_same_type: bool
    oxides_out_of_range: int  # outside the typical ranges of the user's slag type
    shared_phases: tuple[str, ...]
    distance: float | None  # scaled oxide distance; smaller is closer; None if too few shared
    match_label: str


@dataclass(frozen=True, slots=True)
class SimilarSlags:
    """The tool's answer: the user's slag checked, its closest slags, and why if there are none."""

    profile: ProfileCheck
    matches: list[SimilarSlag]
    reason_if_empty: str | None


def check_profile(
    profile: SlagProfile, reference: SlagReference, catalogue_settings: SlagCatalogueSettings
) -> ProfileCheck:
    """Put the user's slag on the catalogue's basis and flag Cr/V and thin data.

    Out-of-range oxides are flagged only on published slags (CNS): the user's slag is what it is.
    """
    oxides = dict(profile.oxides)
    iron = IronReading(oxides.get("fe2o3_pct"), oxides.get("feo_pct"))
    total_iron = oxides.get("total_fe2o3_pct") or total_iron_as_fe2o3(iron, None)
    if total_iron is not None:
        oxides["total_fe2o3_pct"] = round(total_iron, 2)
    patterns = compile_phase_patterns(reference.phase_names)
    phases = find_mineral_phases("; ".join(profile.mineral_phases), patterns)
    coverage = oxide_coverage(oxides, None)
    flags = heavy_metal_flags(oxides, catalogue_settings.heavy_metal_limits)
    if coverage < catalogue_settings.min_coverage_pct:
        flags.append(f"Your oxides cover {coverage:g}% of the total; matches are less reliable")
    return ProfileCheck(oxides, phases, coverage, tuple(flags))


def slag_group(steel_slag_type: str | None, reference: SlagReference) -> str:
    """The group a slag is matched in: its type if CNS gave ranges for it, else "Other".

    CNS treats everything but EAF, BOF and LMF (stainless, dust-derived, unlabelled) as Other.
    """
    return steel_slag_type if steel_slag_type in reference.slag_types else OTHER_GROUP


def oxide_spreads(
    entries: Sequence[SlagCatalogueEntry], in_group: Sequence[SlagCatalogueEntry]
) -> dict[str, float]:
    """How much each oxide usually varies within the group (all slags if too few values)."""
    spreads = {}
    for column in COVERAGE_OXIDES:
        group_values = [entry.oxides[column] for entry in in_group if column in entry.oxides]
        values = group_values
        if len(values) < _MIN_VALUES_FOR_SPREAD:
            values = [entry.oxides[column] for entry in entries if column in entry.oxides]
        if len(values) >= _MIN_VALUES_FOR_SPREAD and (spread := statistics.stdev(values)) > 0:
            spreads[column] = spread
    return spreads


def oxide_distance(
    user: Mapping[str, float],
    slag: Mapping[str, float],
    spreads: Mapping[str, float],
    min_shared: int,
) -> float | None:
    """Root-mean-square difference over shared oxides, each in units of its usual spread."""
    terms = [
        ((user[column] - slag[column]) / spread) ** 2
        for column, spread in spreads.items()
        if column in user and column in slag
    ]
    if len(terms) < min_shared:
        return None
    return round(math.sqrt(sum(terms) / len(terms)), 3)


def _match_label(
    entry: SlagCatalogueEntry,
    group: str,
    has_ranges: bool,
    is_same_type: bool,
    out_of_range: int,
    shared: tuple[str, ...],
    user_has_phases: bool,
) -> str:
    """A short reason for the match, e.g. "Same type (EAF); oxides in range; 3 shared phases"."""
    if not is_same_type:
        kind = f"Other type ({entry.steel_slag_type})"
    elif group == OTHER_GROUP:
        kind = f"Same group (Other: {entry.steel_slag_type})"
    else:
        kind = f"Same type ({group})"
    if not has_ranges:
        ranges = f"no typical ranges for {group} slags"
    elif out_of_range == 0:
        ranges = f"oxides in typical {group} ranges"
    else:
        ranges = f"{out_of_range} oxide(s) outside typical {group} ranges"
    if not entry.mineral_phases:
        phases = "no phase data in the paper"
    elif not user_has_phases:
        phases = "phases not compared (none given)"
    else:
        phases = f"{len(shared)} shared phase(s)"
    return f"{kind}; {ranges}; {phases}"


def find_similar_slags(
    profile: SlagProfile,
    catalogue: Sequence[SlagCatalogueEntry],
    reference: SlagReference,
    catalogue_settings: SlagCatalogueSettings,
    settings: SimilarSlagSettings,
) -> SimilarSlags:
    """The catalogue slags most similar to the user's slag, closest first.

    Slags of the user's group (EAF, BOF, Ladle or Other) come first; then more shared phases;
    then fewer oxides outside the typical ranges; then smaller oxide distance. Out-of-range slags
    stay in, and their flags travel with them. A slag of another group is included only when its
    oxides fit the user's ranges and it shares enough phases. Slags with too little chemistry
    are left out.
    """
    check = check_profile(profile, reference, catalogue_settings)
    group = slag_group(profile.steel_slag_type, reference)
    group_reference = reference.slag_types.get(group)
    comparable = [entry for entry in catalogue if entry.is_comparable]
    in_group = [e for e in comparable if slag_group(e.steel_slag_type, reference) == group]
    spreads = oxide_spreads(comparable, in_group)
    matches = []
    for entry in comparable:
        is_same_type = slag_group(entry.steel_slag_type, reference) == group
        out_of_range = (
            len(oxides_out_of_range(entry.oxides, group_reference)) if group_reference else 0
        )
        shared = tuple(phase for phase in check.mineral_phases if phase in entry.mineral_phases)
        if not is_same_type and (
            out_of_range > settings.max_oxides_out_of_range_cross_type
            or len(shared) < settings.min_shared_phases_cross_type
        ):
            continue
        distance = oxide_distance(check.oxides, entry.oxides, spreads, settings.min_shared_oxides)
        label = _match_label(
            entry,
            group,
            group_reference is not None,
            is_same_type,
            out_of_range,
            shared,
            bool(check.mineral_phases),
        )
        matches.append(SimilarSlag(entry, is_same_type, out_of_range, shared, distance, label))
    matches.sort(
        key=lambda m: (
            not m.is_same_type,
            -len(m.shared_phases),
            m.oxides_out_of_range,
            m.distance is None,  # slags too sparse to measure go after measured ones
            m.distance or 0.0,
        )
    )
    top = matches[: settings.top_k]
    # Said when nothing matches, so the answer can fall back to paper search
    reason = f"No {group} slag in the catalogue reports enough chemistry to compare"
    return SimilarSlags(check, top, None if top else reason)
