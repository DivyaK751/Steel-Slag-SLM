"""BigQuery tables mirroring the data sheets of the CNS steel-slag input workbook."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, get_args

from google.cloud import bigquery

from steel_slag_slm.config import ExtractionTablesSettings
from steel_slag_slm.schemas import SteelSlagType

_UNSAFE_CHARS = re.compile(r"[^a-z0-9_]")


@dataclass(frozen=True, slots=True)
class SheetTable:
    """One data sheet of the workbook and the BigQuery table that stores its rows."""

    sheet: str  # tab name in the workbook
    table: str
    headers: tuple[str, ...]  # workbook column headers, in workbook order


# Copied from "Steel Slag Input Spreadsheet.xlsx"; a new workbook version means updating these
SHEET_TABLES = (
    SheetTable("1 Scientific Literature", "sheet_01_scientific_literature", (
        "Source_ID", "Title", "Authors", "Publication_Year", "Publication_Type",
        "Journal_or_Source", "DOI_or_URL", "Country_or_Region", "Steel_Slag_Type",
        "Study_Objective", "Abstract_or_Key_Finding", "Full_Text_Access",
        "Data_Extraction_Status", "Relevant_To_SCM", "Notes",
    )),
    SheetTable("2 Feedstock Chemistry", "sheet_02_feedstock_chemistry", (
        "Source_ID", "Material_ID", "Experiment_ID", "Steel_Slag_Type", "Source/Origin",
        "Sampling_Date_or_Year", "CaO_pct", "SiO2_pct", "Al2O3_pct", "Fe2O3_pct", "MgO_pct",
        "SO3_pct", "Na2O_pct", "K2O_pct", "TiO2_pct", "P2O5_pct", "MnO_pct", "Trace_Elements",
        "Free_CaO_pct", "Free_MgO_pct", "LOI_pct", "Total_Oxides_pct", "Analysis_Method",
        "Basis_As_Reported", "Notes",
    )),
    SheetTable("3 Processing", "sheet_03_processing", (
        "Source_ID", "Material_ID", "Experiment_ID", "Steel_Slag_Type", "Initial_Material_Form",
        "Processing_Method", "Equipment_or_Process", "Dry_or_Wet", "Process_Temperature_C",
        "Residence_Time", "Energy_Input_if_Reported", "Preprocessing", "Chemical_Additives",
        "CO2_Exposure", "Post_Process_Form", "Yield_pct", "Processing_Notes",
    )),
    SheetTable("4 Particle Size", "sheet_04_particle_size", (
        "Source_ID", "Material_ID", "Experiment_ID", "Steel_Slag_Type", "Measurement_Method",
        "D10_um", "D50_um", "D90_um", "Mean_Particle_Size_um", "Max_Particle_Size_um",
        "Blaine_m2_per_kg", "Sieve_Passing_pct", "Target_Size_um", "Grinding_Time",
        "Grinding_Equipment", "Particle_Size_Notes",
    )),
    SheetTable("5 Activation", "sheet_05_activation", (
        "Source_ID", "Material_ID", "Experiment_ID", "Steel_Slag_Type", "Activation_Type",
        "Activator_or_Additive", "Activator_Dosage", "Activator_Concentration",
        "Activation_Temperature_C", "Activation_Time", "Liquid_to_Solid_Ratio",
        "Mechanical_Activation", "Thermal_Activation", "Chemical_Activation",
        "Carbonation_Activation", "Other_Activation", "Post_Activation_Treatment",
        "Activation_Notes",
    )),
    SheetTable("6 Concrete Performance", "sheet_06_concrete_performance", (
        "Source_ID", "Material_ID", "Experiment_ID", "Mix_ID", "Steel_Slag_Type",
        "SCM_Replacement_pct", "Cement_Type", "Other_SCMs", "Water_Binder_Ratio",
        "Binder_Content_kg_m3", "Admixtures", "Mix_Proportion_Notes", "Workability_Slump_mm",
        "Air_Content_pct", "Set_Time_Initial_min", "Set_Time_Final_min",
        "Compressive_Strength_1d_MPa", "Compressive_Strength_3d_MPa",
        "Compressive_Strength_7d_MPa", "Compressive_Strength_28d_MPa",
        "Compressive_Strength_56d_MPa", "Compressive_Strength_90d_MPa", "Flexural_Strength_MPa",
        "Strength_Test_Method", "Strength_Notes",
    )),
    SheetTable("7 Durability", "sheet_07_durability", (
        "Source_ID", "Material_ID", "Experiment_ID", "Mix_ID", "Steel_Slag_Type",
        "Test_Category", "Test_Method", "Test_Age", "Expansion_pct", "Sulfate_Resistance",
        "Chloride_Resistance", "Freeze_Thaw", "ASR_Performance", "Carbonation_Resistance",
        "Water_Absorption", "Permeability", "Leaching", "Durability_Result", "Durability_Notes",
    )),
    SheetTable("8 Carbon Uptake", "sheet_08_carbon_uptake", (
        "Source_ID", "Material_ID", "Experiment_ID", "Steel_Slag_Type", "Carbonation_Method",
        "CO2_Source", "CO2_Concentration", "CO2_Pressure", "Carbonation_Temperature_C",
        "Carbonation_Time", "Relative_Humidity_pct", "Moisture_Content_pct",
        "CO2_Uptake_kg_per_t", "CO2_Uptake_pct_by_Mass", "Measurement_Method", "CO2_Basis",
        "Net_GWP_if_Reported", "Carbonation_Notes",
    )),
    SheetTable("9 Citations & Provenance", "sheet_09_citations_provenance", (
        "Source_ID", "Material_ID", "Experiment_ID", "Fact_or_Data_Element", "Reported_Value",
        "Unit", "Page_or_Table_or_Figure", "Extraction_Method",
        "Original_Source_Text_or_Excerpt", "Confidence", "Reviewer", "Review_Date",
        "Provenance_Notes",
    )),
)  # fmt: skip

TABLES_BY_NAME = {sheet_table.table: sheet_table for sheet_table in SHEET_TABLES}
LITERATURE_TABLE = SHEET_TABLES[0].table
PROVENANCE_TABLE = SHEET_TABLES[-1].table
# Filled by the pipeline from the paper and its material list, never by the model directly
KEY_HEADERS = ("Source_ID", "Material_ID", "Experiment_ID", "Mix_ID", "Steel_Slag_Type")

# Columns limited to the workbook's controlled vocabulary (plus Dry_or_Wet, by its name)
ALLOWED_VALUES: dict[str, tuple[str, ...]] = {
    "Steel_Slag_Type": get_args(SteelSlagType),
    "Processing_Method": (
        "Grinding", "Milling", "Screening", "Sieving", "Drying", "Magnetic Separation",
        "Classification", "Carbonation", "Other",
    ),
    "Activation_Type": ("None", "Mechanical", "Chemical", "Thermal", "Carbonation", "Combined"),
    "Analysis_Method": ("XRF", "ICP-OES", "ICP-MS", "XRD", "SEM-EDS", "Other"),
    "Dry_or_Wet": ("Dry", "Wet"),
}  # fmt: skip

# A header ending in one of these holds a bare number (or range) in that unit
_UNIT_SUFFIXES = {
    "_pct_by_Mass": "% by mass",
    "_pct": "%",
    "_C": "°C",
    "_MPa": "MPa",
    "_um": "µm",
    "_mm": "mm",
    "_min": "minutes",
    "_kg_m3": "kg/m³",
    "_m2_per_kg": "m²/kg",
    "_kg_per_t": "kg per tonne",
    "Water_Binder_Ratio": "ratio by mass",
}

# Added to every table so a paper's rows can be traced to (and replaced by) one extraction run
RUN_FIELDS = [
    bigquery.SchemaField("extracted_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("extraction_model", "STRING", description="Gemini model ID"),
]


def column_unit(header: str) -> str | None:
    """The unit a numeric column's values are stored in, or None for a free-text column."""
    for suffix, unit in _UNIT_SUFFIXES.items():
        if header.endswith(suffix):
            return unit
    return None


