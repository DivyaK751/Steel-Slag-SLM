"""Capture Docling table structure without changing production parse artifacts."""

from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.table.table_data import TableData
from docling_core.types.doc.items.text import TextItem
from pydantic import BaseModel


class InspectionText(BaseModel):
    """Keep nearby source text with its own page and bounding-box provenance."""

    text: str
    provenance: list[ProvenanceItem]


class InspectedTable(BaseModel):
    """Preserve native cells, header flags, spans, captions and source locations."""

    reference: str
    caption: str
    footnotes: list[str]
    provenance: list[ProvenanceItem]
    data: TableData
    markdown: str
    preceding_text: InspectionText | None
    following_text: InspectionText | None


def capture_tables(document: DoclingDocument) -> list[InspectedTable]:
    """Capture all tables and their nearest non-caption text in document reading order.

    Nearby text is for human inspection only: adjacency does not establish that it describes
    the table. Preserve Docling's native coordinates and page numbers without reinterpretation.
    """
    items = [item for item, _ in document.iterate_items()]

    def nearby(indices: range) -> InspectionText | None:
        for index in indices:
            item = items[index]
            if isinstance(item, TableItem):
                break
            if isinstance(item, TextItem) and item.label.value == "text":
                return InspectionText(text=item.text, provenance=list(item.prov))
        return None

    return [
        InspectedTable(
            reference=item.self_ref,
            caption=item.caption_text(document),
            footnotes=[ref.resolve(document).text for ref in item.footnotes],
            provenance=list(item.prov),
            data=item.data,
            markdown=item.export_to_markdown(document),
            preceding_text=nearby(range(index - 1, -1, -1)),
            following_text=nearby(range(index + 1, len(items))),
        )
        for index, item in enumerate(items)
        if isinstance(item, TableItem)
    ]
