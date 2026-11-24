"""Rebuild the slag catalogue from the feedstock-chemistry sheet."""

from google.cloud import bigquery

from steel_slag_slm.config import SlagCatalogueSettings, SlagReference
from steel_slag_slm.data.slag_catalogue import (
    SlagCatalogueEntry,
    build_entries,
    read_chemistry_rows,
    replace_catalogue,
)


def run_catalogue_build(
    client: bigquery.Client, settings: SlagCatalogueSettings, reference: SlagReference
) -> list[SlagCatalogueEntry]:
    """Clean every chemistry row and replace the catalogue table with the result."""
    dataset_id = f"{client.project}.{settings.dataset}"
    entries = build_entries(read_chemistry_rows(client, dataset_id), reference, settings)
    replace_catalogue(client, f"{dataset_id}.{settings.table}", entries)
    return entries
