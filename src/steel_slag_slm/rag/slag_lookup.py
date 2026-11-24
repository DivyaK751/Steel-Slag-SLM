"""Look up what papers measured on the similar slags, as cited evidence for the answer model."""

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from google.cloud import bigquery

from steel_slag_slm.data.extraction_lookup import (
    read_citations,
    read_provenance,
    read_sheet_rows,
)
from steel_slag_slm.data.extraction_tables import (
    ID_HEADERS,
    SHEET_TABLES,
    SheetTable,
    column_unit,
    is_filled,
    to_column_name,
)
from steel_slag_slm.data.slag_catalogue import OXIDE_LABELS, SlagCatalogueEntry
from steel_slag_slm.data.slag_chemistry import parse_value_range

# Sheets 03-08: what was done to each slag and how it performed
RESULT_SHEETS = SHEET_TABLES[2:-1]
# Already stated once per slag, so not repeated in every row's values
_NOT_VALUES = (*ID_HEADERS, "Steel_Slag_Type")

ProvenanceKey = tuple[str, str, str | None, str]  # source, material, experiment, column header


@dataclass(frozen=True, slots=True)
class Citation:
    """How to cite one paper."""

    source_id: str
    title: str | None
    authors: str | None
    year: str | None
    doi: str | None


@dataclass(frozen=True, slots=True)
class SheetRow:
    """One row of a workbook sheet, with its column headers, values and where each was found."""

    sheet: str  # workbook tab, e.g. "6 Concrete Performance"
    source_id: str
    material_id: str
    experiment_id: str | None
    mix_id: str | None
    values: dict[str, str]  # workbook column header -> value, filled cells only, sheet order
    locations: dict[str, str]  # column header -> page, table or figure in the paper


@dataclass(frozen=True, slots=True)
class SlagEvidence:
    """Everything the papers report about one similar slag."""

    label: str  # S1, S2, ... in match order; the answer cites slags by it
    entry: SlagCatalogueEntry
    citation: Citation | None
    rows_by_sheet: dict[str, list[SheetRow]]  # workbook tab -> rows; sheets without rows absent


@dataclass(frozen=True, slots=True)
class ColumnSummary:
    """One numeric column of one sheet, across all similar slags."""

    sheet: str
    column: str  # workbook column header, e.g. Compressive_Strength_28d_MPa
    unit: str
    low: float
    high: float
    value_count: int
    study_count: int  # distinct papers
    sources: tuple[str, ...]  # e.g. "S1 p. 6, Table 3"


@dataclass(frozen=True, slots=True)
class SlagLookup:
    """The lookup tool's answer: per-slag rows, per-column summaries, and sheets with no data."""

    slags: list[SlagEvidence]
    summaries: list[ColumnSummary]
    sheets_without_data: list[str]


def index_locations(provenance: Sequence[Mapping[str, Any]]) -> dict[ProvenanceKey, str]:
    """Where each value was found, keyed by paper, material, experiment and column header."""
    index: dict[ProvenanceKey, list[str]] = {}
    for row in provenance:
        location = row.get("page_or_table_or_figure")
        if not location:
            continue
        key = (
            row["source_id"],
            row["material_id"],
            row.get("experiment_id"),
            row["fact_or_data_element"],
        )
        if location not in index.setdefault(key, []):
            index[key].append(location)
    return {key: "; ".join(locations) for key, locations in index.items()}


def select_slag_rows(
    rows: Sequence[Mapping[str, Any]], entry: SlagCatalogueEntry
) -> list[Mapping[str, Any]]:
    """The rows of one sheet about this slag.

    Rows of the slag's experiment when the sheet has any, otherwise every row of its material:
    papers often number a slag's chemistry and its tests as different experiments.
    """
    material_rows = [
        row
        for row in rows
        if row["source_id"] == entry.source_id and row["material_id"] == entry.material_id
    ]
    same_experiment = [r for r in material_rows if r.get("experiment_id") == entry.experiment_id]
    return same_experiment if entry.experiment_id and same_experiment else material_rows


