import json

import pytest

from steel_slag_slm.data.approved_table_text import enrich_approved_json
from steel_slag_slm.data.parsed_document import parse_parsed_json
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


@pytest.mark.parametrize("row", ["| S1 | |", "| S1 | 45.00 | 9 |", r"| S\|1 | 45.00 |"])
def test_enrichment_reports_ambiguous_rows_without_guessing(row: str) -> None:
    output = json.loads(enrich_approved_json(_approved(TABLE.replace("| S1 | 45.00 |", row))))
    (table,) = output["contextual_tables"]
    assert table["rows"] == []
    assert table["issues"][0]["row_index"] == 0


def test_enrichment_refuses_to_replace_existing_rows() -> None:
    with pytest.raises(ValueError, match="already has contextual_tables"):
        enrich_approved_json(enrich_approved_json(_approved()))
