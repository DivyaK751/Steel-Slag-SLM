import json
from typing import Any

from steel_slag_slm.data.parsed_manifest import ParsedManifest, has_retired_fields

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
