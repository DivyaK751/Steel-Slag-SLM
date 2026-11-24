import pytest
from pydantic import ValidationError

from steel_slag_slm.config import (
    OxideRange,
    SimilarSlagSettings,
    SlagCatalogueSettings,
    SlagReference,
    SlagTypeReference,
)
from steel_slag_slm.data.slag_catalogue import SlagCatalogueEntry
from steel_slag_slm.rag.similar_slags import (
    SlagProfile,
    check_profile,
    find_similar_slags,
    oxide_distance,
)

CATALOGUE_SETTINGS = SlagCatalogueSettings(
    dataset="steel_slag_extraction",
    table="slag_catalogue",
    min_coverage_pct=90,
    max_range_width=2,
    heavy_metal_limits={"cr2o3_pct": 0.5, "v2o5_pct": 0.5},
)
SETTINGS = SimilarSlagSettings(
    top_k=5,
    max_oxides_out_of_range_cross_type=0,
    min_shared_phases_cross_type=3,
    min_shared_oxides=3,
)
REFERENCE = SlagReference(
    slag_types={
        "EAF": SlagTypeReference(
            label="EAF slag",
            process="Scrap melting",
            oxides={
                "cao_pct": OxideRange(min=22, max=60),
                "total_fe2o3_pct": OxideRange(min=10, max=40),
                "sio2_pct": OxideRange(min=6, max=34),
                "mgo_pct": OxideRange(min=3, max=13),
            },
            mineral_phases=["Wüstite", "Dicalcium silicate", "Brownmillerite"],
        )
    },
    phase_names={
        "Wüstite": ["wüstite", "RO phase"],
        "Dicalcium silicate": ["C2S", "larnite"],
        "Brownmillerite": ["C4AF", "brownmillerite"],
    },
)
PROFILE = SlagProfile(
    steel_slag_type="EAF",
    oxides={"cao_pct": 38, "fe2o3_pct": 25, "feo_pct": 3, "sio2_pct": 16, "mgo_pct": 8},
    mineral_phases=["RO phase", "C2S", "C4AF"],
)
ALL_PHASES = ("Wüstite", "Dicalcium silicate", "Brownmillerite")


def _slag(
    material_id: str,
    slag_type: str,
    oxides: dict[str, float],
    phases: tuple[str, ...] = (),
    is_comparable: bool = True,
) -> SlagCatalogueEntry:
    return SlagCatalogueEntry(
        source_id="W1",
        material_id=material_id,
        experiment_id=None,
        steel_slag_type=slag_type,
        oxides=oxides,
        mineral_phases=phases,
        oxide_coverage_pct=95.0,
        is_comparable=is_comparable,
        flags=(),
        notes=None,
    )


def _eaf_oxides(cao: float, iron: float = 28, sio2: float = 16, mgo: float = 8) -> dict[str, float]:
    return {"cao_pct": cao, "total_fe2o3_pct": iron, "sio2_pct": sio2, "mgo_pct": mgo}


def _ids(catalogue: list[SlagCatalogueEntry], profile: SlagProfile = PROFILE) -> list[str]:
    result = find_similar_slags(profile, catalogue, REFERENCE, CATALOGUE_SETTINGS, SETTINGS)
    return [match.entry.material_id for match in result.matches]


def test_slag_profile_rejects_unknown_oxide_columns() -> None:
    with pytest.raises(ValidationError, match="unknown oxide columns"):
        SlagProfile(steel_slag_type="EAF", oxides={"cao": 38})


def test_check_profile_totals_iron_reads_phase_names_and_flags_only_heavy_metals() -> None:
    profile = SlagProfile(
        steel_slag_type="EAF",
        oxides={"cao_pct": 72, "fe2o3_pct": 5, "feo_pct": 20, "cr2o3_pct": 1.2},
        mineral_phases=["larnite", "unknown phase"],
    )
    check = check_profile(profile, REFERENCE, CATALOGUE_SETTINGS)
    assert check.oxides["total_fe2o3_pct"] == 27.22
    assert check.mineral_phases == ("Dicalcium silicate",)
    # CaO 72% is outside the EAF range, but CNS flags ranges on published slags only
    assert check.flags == ("Cr2O3 1.2% above 0.5%: leaching risk, possible recovery value",)


