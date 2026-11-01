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


def test_parse_parsed_json_rejects_a_placement_without_a_page_number() -> None:
    bad = dict(BLOCK, placements=[{"bbox": [1, 2, 3, 4]}])
    with pytest.raises(ValueError, match="page number"):
        parse_parsed_json(_record_with_blocks([bad]))
