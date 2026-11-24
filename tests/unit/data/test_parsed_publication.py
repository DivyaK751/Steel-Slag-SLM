import json
from typing import Any

from steel_slag_slm.data.parsed_manifest import (
    ParsedManifest,
    StoredTableDescription,
    has_retired_fields,
    table_text_sha256,
)
from steel_slag_slm.data.parsed_publication import publish_table_descriptions
from steel_slag_slm.schemas import TableDescription


def _stored(record: dict[str, Any], summary: str, prompt_version: int) -> StoredTableDescription:
    return StoredTableDescription(
        block_index=1,
        table_sha256=table_text_sha256(record["content_blocks"][1]["text"]),
        prompt_version=prompt_version,
        description=TableDescription(summary=summary),
    )


def test_publish_table_descriptions_rewrites_the_same_file_without_retired_rows(
    fake_drive: Any, manifest_record: dict[str, Any]
) -> None:
    backfilled = json.dumps({**manifest_record, "contextual_tables": []}).encode()
    file_id = fake_drive.write_file(None, "paper", "parsed.json", backfilled, "application/json")
    manifest = ParsedManifest.model_validate_json(backfilled)

    publish_table_descriptions(None, "paper", manifest, [_stored(manifest_record, "Oxides.", 1)])

    assert fake_drive.list_file_names(None, "paper") == {"parsed.json": file_id}
    content = fake_drive.contents[file_id]
    assert not has_retired_fields(content)
    stored = ParsedManifest.model_validate_json(content)
    assert [d.description.summary for d in stored.table_descriptions] == ["Oxides."]
    assert stored.model_dump(exclude={"table_descriptions"}) == manifest.model_dump(
        exclude={"table_descriptions"}
    )


def test_publish_table_descriptions_replaces_an_older_description_of_the_table(
    fake_drive: Any, manifest_record: dict[str, Any]
) -> None:
    older = _stored(manifest_record, "Old.", 1).model_dump()
    manifest = ParsedManifest.model_validate({**manifest_record, "table_descriptions": [older]})

    updated = publish_table_descriptions(
        None, "paper", manifest, [_stored(manifest_record, "New.", 2)]
    )

    assert [(d.prompt_version, d.description.summary) for d in updated.table_descriptions] == [
        (2, "New.")
    ]
