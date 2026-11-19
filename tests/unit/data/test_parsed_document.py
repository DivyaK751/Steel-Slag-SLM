import json

import pytest

from steel_slag_slm.data.parsed_document import parse_parsed_json


def _record(**overrides: object) -> bytes:
    record: dict[str, object] = {
        "openalex_id": "W1",
        "markdown": "## Results\n\nText",
        "tables": ["Table 1. Composition"],
        "images": [],
    }
    record.update(overrides)
    return json.dumps(record).encode()


def test_parse_parsed_json_reads_markdown_and_tables() -> None:
    paper = parse_parsed_json(_record())
    assert paper.openalex_id == "W1"
    assert paper.markdown == "## Results\n\nText"
    assert paper.tables == ("Table 1. Composition",)


def test_parse_parsed_json_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_parsed_json(b"{not json")


def test_parse_parsed_json_rejects_missing_markdown() -> None:
    with pytest.raises(ValueError, match="openalex_id and markdown"):
        parse_parsed_json(_record(markdown=None))


def test_parse_parsed_json_rejects_non_string_tables() -> None:
    with pytest.raises(ValueError, match="list of strings"):
        parse_parsed_json(_record(tables=[1]))


BLOCK = {
    "kind": "text",
    "label": "text",
    "text": "Body",
    "placements": [{"page_number": 2, "bbox": [1, 2, 3, 4], "coordinate_origin": "BOTTOMLEFT"}],
}


def _record_with_blocks(blocks: list[object]) -> bytes:
    record = {"openalex_id": "W1", "markdown": "", "tables": [], "content_blocks": blocks}
    return json.dumps(record).encode()


def test_parse_parsed_json_reads_content_blocks_with_pages() -> None:
    (block,) = parse_parsed_json(_record_with_blocks([BLOCK])).content_blocks
    assert (block.label, block.text) == ("text", "Body")
    assert block.placements[0].page_number == 2
    assert block.placements[0].bbox == (1.0, 2.0, 3.0, 4.0)


def test_parse_parsed_json_gives_no_content_blocks_for_older_papers() -> None:
    assert parse_parsed_json(_record()).content_blocks == ()


READING = {"figure_type": "data_plot", "summary": "Strength over time.", "rows": [["7", "30"]]}


def _image(**overrides: object) -> dict[str, object]:
    image: dict[str, object] = {
        "figure_number": "3",
        "caption": "Fig. 3. Strength",
        "placements": [{"page_number": 4, "bbox": [1, 2, 3, 4]}],
        "reading": READING,
    }
    image.update(overrides)
    return image


def test_parse_parsed_json_reads_figures_with_their_pages_and_readings() -> None:
    (figure,) = parse_parsed_json(_record(images=[_image()])).figures
    assert (figure.figure_number, figure.caption, figure.pages) == ("3", "Fig. 3. Strength", (4,))
    assert figure.reading is not None and figure.reading.rows == [["7", "30"]]


def test_parse_parsed_json_keeps_a_figure_whose_reading_is_unreadable_without_it() -> None:
    (figure,) = parse_parsed_json(_record(images=[_image(reading={"rows": "bad"})])).figures
    assert (figure.caption, figure.reading) == ("Fig. 3. Strength", None)


def test_parse_parsed_json_skips_old_embedded_images_with_nothing_to_search() -> None:
    legacy = {"xref": 7, "file_name": "img.png", "placements": [{"page_number": 1}]}
    assert parse_parsed_json(_record(images=[legacy])).figures == ()


def test_parse_parsed_json_rejects_a_placement_without_a_page_number() -> None:
    bad = dict(BLOCK, placements=[{"bbox": [1, 2, 3, 4]}])
    with pytest.raises(ValueError, match="page number"):
        parse_parsed_json(_record_with_blocks([bad]))


TABLE_BLOCK = {**BLOCK, "kind": "table", "label": "table", "text": "Table 1. Oxides\n| CaO |"}


def _with_rows(contextual_tables: object) -> bytes:
    record = json.loads(_record_with_blocks([BLOCK, TABLE_BLOCK]))
    record["contextual_tables"] = contextual_tables
    return json.dumps(record).encode()


def test_table_rows_attach_to_the_table_block_their_reference_names() -> None:
    rows = [{"text": "Table 1. Oxides\nCaO: 45.2"}, {"text": "  "}]
    paper = parse_parsed_json(_with_rows([{"reference": "approved-markdown:1", "rows": rows}]))
    assert paper.table_rows == {1: ("Table 1. Oxides\nCaO: 45.2",)}


def test_table_rows_that_point_to_no_table_are_left_out() -> None:
    rows = [{"text": "CaO: 45.2"}]
    tables = [
        {"reference": "approved-markdown:0", "rows": rows},
        {"reference": "x:9", "rows": rows},
    ]
    paper = parse_parsed_json(_with_rows(tables))
    assert paper.table_rows == {}


def test_malformed_table_rows_do_not_fail_the_paper() -> None:
    paper = parse_parsed_json(_with_rows("not a list"))
    assert len(paper.content_blocks) == 2