def test_check_profile_warns_when_the_oxides_given_are_too_few() -> None:
    profile = SlagProfile(steel_slag_type="EAF", oxides={"cao_pct": 38, "sio2_pct": 16})
    check = check_profile(profile, REFERENCE, CATALOGUE_SETTINGS)
    assert check.flags == ("Your oxides cover 54% of the total; matches are less reliable",)


def test_oxide_distance_scales_by_spread_and_needs_enough_shared_oxides() -> None:
    spreads = {"cao_pct": 10.0, "sio2_pct": 5.0, "mgo_pct": 2.0}
    user = {"cao_pct": 40.0, "sio2_pct": 15.0, "mgo_pct": 8.0}
    assert oxide_distance(user, {"cao_pct": 50, "sio2_pct": 20, "mgo_pct": 10}, spreads, 3) == 1.0
    assert oxide_distance(user, {"cao_pct": 50, "sio2_pct": 20}, spreads, 3) is None


def test_same_type_slags_rank_by_shared_phases_then_distance() -> None:
    catalogue = [
        _slag("near-no-phases", "EAF", _eaf_oxides(38)),
        _slag("far-all-phases", "EAF", _eaf_oxides(50), ALL_PHASES),
        _slag("near-one-phase", "EAF", _eaf_oxides(39), ALL_PHASES[:1]),
        _slag("filler", "EAF", _eaf_oxides(30)),
    ]
    assert _ids(catalogue) == ["far-all-phases", "near-one-phase", "near-no-phases", "filler"]


def test_out_of_range_slags_stay_in_but_rank_below_in_range_ones() -> None:
    catalogue = [
        _slag("two-out-of-range", "EAF", _eaf_oxides(cao=72, iron=50)),
        _slag("one-out-of-range", "EAF", _eaf_oxides(cao=62)),
        _slag("in-range-far", "EAF", _eaf_oxides(cao=25)),
        _slag("incomplete", "EAF", _eaf_oxides(38), is_comparable=False),
    ]
    assert _ids(catalogue) == ["in-range-far", "one-out-of-range", "two-out-of-range"]


def test_another_group_needs_oxides_in_range_and_three_shared_phases() -> None:
    catalogue = [
        _slag("match", "EAF", _eaf_oxides(38)),
        _slag("bof-two-phases", "BOF", _eaf_oxides(40), ALL_PHASES[:2]),
        _slag("bof-one-out-of-range", "BOF", _eaf_oxides(cao=62), ALL_PHASES),
        _slag("bof-three-phases", "BOF", _eaf_oxides(40), ALL_PHASES),
        _slag("stainless-three-phases", "Stainless Steel", _eaf_oxides(41), ALL_PHASES),
    ]
    assert _ids(catalogue) == ["match", "bof-three-phases", "stainless-three-phases"]


def test_stainless_and_unlabelled_slags_match_as_one_other_group() -> None:
    catalogue = [
        _slag("unlabelled", "Other Steel Slag", _eaf_oxides(40)),
        _slag("eaf", "EAF", _eaf_oxides(40)),
    ]
    profile = PROFILE.model_copy(update={"steel_slag_type": "Stainless Steel"})
    result = find_similar_slags(profile, catalogue, REFERENCE, CATALOGUE_SETTINGS, SETTINGS)
    assert [match.entry.material_id for match in result.matches] == ["unlabelled"]
    assert result.matches[0].match_label == (
        "Same group (Other: Other Steel Slag); no typical ranges for Other slags; "
        "no phase data in the paper"
    )


def test_no_match_explains_why() -> None:
    result = find_similar_slags(
        PROFILE, [_slag("bof", "BOF", _eaf_oxides(40))], REFERENCE, CATALOGUE_SETTINGS, SETTINGS
    )
    assert result.matches == []
    assert result.reason_if_empty == (
        "No EAF slag in the catalogue reports enough chemistry to compare"
    )
