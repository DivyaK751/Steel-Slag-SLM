from pathlib import Path

import pytest

from steel_slag_slm.exceptions import PapersNotFoundError
from steel_slag_slm.pipelines.paper_selection import select_papers

PATHS = [Path("pdfs/W1.pdf"), Path("pdfs/W2.pdf"), Path("figures/W3")]


def test_select_papers_keeps_every_path_when_no_ids_are_given() -> None:
    assert select_papers(PATHS, None) == PATHS


def test_select_papers_matches_pdf_files_and_folders_by_name() -> None:
    assert select_papers(PATHS, ["W3", "W1"]) == [Path("pdfs/W1.pdf"), Path("figures/W3")]


def test_select_papers_names_every_missing_id() -> None:
    with pytest.raises(PapersNotFoundError) as error:
        select_papers(PATHS, ["W1", "W9", "W8"])
    assert error.value.missing_ids == ["W8", "W9"]
