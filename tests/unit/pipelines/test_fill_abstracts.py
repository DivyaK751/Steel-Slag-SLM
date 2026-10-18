import asyncio
from collections import Counter
from typing import Any

import pymupdf
import pytest
from googleapiclient.errors import HttpError

from steel_slag_slm.config import AbstractExtractionSettings
from steel_slag_slm.data import drive_storage
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.pipelines import fill_abstracts
from steel_slag_slm.schemas import ExtractedAbstract, PaperForAbstract

ABSTRACT = (
    "Ladle furnace slag was ground and used to replace twenty percent of Portland cement in mortar."
)
SETTINGS = AbstractExtractionSettings(
    model="m", temperature=0.0, prompt_name="p", max_pages=3, batch_size=2,
    max_concurrency=2, papers_table="p.d.t",
)  # fmt: skip
PROMPT = PromptTemplate("p", 1, "d", "i", "{openalex_id}{title}", ("openalex_id", "title"))


def _pdf(text: str) -> bytes:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text + " Introduction." * 30)
        return document.tobytes()


@pytest.fixture
def written(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Fake BigQuery with W1-W3 missing abstracts, and a fake Drive holding their PDFs."""
    rows = [{"openalex_id": f"W{n}", "title": "t", "drive_file_id": f"f{n}"} for n in (1, 2, 3)]
    saved: list[dict[str, Any]] = []

    def query_rows(_client: object, _sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        pending = [row for row in rows if row["openalex_id"] not in params["attempted_ids"]]
        return pending[: params["limit"]]

    def merge_updates(*args: Any) -> int:
        saved.extend(args[3])
        return len(args[3])

    monkeypatch.setattr(fill_abstracts, "query_rows", query_rows)
    monkeypatch.setattr(fill_abstracts, "merge_updates", merge_updates)
    monkeypatch.setattr(drive_storage, "download_pdf", lambda _s, file_id: _pdf(ABSTRACT))
    return saved


def _answers(monkeypatch: pytest.MonkeyPatch, answers: dict[str, ExtractedAbstract]) -> None:
    async def extract(_r: object, _p: object, paper: PaperForAbstract, _pdf: bytes) -> Any:
        return answers[paper.openalex_id]

    monkeypatch.setattr(fill_abstracts, "extract_abstract", extract)


def _run(max_papers: int | None = None, dry_run: bool = False) -> Counter[str]:
    return asyncio.run(
        fill_abstracts.run_fill_abstracts(
            SETTINGS,
            object(),
            object(),
            object(),
            PROMPT,
            max_papers,
            dry_run,  # type: ignore[arg-type]
        )
    )


def test_fill_abstracts_writes_verified_abstracts_and_marks_missing_ones(
    written: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    invented = ExtractedAbstract(found=True, abstract="A study of copper slag in asphalt roads.")
    _answers(monkeypatch, {
        "W1": ExtractedAbstract(found=True, abstract=ABSTRACT),
        "W2": ExtractedAbstract(found=False),
        "W3": invented,
    })  # fmt: skip
    outcomes = _run()
    assert {row["openalex_id"]: (row["abstract_source"], row["abstract"]) for row in written} == {
        "W1": ("pdf", ABSTRACT),
        "W2": ("not_found", None),
        "W3": ("pdf_mismatch", None),
    }
    assert outcomes["written"] == 3


def test_fill_abstracts_leaves_papers_whose_pdf_cannot_be_read_for_the_next_run(
    written: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def download(_s: object, file_id: str) -> bytes:
        if file_id == "f1":
            raise HttpError(resp=type("R", (), {"status": 404, "reason": "gone"})(), content=b"")
        return _pdf(ABSTRACT)

    monkeypatch.setattr(drive_storage, "download_pdf", download)
    _answers(
        monkeypatch, {f"W{n}": ExtractedAbstract(found=True, abstract=ABSTRACT) for n in (2, 3)}
    )
    outcomes = _run()
    assert outcomes["failed"] == 1
    assert [row["openalex_id"] for row in written] == ["W2", "W3"]


def test_fill_abstracts_dry_run_writes_nothing_and_respects_max_papers(
    written: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _answers(
        monkeypatch, {f"W{n}": ExtractedAbstract(found=True, abstract=ABSTRACT) for n in (1, 2, 3)}
    )
    outcomes = _run(max_papers=2, dry_run=True)
    assert outcomes["pdf"] == 2 and written == []
