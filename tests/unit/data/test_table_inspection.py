from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableCell, TableData
from docling_core.types.doc.labels import DocItemLabel

from steel_slag_slm.data.table_inspection import InspectedTable, capture_tables


def _document() -> DoclingDocument:
    document = DoclingDocument(name="test")
    document.add_page(page_no=13, size=Size(width=600, height=800))
    provenance = ProvenanceItem(
        page_no=13,
        bbox=BoundingBox(l=10, t=100, r=500, b=200, coord_origin=CoordOrigin.TOPLEFT),
        charspan=(0, 0),
    )
    document.add_text(label=DocItemLabel.TEXT, text="Before table", prov=provenance)
    caption = document.add_text(
        label=DocItemLabel.CAPTION, text="Table 11. Strength", prov=provenance
    )
    table = document.add_table(
        data=TableData(
            num_rows=2,
            num_cols=2,
            table_cells=[
                TableCell(
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=2,
                    col_span=2,
                    text="Strength (MPa)",
                    column_header=True,
                ),
                TableCell(
                    start_row_offset_idx=1,
                    end_row_offset_idx=2,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                    text=">45.14",
                ),
                TableCell(
                    start_row_offset_idx=1,
                    end_row_offset_idx=2,
                    start_col_offset_idx=1,
                    end_col_offset_idx=2,
                    text="–",
                ),
            ],
        ),
        prov=provenance,
    )
    table.captions.append(caption.get_ref())
    note = document.add_text(label=DocItemLabel.FOOTNOTE, text="As reported")
    table.footnotes.append(note.get_ref())
    document.add_text(label=DocItemLabel.TEXT, text="After table", prov=provenance)
    return document


def test_capture_preserves_structure_provenance_and_context_without_mutation() -> None:
    document = _document()
    before = document.model_dump_json()
    (table,) = capture_tables(document)
    restored = InspectedTable.model_validate_json(table.model_dump_json())
    assert restored == table
    assert table.caption == "Table 11. Strength"
    assert table.footnotes == ["As reported"]
    assert table.data.table_cells[0].col_span == 2
    assert table.data.table_cells[0].column_header
    assert [cell.text for cell in table.data.table_cells] == ["Strength (MPa)", ">45.14", "–"]
    assert table.provenance[0].page_no == 13
    assert table.provenance[0].bbox.coord_origin == CoordOrigin.TOPLEFT
    assert table.preceding_text is not None
    assert table.preceding_text.text == "Before table"
    assert table.following_text is not None
    assert table.following_text.text == "After table"
    assert ">45.14" in table.markdown
    assert document.model_dump_json() == before


def test_capture_handles_no_tables_and_missing_captions() -> None:
    document = DoclingDocument(name="empty")
    assert capture_tables(document) == []
    document.add_table(data=TableData(num_rows=0, num_cols=0, table_cells=[]))
    (table,) = capture_tables(document)
    assert table.caption == ""
    assert table.preceding_text is None
    assert table.following_text is None