def to_sheet_row(
    sheet_table: SheetTable, row: Mapping[str, Any], locations: Mapping[ProvenanceKey, str]
) -> SheetRow:
    """One table row as a sheet row: workbook headers, filled values and their locations."""
    values = {
        header: str(row[to_column_name(header)]).strip()
        for header in sheet_table.headers
        if header not in _NOT_VALUES and is_filled(row.get(to_column_name(header)))
    }
    key = (row["source_id"], row["material_id"], row.get("experiment_id"))
    found = {header: locations.get((*key, header)) for header in values}
    return SheetRow(
        sheet=sheet_table.sheet,
        source_id=row["source_id"],
        material_id=row["material_id"],
        experiment_id=row.get("experiment_id"),
        mix_id=row.get("mix_id"),
        values=values,
        locations={header: where for header, where in found.items() if where},
    )


def _summarise_column(
    sheet: str, header: str, unit: str, slags: Sequence[SlagEvidence]
) -> ColumnSummary | None:
    """Range, value count, study count and sources of one numeric column; None if no values."""
    ranges, studies, sources = [], set(), []
    for slag in slags:
        for row in slag.rows_by_sheet.get(sheet, []):
            parsed = parse_value_range(row.values.get(header, ""))
            if parsed is None:
                continue
            ranges.append(parsed)
            studies.add(row.source_id)
            where = row.locations.get(header)
            source = f"{slag.label} {where}" if where else slag.label
            if source not in sources:
                sources.append(source)
    if not ranges:
        return None
    return ColumnSummary(
        sheet=sheet,
        column=header,
        unit=unit,
        low=min(low for low, _ in ranges),
        high=max(high for _, high in ranges),
        value_count=len(ranges),
        study_count=len(studies),
        sources=tuple(sources),
    )


def summarise_columns(slags: Sequence[SlagEvidence]) -> list[ColumnSummary]:
    """A summary of every numeric column with data, in sheet and column order."""
    summaries = (
        _summarise_column(sheet_table.sheet, header, unit, slags)
        for sheet_table in RESULT_SHEETS
        for header in sheet_table.headers
        if header not in _NOT_VALUES and (unit := column_unit(header)) is not None
    )
    return [summary for summary in summaries if summary is not None]


def build_lookup(
    entries: Sequence[SlagCatalogueEntry],
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    citations: Sequence[Mapping[str, Any]],
    provenance: Sequence[Mapping[str, Any]],
) -> SlagLookup:
    """Assemble the evidence for these slags from rows already read from BigQuery."""
    locations = index_locations(provenance)
    cited = {
        row["source_id"]: Citation(
            source_id=row["source_id"],
            title=row.get("title"),
            authors=row.get("authors"),
            year=row.get("publication_year"),
            doi=row.get("doi_or_url"),
        )
        for row in citations
    }
    seen: set[tuple[Any, ...]] = set()  # two slags of one material would share its rows
    slags = []
    for number, entry in enumerate(entries, start=1):
        rows_by_sheet: dict[str, list[SheetRow]] = {}
        for sheet_table in RESULT_SHEETS:
            for row in select_slag_rows(rows_by_table.get(sheet_table.table, []), entry):
                sheet_row = to_sheet_row(sheet_table, row, locations)
                identity = (
                    sheet_table.table,
                    row["source_id"],
                    row["material_id"],
                    row.get("experiment_id"),
                    row.get("mix_id"),
                    tuple(sheet_row.values.items()),
                )
                if sheet_row.values and identity not in seen:
                    seen.add(identity)
                    rows_by_sheet.setdefault(sheet_table.sheet, []).append(sheet_row)
        slags.append(SlagEvidence(f"S{number}", entry, cited.get(entry.source_id), rows_by_sheet))
    with_data = {sheet for slag in slags for sheet in slag.rows_by_sheet}
    missing = [table.sheet for table in RESULT_SHEETS if table.sheet not in with_data]
    return SlagLookup(slags, summarise_columns(slags), missing)