def to_column_name(header: str) -> str:
    """Turn a workbook header into a column name, e.g. Source/Origin -> source_or_origin."""
    return _UNSAFE_CHARS.sub("_", header.lower().replace("/", "_or_"))


def build_schema(sheet_table: SheetTable) -> list[bigquery.SchemaField]:
    """Columns for one sheet's table: its headers in order, then the run fields.

    Values are STRING because the workbook asks for them exactly as reported, and papers
    report ranges ("45-60") and bounds (">40") as often as plain numbers.
    """
    fields = [
        bigquery.SchemaField(
            "source_id",
            "STRING",
            mode="REQUIRED",
            description="OpenAlex ID of the paper; joins to the papers registry",
        )
    ]
    fields += [
        bigquery.SchemaField(to_column_name(header), "STRING")
        for header in sheet_table.headers
        if header != "Source_ID"
    ]
    return fields + RUN_FIELDS


def ensure_extraction_tables(
    client: bigquery.Client, settings: ExtractionTablesSettings
) -> list[str]:
    """Create the dataset and one table per workbook sheet if missing; return their full IDs."""
    dataset = bigquery.Dataset(f"{client.project}.{settings.dataset}")
    dataset.location = settings.location
    client.create_dataset(dataset, exists_ok=True)
    table_ids = []
    for sheet_table in SHEET_TABLES:
        table_id = f"{client.project}.{settings.dataset}.{sheet_table.table}"
        table = bigquery.Table(table_id, schema=build_schema(sheet_table))
        table.description = f'Rows of the "{sheet_table.sheet}" sheet of the CNS input workbook'
        # Every read and re-extraction filters on one paper, so cluster on it
        table.clustering_fields = ["source_id"]
        client.create_table(table, exists_ok=True)
        table_ids.append(table_id)
    return table_ids


