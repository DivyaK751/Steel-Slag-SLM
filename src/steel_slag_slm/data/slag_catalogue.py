"""The slag catalogue: one cleaned row per slag of the feedstock-chemistry sheet."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.config import (
    OxideRange,
    SlagCatalogueSettings,
    SlagReference,
    SlagTypeReference,
)
from steel_slag_slm.data.bigquery_store import query_rows
from steel_slag_slm.data.extraction_tables import CHEMISTRY_TABLE
from steel_slag_slm.data.slag_chemistry import (
    CR_TO_CR2O3,
    V_TO_V2O5,
    compile_phase_patterns,
    find_mineral_phases,
    oxide_or_element,
    parse_percent,
    parse_trace_elements,
    read_iron,
    total_iron_as_fe2o3,
)

# Oxide columns of the chemistry sheet; a value missing there may be found in trace_elements
SHEET_OXIDES = (
    "cao_pct", "sio2_pct", "al2o3_pct", "fe2o3_pct", "mgo_pct", "so3_pct", "na2o_pct",
    "k2o_pct", "tio2_pct", "p2o5_pct", "mno_pct",
)  # fmt: skip
# The oxides whose sum is the coverage; iron counts once, as total_fe2o3_pct
COVERAGE_OXIDES = tuple(o for o in SHEET_OXIDES if o != "fe2o3_pct") + ("total_fe2o3_pct",)
# Every catalogue oxide column (sheet oxides first, in sheet order) and how answers name it
OXIDE_LABELS = {
    "cao_pct": "CaO", "sio2_pct": "SiO2", "al2o3_pct": "Al2O3", "fe2o3_pct": "Fe2O3",
    "mgo_pct": "MgO", "so3_pct": "SO3", "na2o_pct": "Na2O", "k2o_pct": "K2O",
    "tio2_pct": "TiO2", "p2o5_pct": "P2O5", "mno_pct": "MnO", "feo_pct": "FeO",
    "total_fe2o3_pct": "Total iron (as Fe2O3)", "cr2o3_pct": "Cr2O3", "v2o5_pct": "V2O5",
    "free_cao_pct": "Free CaO", "free_mgo_pct": "Free MgO",
}  # fmt: skip
CATALOGUE_OXIDES = tuple(OXIDE_LABELS)
_FULL_TOTAL_PCT = 100.0


@dataclass(frozen=True, slots=True)
class SlagCatalogueEntry:
    """One slag as the similarity search sees it: clean wt% values, phases and flags."""

    source_id: str
    material_id: str
    experiment_id: str | None
    steel_slag_type: str | None
    oxides: dict[str, float]  # wt% by catalogue column; oxides the paper doesn't give are absent
    mineral_phases: tuple[str, ...]  # standard names, in the order the paper mentions them
    oxide_coverage_pct: float
    is_comparable: bool
    flags: tuple[str, ...]
    notes: str | None


def clean_oxides(row: Mapping[str, Any], max_range_width: float) -> dict[str, float]:
    """The wt% of every catalogue oxide a chemistry row gives, from its columns and trace list."""
    trace = parse_trace_elements(row.get("trace_elements"), max_range_width)
    oxides: dict[str, float | None] = {
        column: parse_percent(row.get(column), max_range_width)
        for column in (*SHEET_OXIDES, "free_cao_pct", "free_mgo_pct")
    }
    for column in SHEET_OXIDES:  # e.g. a MnO or TiO2 given only in the trace list
        if oxides[column] is None:
            oxides[column] = trace.get(OXIDE_LABELS[column].lower())
    iron = read_iron(oxides["fe2o3_pct"], trace)
    oxides["feo_pct"] = iron.feo_pct
    oxides["total_fe2o3_pct"] = total_iron_as_fe2o3(iron, row.get("notes"))
    oxides["cr2o3_pct"] = oxide_or_element(trace, "cr2o3", "cr", CR_TO_CR2O3)
    oxides["v2o5_pct"] = oxide_or_element(trace, "v2o5", "v", V_TO_V2O5)
    return {column: round(value, 2) for column, value in oxides.items() if value is not None}


def oxide_coverage(oxides: Mapping[str, float], total_oxides_pct: float | None) -> float:
    """The share of the slag's reported total that its major oxides account for, in %."""
    covered = sum(oxides.get(column, 0.0) for column in COVERAGE_OXIDES)
    return round(100 * covered / (total_oxides_pct or _FULL_TOTAL_PCT), 1)


def oxides_out_of_range(
    oxides: Mapping[str, float], reference: SlagTypeReference
) -> list[tuple[str, float, OxideRange]]:
    """Each oxide outside the typical range of a slag type, as (column, value, range)."""
    return [
        (column, value, typical)
        for column, typical in reference.oxides.items()
        if (value := oxides.get(column)) is not None and not typical.min <= value <= typical.max
    ]


def range_flags(
    oxides: Mapping[str, float], slag_type: str, reference: SlagTypeReference
) -> list[str]:
    """A message for each oxide outside the typical range of its slag type."""
    return [
        f"{OXIDE_LABELS[column]} {value:g}% {'above' if value > typical.max else 'below'} "
        f"typical {slag_type} range {typical.min:g}-{typical.max:g}%"
        for column, value, typical in oxides_out_of_range(oxides, reference)
    ]


