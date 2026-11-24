import pytest

from steel_slag_slm.data.slag_chemistry import (
    IronReading,
    compile_phase_patterns,
    find_mineral_phases,
    oxide_or_element,
    parse_percent,
    parse_trace_elements,
    parse_value_range,
    read_iron,
    total_iron_as_fe2o3,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("41", (41.0, 41.0)), ("32-48", (32.0, 48.0)), ("41.2 ± 5", (41.2, 41.2)), (">40", None)],
)
def test_parse_value_range_keeps_ranges_as_ranges(
    value: str, expected: tuple[float, float] | None
) -> None:
    assert parse_value_range(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("42.1", 42.1),
        ("4.88 wt%", 4.88),
        ("0.6 ± 0.3%", 0.6),
        ("~2 mass%", 2.0),
        ("0.42-0.705", pytest.approx(0.5625)),
        ("442.30 mg/kg", pytest.approx(0.04423)),
        ("20-50", None),
        (">10", None),
        ("<L.Q.", None),
        ("NA", None),
        (None, None),
        ("0.0", 0.0),
    ],
)
def test_parse_percent_reads_numbers_and_rejects_vague_values(
    value: str | None, expected: float | None
) -> None:
    assert parse_percent(value, max_range_width=2) == expected


def test_parse_trace_elements_normalises_names_and_skips_unusable_entries() -> None:
    text = "FeO + Fe2O3: 33.05%; Cr2O3: 0.5-5%; V2O5: 0.27 wt%; P; S; Co3O4: <L.Q."
    assert parse_trace_elements(text, max_range_width=2) == {"feo+fe2o3": 33.05, "v2o5": 0.27}


@pytest.mark.parametrize(
    ("iron", "notes", "expected"),
    [
        (IronReading(5.0, 20.0, None, None), None, pytest.approx(27.22)),
        (IronReading(25.0, None, None, None), None, 25.0),
        (IronReading(None, 10.0, None, None), None, pytest.approx(11.11)),
        (IronReading(None, None, 33.05, None), None, 33.05),
        (IronReading(None, None, None, 20.0), None, pytest.approx(28.6)),
        (IronReading(None, None, None, None), None, None),
        (IronReading(25.0, 5.0, None, None), "Total iron as Fe2O3 by XRF", 25.0),
    ],
)
def test_total_iron_as_fe2o3_combines_iron_without_double_counting(
    iron: IronReading, notes: str | None, expected: float | None
) -> None:
    assert total_iron_as_fe2o3(iron, notes) == expected


def test_read_iron_prefers_named_iron_values_from_the_trace_list() -> None:
    trace = {"t.fe": 15.28, "m.fe": 2.0, "feo": 4.88}
    assert read_iron(13.56, trace) == IronReading(13.56, 4.88, None, 15.28)


def test_oxide_or_element_converts_a_bare_element_to_its_oxide() -> None:
    assert oxide_or_element({"cr2o3": 0.45}, "cr2o3", "cr", 1.462) == 0.45
    assert oxide_or_element({"cr": 2.1}, "cr2o3", "cr", 1.462) == pytest.approx(3.07, abs=0.01)
    assert oxide_or_element({}, "cr2o3", "cr", 1.462) is None


def test_find_mineral_phases_matches_whole_names_in_order_of_mention() -> None:
    patterns = compile_phase_patterns(
        {
            "Dicalcium silicate": ["C2S", "larnite", "Ca2SiO4"],
            "Alite": ["C3S"],
            "Wüstite": ["wuestite", "RO phase"],
            "Oldhamite": ["CaS"],
        }
    )
    text = "XRD: RO phase, β-C2S, Larnite (Ca₂SiO₄), C3S2 and CaSO4"
    assert find_mineral_phases(text, patterns) == ("Wüstite", "Dicalcium silicate")