def list_extracted_ids(client: bigquery.Client, dataset_id: str) -> set[str]:
    """Return the papers that already have a Scientific Literature row in project.dataset."""
    rows = client.query(
        f"SELECT DISTINCT source_id FROM `{dataset_id}.{LITERATURE_TABLE}`"
    ).result()
    return {row.source_id for row in rows}


def replace_paper_rows(
    client: bigquery.Client,
    dataset_id: str,
    source_id: str,
    rows_by_table: Mapping[str, Sequence[dict[str, Any]]],
) -> int:
    """Delete one paper's rows from every sheet table, then load its new rows.

    The literature row is loaded last, so a run that stops halfway leaves the paper looking
    unextracted and the next run redoes it.

    Returns:
        Bytes billed for the delete; load jobs are free, so this is the paper's whole charge.
    """
    deletes = "\n".join(
        f"DELETE FROM `{dataset_id}.{sheet_table.table}` WHERE source_id = @source_id;"
        for sheet_table in SHEET_TABLES
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("source_id", "STRING", source_id)]
    )
    delete_job = client.query(deletes, job_config=job_config)
    delete_job.result()
    for sheet_table in (*SHEET_TABLES[1:], SHEET_TABLES[0]):
        rows = rows_by_table.get(sheet_table.table)
        if not rows:
            continue
        # A load job, not a streaming insert: streamed rows can't be deleted for ~30 minutes
        load_config = bigquery.LoadJobConfig(
            schema=build_schema(sheet_table), write_disposition="WRITE_APPEND"
        )
        client.load_table_from_json(
            list(rows), f"{dataset_id}.{sheet_table.table}", job_config=load_config
        ).result()
    return int(delete_job.total_bytes_billed or 0)
