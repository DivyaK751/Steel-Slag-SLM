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
