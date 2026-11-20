import json

import pytest

from steel_slag_slm.data.approved_table_text import enrich_approved_json, render_approved_tables
from steel_slag_slm.data.parsed_document import parse_parsed_json
from steel_slag_slm.data.parsed_manifest import ContentBlock
from steel_slag_slm.rag.chunking import build_chunks

TABLE = "Table 1. Composition [wt%].\n\n| Mix | CaO |\n| --- | --- |\n| S1 | 45.00 |"
PLACEMENT = {"page_number": 2, "bbox": [1, 4, 3, 2], "coordinate_origin": "BOTTOMLEFT"}
GENERATION = "a" * 32
DIGEST = "b" * 64


def _approved(table: str = TABLE) -> bytes:
    return json.dumps(
        {
            "openalex_id": "W1",
            "source_drive_link": "https://example.test/paper",
            "source_sha256": DIGEST,
            "content_schema_version": 4,
            "parser_revision": DIGEST,
            "parse_generation": GENERATION,
            "text_parser": "Docling",
            "image_parser": "Docling+PyMuPDF",
            "page_count": 2,
            "markdown": table,
            "tables": [table],
            "content_blocks": [
                {"kind": "table", "label": "table", "text": table, "placements": [PLACEMENT]}
            ],
            "images": [
                {
                    "figure_id": f"W1:{GENERATION}:f1",
                    "file_name": f"{GENERATION}_figure_01.png",
                    "relative_path": f"images/{GENERATION}_figure_01.png",
                    "file_id": "test-image",
                    "sha256": DIGEST,
                    "width": 100,
                    "height": 100,
                    "render_dpi": 120,
                    "figure_number": "1",
                    "caption": "Fig. 1. Strength",
                    "figure_type": "data_plot",
                    "type_confidence": 1,
                    "pdf_text": "",
                    "detector": "Docling",
                    "renderer": "PyMuPDF",
                    "placements": [
                        {"page_number": 2, "bbox": [1, 2, 3, 4], "coordinate_origin": "TOPLEFT"}
                    ],
                    "caption_placements": [],
                    "reading": {
                        "figure_type": "data_plot",
                        "summary": "Strength over time.",
                        "columns": [{"label": "Strength", "unit": "MPa", "source": "y_axis"}],
                        "rows": [["30"]],
                    },
                }
            ],
        }
    ).encode()


def test_enrichment_preserves_figure_data_and_baseline_chunks() -> None:
    source = _approved()
    enriched = enrich_approved_json(source)
    output = json.loads(enriched)
    (table,) = output.pop("contextual_tables")
    assert output == json.loads(source)
    assert output["images"][0]["reading"]["rows"] == [["30"]]
    assert table["provenance"][0]["page_no"] == 2
    assert table["rows"] == [
        {
            "row_index": 0,
            "cell_indices": [],
            "text": "Table 1. Composition [wt%].\nMix: S1\nCaO: 45.00",
        }
    ]
    assert build_chunks(parse_parsed_json(source), 1500, 3600) == build_chunks(
        parse_parsed_json(enriched), 1500, 3600
    )


@pytest.mark.parametrize("row", ["| S1 | |", "| S1 | 45.00 | 9 |"])
def test_enrichment_reports_rows_without_values_or_alignment(row: str) -> None:
    output = json.loads(enrich_approved_json(_approved(TABLE.replace("| S1 | 45.00 |", row))))
    (table,) = output["contextual_tables"]
    assert table["rows"] == []
    assert table["issues"][0]["row_index"] == 0


def test_enrichment_rerenders_its_own_rows_and_is_idempotent() -> None:
    first = json.loads(enrich_approved_json(_approved()))
    first["contextual_tables"][0]["rows"] = []  # rows written by older rendering rules
    rerendered = enrich_approved_json(json.dumps(first).encode())
    (table,) = json.loads(rerendered)["contextual_tables"]
    assert table["reference"] == "approved-markdown:0"
    assert len(table["rows"]) == 1
    assert enrich_approved_json(rerendered) == rerendered


def test_enrichment_refuses_to_replace_rows_from_a_native_parse() -> None:
    native = json.loads(enrich_approved_json(_approved()))
    native["contextual_tables"][0]["reference"] = "#/tables/0"
    with pytest.raises(ValueError, match="from another source"):
        enrich_approved_json(json.dumps(native).encode())


