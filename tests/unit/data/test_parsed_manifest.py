import json
from typing import Any

import pytest
from pydantic import ValidationError

from steel_slag_slm.data.parsed_manifest import (
    ParsedManifest,
    has_retired_fields,
    table_text_sha256,
)

_RETIRED_ROWS = [{"reference": "approved-markdown:1", "rows": [], "issues": []}]


def test_manifest_from_the_row_backfill_loads_without_its_rows(
    manifest_record: dict[str, Any],
) -> None:
    content = json.dumps({**manifest_record, "contextual_tables": _RETIRED_ROWS}).encode()

    manifest = ParsedManifest.model_validate_json(content)

    assert "contextual_tables" not in manifest.model_dump()
    assert manifest == ParsedManifest.model_validate(manifest_record)


def test_has_retired_fields_flags_only_manifests_that_need_a_rewrite(
    manifest_record: dict[str, Any],
) -> None:
    backfilled = {**manifest_record, "contextual_tables": _RETIRED_ROWS}

    assert has_retired_fields(json.dumps(backfilled).encode())
    assert not has_retired_fields(json.dumps(manifest_record).encode())


def _stored(record: dict[str, Any], block_index: int = 1) -> dict[str, Any]:
    return {
        "block_index": block_index,
        "table_sha256": table_text_sha256(record["content_blocks"][block_index]["text"]),
        "prompt_version": 1,
        "description": {"summary": "Oxide composition of the steel slag measured by XRF."},
    }


def test_manifest_keeps_a_description_of_its_table_block(manifest_record: dict[str, Any]) -> None:
    manifest = ParsedManifest.model_validate(
        {**manifest_record, "table_descriptions": [_stored(manifest_record)]}
    )

    assert manifest.table_descriptions[0].description.summary.startswith("Oxide composition")


def test_a_description_is_written_with_a_source_only_when_it_is_the_papers_words(
    manifest_record: dict[str, Any],
) -> None:
    paper = {**_stored(manifest_record), "source": "paper"}
    for stored, written in [(_stored(manifest_record), None), (paper, "paper")]:
        manifest = ParsedManifest.model_validate(
            {**manifest_record, "table_descriptions": [stored]}
        )

        [record] = json.loads(manifest.model_dump_json())["table_descriptions"]

        assert record.get("source") == written  # no key means "gemini", as older code expects
        assert ParsedManifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_manifest_without_descriptions_has_none(manifest_record: dict[str, Any]) -> None:
    assert ParsedManifest.model_validate(manifest_record).table_descriptions == []


def test_manifest_rejects_a_description_of_changed_table_text(
    manifest_record: dict[str, Any],
) -> None:
    stale = {**_stored(manifest_record), "table_sha256": "f" * 64}

    with pytest.raises(ValidationError, match="does not match its table block"):
        ParsedManifest.model_validate({**manifest_record, "table_descriptions": [stale]})


@pytest.mark.parametrize("block_index", [0, 5])
def test_manifest_rejects_a_description_of_a_block_that_is_not_a_table(
    manifest_record: dict[str, Any], block_index: int
) -> None:
    misplaced = {**_stored(manifest_record), "block_index": block_index}

    with pytest.raises(ValidationError, match="table block|past the content blocks"):
        ParsedManifest.model_validate({**manifest_record, "table_descriptions": [misplaced]})


def test_manifest_rejects_two_descriptions_of_one_table(manifest_record: dict[str, Any]) -> None:
    twice = [_stored(manifest_record), _stored(manifest_record)]

    with pytest.raises(ValidationError, match="only one description"):
        ParsedManifest.model_validate({**manifest_record, "table_descriptions": twice})
