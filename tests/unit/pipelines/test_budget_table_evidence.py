from pathlib import Path
from unittest.mock import patch

import pytest
from docling_core.types.doc.items.table.table_data import TableCell, TableData
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import PreTrainedTokenizerFast

from steel_slag_slm.config import TableInspectionCase
from steel_slag_slm.data.table_inspection import InspectedTable
from steel_slag_slm.pipelines.budget_table_evidence import (
    TableChunkReport,
    budget_local_table_evidence,
)
from steel_slag_slm.pipelines.inspect_tables import TableInspectionReport
from steel_slag_slm.rag.table_budgeting import TableTokenBudget


def test_offline_budgeting_roundtrip_and_preflight_errors(tmp_path: Path) -> None:
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    fast = PreTrainedTokenizerFast(tokenizer_object=tokenizer, model_max_length=512)
    cells = [
        TableCell(
            text=text,
            column_header=row == 0,
            start_row_offset_idx=row,
            end_row_offset_idx=row + 1,
            start_col_offset_idx=col,
            end_col_offset_idx=col + 1,
        )
        for row, values in enumerate([["Mix", "Strength (MPa)"], ["SS", ">45.14"]])
        for col, text in enumerate(values)
    ]
    report = TableInspectionReport(
        case=TableInspectionCase(paper_id="W123", page=1, target="Table 1"),
        source_filename="W123 - Title.pdf",
        source_sha256="source",
        source_page_count=1,
        docling_version="test",
        docling_core_version="test",
        status="captured",
        tables=[
            InspectedTable(
                reference="#/tables/0",
                caption="Table 1",
                footnotes=[],
                provenance=[],
                data=TableData(num_rows=2, num_cols=2, table_cells=cells),
                markdown="",
                preceding_text=None,
                following_text=None,
            )
        ],
    )
    source = tmp_path / "W123.json"
    source.write_text(report.model_dump_json())
    original = source.read_bytes()
    budget = TableTokenBudget(embedding_limit=512, reranker_limit=512, query_reserve=64)
    with patch(
        "steel_slag_slm.pipelines.budget_table_evidence.AutoTokenizer.from_pretrained",
        return_value=fast,
    ) as load:
        (saved,) = budget_local_table_evidence(
            tmp_path, tmp_path / "out", budget, "embed", "rerank"
        )
        assert all(call.kwargs == {"local_files_only": True} for call in load.call_args_list)
        assert saved == TableChunkReport.model_validate_json(
            (tmp_path / "out/W123.json").read_bytes()
        )
        assert saved.tables[0].rows[0].chunk.text == "Table 1\nMix: SS\nStrength (MPa): >45.14"
        assert source.read_bytes() == original
        with pytest.raises(FileExistsError):
            budget_local_table_evidence(tmp_path, tmp_path / "out", budget, "embed", "rerank")
        source.with_name("duplicate.json").write_bytes(original)
        with pytest.raises(ValueError, match="Duplicate inspection"):
            budget_local_table_evidence(tmp_path, tmp_path / "fresh", budget, "embed", "rerank")
        assert not (tmp_path / "fresh").exists()
        with pytest.raises(ValueError, match="model limit"):
            budget_local_table_evidence(
                tmp_path,
                tmp_path / "fresh",
                TableTokenBudget(embedding_limit=513, reranker_limit=512, query_reserve=64),
                "embed",
                "rerank",
            )
