from pathlib import Path

import pytest

from steel_slag_slm.data.local_pdfs import select_local_pdfs


def test_local_selection_preserves_order_and_matches_whole_ids(tmp_path: Path) -> None:
    for name in ["W123 - title.pdf", "W12.pdf"]:
        (tmp_path / name).touch()
    assert [p.name for p in select_local_pdfs(["W12", "W123"], tmp_path)] == [
        "W12.pdf",
        "W123 - title.pdf",
    ]
    (tmp_path / "W123 - duplicate.pdf").touch()
    with pytest.raises(ValueError, match="found 2"):
        select_local_pdfs(["W123"], tmp_path)


@pytest.mark.parametrize("ids", [[], ["W1", "W1"], ["../W1"], ["W9"]])
def test_invalid_or_missing_sources_fail_explicitly(tmp_path: Path, ids: list[str]) -> None:
    with pytest.raises(ValueError):
        select_local_pdfs(ids, tmp_path)