def heavy_metal_flags(oxides: Mapping[str, float], limits: Mapping[str, float]) -> list[str]:
    """A message for each heavy-metal oxide above its limit (leaching risk, recovery value)."""
    return [
        f"{OXIDE_LABELS[column]} {oxides[column]:g}% above {limit:g}%: leaching risk, "
        "possible recovery value"
        for column, limit in limits.items()
        if oxides.get(column, 0.0) > limit
    ]


def build_entries(
    rows: Sequence[Mapping[str, Any]],
    reference: SlagReference,
    settings: SlagCatalogueSettings,
) -> list[SlagCatalogueEntry]:
    """Clean every chemistry row into a catalogue entry."""
    patterns = compile_phase_patterns(reference.phase_names)
    entries = []
    for row in rows:
        oxides = clean_oxides(row, settings.max_range_width)
        slag_type = row.get("steel_slag_type")
        total = parse_percent(row.get("total_oxides_pct"), settings.max_range_width)
        coverage = oxide_coverage(oxides, total)
        flags = heavy_metal_flags(oxides, settings.heavy_metal_limits)
        if slag_type in reference.slag_types:
            flags += range_flags(oxides, slag_type, reference.slag_types[slag_type])
        phase_text = f"{row.get('notes') or ''} {row.get('trace_elements') or ''}"
        entries.append(
            SlagCatalogueEntry(
                source_id=row["source_id"],
                material_id=row["material_id"],
                experiment_id=row.get("experiment_id"),
                steel_slag_type=slag_type,
                oxides=oxides,
                mineral_phases=find_mineral_phases(phase_text, patterns),
                oxide_coverage_pct=coverage,
                is_comparable=coverage >= settings.min_coverage_pct,
                flags=tuple(flags),
                notes=row.get("notes"),
            )
        )
    return entries


def catalogue_schema() -> list[bigquery.SchemaField]:
    """Columns of the catalogue table: keys, one FLOAT per oxide, phases, coverage, flags."""
    return [
        bigquery.SchemaField("source_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("material_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("experiment_id", "STRING"),
        bigquery.SchemaField("steel_slag_type", "STRING"),
        *(bigquery.SchemaField(column, "FLOAT") for column in CATALOGUE_OXIDES),
        bigquery.SchemaField("mineral_phases", "STRING", mode="REPEATED"),
        bigquery.SchemaField("oxide_coverage_pct", "FLOAT"),
        bigquery.SchemaField("is_comparable", "BOOLEAN"),
        bigquery.SchemaField("flags", "STRING", mode="REPEATED"),
        bigquery.SchemaField("notes", "STRING"),
        bigquery.SchemaField("built_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def to_table_row(entry: SlagCatalogueEntry, built_at: datetime) -> dict[str, Any]:
    """One catalogue entry as a row of the catalogue table."""
    return {
        "source_id": entry.source_id,
        "material_id": entry.material_id,
        "experiment_id": entry.experiment_id,
        "steel_slag_type": entry.steel_slag_type,
        **{column: entry.oxides.get(column) for column in CATALOGUE_OXIDES},
        "mineral_phases": list(entry.mineral_phases),
        "oxide_coverage_pct": entry.oxide_coverage_pct,
        "is_comparable": entry.is_comparable,
        "flags": list(entry.flags),
        "notes": entry.notes,
        "built_at": built_at.isoformat(),
    }


def read_chemistry_rows(client: bigquery.Client, dataset_id: str) -> list[dict[str, Any]]:
    """Every row of the feedstock-chemistry sheet in project.dataset."""
    sql = f"SELECT * FROM `{dataset_id}.{CHEMISTRY_TABLE}` ORDER BY source_id, material_id"
    return query_rows(client, sql)


def replace_catalogue(
    client: bigquery.Client, table_id: str, entries: Sequence[SlagCatalogueEntry]
) -> None:
    """Overwrite the catalogue table with these entries, creating it if needed."""
    built_at = datetime.now(UTC)
    job_config = bigquery.LoadJobConfig(
        schema=catalogue_schema(), write_disposition="WRITE_TRUNCATE"
    )
    rows = [to_table_row(entry, built_at) for entry in entries]
    client.load_table_from_json(rows, table_id, job_config=job_config).result()


def read_catalogue(client: bigquery.Client, table_id: str) -> list[SlagCatalogueEntry]:
    """Every slag of the catalogue table, as entries."""
    return [
        SlagCatalogueEntry(
            source_id=row["source_id"],
            material_id=row["material_id"],
            experiment_id=row["experiment_id"],
            steel_slag_type=row["steel_slag_type"],
            oxides={c: row[c] for c in CATALOGUE_OXIDES if row[c] is not None},
            mineral_phases=tuple(row["mineral_phases"]),
            oxide_coverage_pct=row["oxide_coverage_pct"],
            is_comparable=row["is_comparable"],
            flags=tuple(row["flags"]),
            notes=row["notes"],
        )
        for row in query_rows(client, f"SELECT * FROM `{table_id}`")
    ]