def lookup_slags(
    client: bigquery.Client, dataset_id: str, entries: Sequence[SlagCatalogueEntry]
) -> SlagLookup:
    """Read and assemble the evidence for these slags from project.dataset."""
    source_ids = sorted({entry.source_id for entry in entries})
    if not source_ids:
        return SlagLookup([], [], [table.sheet for table in RESULT_SHEETS])
    # The 8 reads are independent, so run them together: about the time of the slowest one
    with ThreadPoolExecutor(max_workers=len(RESULT_SHEETS) + 2) as pool:
        sheet_reads = {
            table.table: pool.submit(read_sheet_rows, client, dataset_id, table.table, source_ids)
            for table in RESULT_SHEETS
        }
        citations = pool.submit(read_citations, client, dataset_id, source_ids)
        provenance = pool.submit(read_provenance, client, dataset_id, source_ids)
        rows_by_table = {table: read.result() for table, read in sheet_reads.items()}
        return build_lookup(entries, rows_by_table, citations.result(), provenance.result())


def _cite(citation: Citation | None, source_id: str) -> str:
    """One line naming the paper, e.g. "Insights into ... (2023). Authors. DOI"."""
    if citation is None:
        return source_id
    parts = [f"{citation.title or source_id} ({citation.year or 'n.d.'})"]
    parts += [part for part in (citation.authors, citation.doi) if part]
    return ". ".join(parts)


def _format_row(slag: SlagEvidence, row: SheetRow) -> str:
    """One row as "[S1 EXP-0004 / Mix 20%] Header: value; ... (p. 6)"."""
    ids = " / ".join(part for part in (row.experiment_id, row.mix_id) if part)
    cells = "; ".join(f"{header}: {value}" for header, value in row.values.items())
    pages = sorted(set(row.locations.values()))
    where = f" ({'; '.join(pages)})" if pages else ""
    return f"- [{slag.label}{' ' + ids if ids else ''}] {cells}{where}"


def render_evidence(lookup: SlagLookup) -> str:
    """The evidence as text for the answer model: one section per sheet, every item cited."""
    lines = ["## Similar slags"]
    for slag in lookup.slags:
        entry = slag.entry
        lines.append(
            f"[{slag.label}] {entry.source_id} {entry.material_id} ({entry.steel_slag_type}): "
            + _cite(slag.citation, entry.source_id)
        )
    lines += ["", "## Chemistry and mineralogy"]
    for slag in lookup.slags:
        oxides = ", ".join(
            f"{OXIDE_LABELS[column]} {value:g}%" for column, value in slag.entry.oxides.items()
        )
        lines.append(f"- [{slag.label}] {oxides}")
        if slag.entry.mineral_phases:
            lines.append(f"  Phases: {', '.join(slag.entry.mineral_phases)}")
        if slag.entry.flags:
            lines.append(f"  Flags: {'; '.join(slag.entry.flags)}")
        if slag.entry.notes:
            lines.append(f"  Notes: {slag.entry.notes}")
    for sheet_table in RESULT_SHEETS:
        lines += ["", f"## {sheet_table.sheet}"]
        if sheet_table.sheet in lookup.sheets_without_data:
            lines.append("No data for these slags.")
            continue
        for summary in (s for s in lookup.summaries if s.sheet == sheet_table.sheet):
            span = f"{summary.low:g}"
            if summary.high != summary.low:
                span += f"-{summary.high:g}"
            lines.append(
                f"Summary {summary.column}: {span} {summary.unit}, {summary.value_count} values, "
                f"{summary.study_count} studies [{'; '.join(summary.sources)}]"
            )
        for slag in lookup.slags:
            lines += [
                _format_row(slag, row) for row in slag.rows_by_sheet.get(sheet_table.sheet, [])
            ]
    return "\n".join(lines)