def _block(text: str, kind: str = "table", label: str = "", page: int = 1) -> ContentBlock:
    return ContentBlock(
        kind=kind,
        label=label or kind,
        text=text,
        placements=[{**PLACEMENT, "page_number": page}],
    )


def _rows(*blocks: ContentBlock) -> list[list[str]]:
    return [[row.text for row in table.rows] for table in render_approved_tables(list(blocks))]


def test_one_unreadable_table_does_not_block_the_others() -> None:
    tables = render_approved_tables([_block("Table 2. Notes\n\nplain text"), _block(TABLE)])
    assert tables[0].issues[0].reason == "No Markdown header and divider"
    assert len(tables[1].rows) == 1


@pytest.mark.parametrize("caption", ["TABLE 1 Composition", "Table-1. Composition", "TABLA 1"])
def test_caption_variants_are_recognised(caption: str) -> None:
    grid = "| Mix | CaO |\n| - | - |\n| S1 | 45.00 |"
    assert _rows(_block(f"{caption}\n\n{grid}")) == [[f"{caption}\nMix: S1\nCaO: 45.00"]]


def test_blank_cells_are_left_out_of_the_row_instead_of_dropping_it() -> None:
    grid = "Table 2. Mixes (% mass).\n\n| Mix | Clinker | Slag |\n| - | - | - |\n| BC | 92 | |"
    assert _rows(_block(grid)) == [["Table 2. Mixes (% mass).\nMix: BC\nClinker: 92"]]


def test_unlinked_caption_or_heading_above_supplies_the_context() -> None:
    grid = "| Mix | CaO |\n| - | - |\n| S1 | 45 |"
    captioned = _rows(_block("Table 3. Oxides (wt%)", "text", "caption"), _block(grid))
    headed = _rows(_block("3.2 Slag composition", "text", "section_header"), _block(grid))
    unnumbered = _rows(_block("Fig. 2 legend", "text", "caption"), _block(grid))
    assert captioned[0][0].startswith("Table 3. Oxides (wt%)\nMix: S1")
    assert headed[0][0].startswith("3.2 Slag composition\nMix: S1")
    assert unnumbered[0][0].startswith("Table on page 1\nMix: S1")


def test_next_page_continuation_inherits_caption_and_header() -> None:
    first = _block("Table 4. Metals (mg/kg)\n\n| Metal | BOF |\n| - | - |\n| Cr | 30 |")
    rest = _block("| Mo | 47 |\n| - | - |\n| Ni | 4.9 |", page=2)
    assert _rows(first, rest)[1] == [
        "Table 4. Metals (mg/kg)\nMetal: Mo\nBOF: 47",
        "Table 4. Metals (mg/kg)\nMetal: Ni\nBOF: 4.9",
    ]


@pytest.mark.parametrize(
    ("block", "reason"),
    [
        (_block("| Section | Page |\n| - | - |\n| Intro ........ | 3 |"), "Table of contents"),
        (_block("| Ref | Source |\n| - | - |\n| [11] | Smith et al. 2020 |"), "Reference list"),
        (_block("| Item | Detail |\n| - | - |\n| Query | Remark |"), "without reported values"),
        (
            _block(
                "| Item | Detail |\n| - | - |\n| Note | Sequestration in 2020 was not reported |"
            ),
            "without reported values",
        ),
        (
            _block("| Section | Page |\n| - | - |\n| Intro | 3 |", label="document_index"),
            "contents",
        ),
    ],
)
def test_uncaptioned_junk_grids_are_blocked(block: ContentBlock, reason: str) -> None:
    (table,) = render_approved_tables([block])
    assert table.rows == []
    assert reason in table.issues[0].reason


def test_group_rows_label_the_rows_below_and_blank_labels_are_not_filled() -> None:
    grid = (
        "Table 5. Properties\n\n| Property | S1 | S2 |\n| - | - | - |\n"
        "| Fresh | | |\n| Slump (cm) | 5 | 7 |\n| | 6 | 8 |\n"
        "| Hardened | Hardened | Hardened |\n| UCS (MPa) | 30 | 45 |"
    )
    (table,) = render_approved_tables([_block(grid)])
    assert [row.text for row in table.rows] == [
        "Table 5. Properties\nGroup: Fresh\nProperty: Slump (cm)\nS1: 5\nS2: 7",
        "Table 5. Properties\nGroup: Hardened\nProperty: UCS (MPa)\nS1: 30\nS2: 45",
    ]
    assert [(issue.row_index, issue.reason) for issue in table.issues] == [(2, "Blank row label")]
