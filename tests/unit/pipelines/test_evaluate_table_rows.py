from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableCell, TableData
from docling_core.types.doc.labels import DocItemLabel

from steel_slag_slm.config import (
    EvalQuestion,
    EvidenceSpan,
    RetrievalEvalSet,
    load_embedding_settings,
)
from steel_slag_slm.pipelines import evaluate_table_rows as evaluation
from steel_slag_slm.pipelines.capture_table_corpus import FullDocumentCapture
from steel_slag_slm.rag.table_budgeting import TableTokenBudget


class FakeTokenizer:
    model_max_length = 512

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        return [1] * (len(text.split()) + (2 if add_special_tokens else 0))

    def num_special_tokens_to_add(self, pair: bool = False) -> int:
        return 4 if pair else 2


class FakeEncoder:
    dimension = 2

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        self.batches.append(list(texts))
        return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(texts), 1))


class FakeScorer:
    def score(self, query: str, passages: Sequence[str]) -> np.ndarray:
        return np.asarray([2.0 if "Mix: SS" in p else float("Table 1" in p) for p in passages])


def _capture(root: Path) -> None:
    document = DoclingDocument(name="test")
    document.add_page(page_no=1, size=Size(width=600, height=800))
    prov = ProvenanceItem(
        page_no=1,
        bbox=BoundingBox(l=10, t=100, r=500, b=200, coord_origin=CoordOrigin.TOPLEFT),
        charspan=(0, 0),
    )
    document.add_text(label=DocItemLabel.TEXT, text="Slag samples were tested.", prov=prov)
    for number, header in [(1, "28-day Strength (MPa)"), (2, "")]:
        caption = document.add_text(label=DocItemLabel.CAPTION, text=f"Table {number}", prov=prov)
        table = document.add_table(
            prov=prov,
            data=TableData(
                num_rows=2,
                num_cols=2,
                table_cells=[
                    TableCell(
                        text=text,
                        column_header=row == 0,
                        start_row_offset_idx=row,
                        end_row_offset_idx=row + 1,
                        start_col_offset_idx=col,
                        end_col_offset_idx=col + 1,
                    )
                    for row, values in enumerate([["Mix", header], ["SS", "45.14"]])
                    for col, text in enumerate(values)
                ],
            ),
        )
        table.captions.append(caption.get_ref())
    report = FullDocumentCapture(
        paper_id="W123",
        source_filename="W123 - Synthetic study.pdf",
        source_sha256="hash",
        source_page_count=1,
        docling_version="test",
        docling_core_version="test",
        pipeline_options_sha256="settings",
        document=document,
    )
    (root / "W123.json").write_text(report.model_dump_json(), encoding="utf-8")


def _queries() -> RetrievalEvalSet:
    return RetrievalEvalSet(
        depth=20,
        corpus="unused.txt",
        questions=[
            EvalQuestion(
                id="table",
                query="What is the 28-day strength of SS?",
                answer=["45.14"],
                evidence=[EvidenceSpan(paper_id="W123", kind="table", location="Table 1")],
            ),
            EvalQuestion(
                id="figure",
                query="What did the figure show?",
                evidence=[EvidenceSpan(paper_id="W123", kind="figure", location="Figure 1")],
            ),
            EvalQuestion(
                id="other",
                query="What did another paper find?",
                evidence=[EvidenceSpan(paper_id="W456", kind="table", location="Table 1")],
            ),
        ],
    )


def test_paired_evaluation_preserves_baseline_and_exact_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture(tmp_path)
    monkeypatch.setattr(evaluation.AutoTokenizer, "from_pretrained", lambda _: FakeTokenizer())
    encoder = FakeEncoder()
    report = evaluation.evaluate_table_rows(
        ["W123"],
        tmp_path,
        _queries(),
        load_embedding_settings(),
        TableTokenBudget(embedding_limit=512, reranker_limit=512, query_reserve=64),
        encoder=encoder,
        scorer=FakeScorer(),
    )
    assert evaluation.TableRowEvaluation.model_validate_json(report.model_dump_json()) == report
    assert set(report.excluded_questions) == {"figure", "other"}
    assert report.augmented.chunk_count == report.baseline.chunk_count + 1
    original = report.baseline.rankings["table"]
    row, *retained = report.augmented.rankings["table"]
    assert sorted(original, key=lambda c: c.position) == sorted(retained, key=lambda c: c.position)
    assert any("Table 2" in c.text and c.kind == "table" for c in retained)
    assert row.pages == (1,)
    assert row.position == len(original)
    assert row.paper_title == "Synthetic study"
    assert row.text.startswith("Table 1\n")
    assert "Mix: SS" in row.text and "28-day Strength (MPa): 45.14" in row.text
    assert row.text.endswith("Source PDF page(s): 1")
    first, blocked = report.papers["W123"].tables
    assert first.rows[0].cell_indices == [2, 3] and first.rows[0].chunk == row
    assert not blocked.rows and blocked.issues
    assert report.augmented.question_scores["table"]["evidence_and_value_at_5"] == 1
    assert [len(batch) for batch in encoder.batches] == [len(original), 1, 1, 1]


@pytest.mark.parametrize(
    ("paper_ids", "reserve", "error", "match"),
    [
        (["W456"], 64, FileNotFoundError, "Missing full-document captures"),
        (["W123"], 1, ValueError, "exceeds the reranker query reserve"),
        (["W123", "W123"], 64, ValueError, "unique paper IDs"),
    ],
)
def test_invalid_inputs_fail_before_model_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    paper_ids: list[str],
    reserve: int,
    error: type[Exception],
    match: str,
) -> None:
    _capture(tmp_path)
    monkeypatch.setattr(evaluation.AutoTokenizer, "from_pretrained", lambda _: FakeTokenizer())
    encoder = FakeEncoder()
    with pytest.raises(error, match=match):
        evaluation.evaluate_table_rows(
            paper_ids,
            tmp_path,
            _queries(),
            load_embedding_settings(),
            TableTokenBudget(embedding_limit=512, reranker_limit=512, query_reserve=reserve),
            encoder=encoder,
            scorer=FakeScorer(),
        )
    assert not encoder.batches
