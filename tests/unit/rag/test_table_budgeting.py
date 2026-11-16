import pytest

from steel_slag_slm.data.table_evidence import EvidenceRow, RenderingIssue, TableEvidence
from steel_slag_slm.rag.table_budgeting import TableTokenBudget, budget_table_rows


class CharacterTokenizer:
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        return [0] * (len(text) + (2 if add_special_tokens else 0))

    def num_special_tokens_to_add(self, pair: bool = False) -> int:
        return 4 if pair else 2


def _table() -> TableEvidence:
    return TableEvidence(
        reference="#/tables/0",
        provenance=[],
        issues=[RenderingIssue(reason="Review needed")],
        rows=[EvidenceRow(row_index=3, cell_indices=[1, 2], text="Mix: 9#\n28 d (MPa): >45.14")],
    )


def test_exact_limits_include_title_query_reserve_and_pair_tokens() -> None:
    table = _table()
    before = table.model_dump_json()
    length = len("Title\n" + table.rows[0].text)
    budget = TableTokenBudget(
        embedding_limit=length + 2, reranker_limit=length + 9, query_reserve=5
    )
    tokenizer = CharacterTokenizer()
    result = budget_table_rows(table, "W123", "Title", budget, tokenizer, tokenizer, 7)
    (row,) = result.rows
    assert row.embedding_tokens == budget.embedding_limit
    assert row.reranker_reserved_tokens == budget.reranker_limit
    assert row.chunk.text == table.rows[0].text and row.chunk.position == 7
    assert row.row_index == 3 and row.cell_indices == [1, 2]
    assert result.issues == table.issues and table.model_dump_json() == before


@pytest.mark.parametrize("limit", ["embedding_limit", "reranker_limit"])
def test_one_token_over_either_limit_is_flagged_not_truncated(limit: str) -> None:
    table = _table()
    length = len("Title\n" + table.rows[0].text)
    limits = dict(embedding_limit=length + 2, reranker_limit=length + 9, query_reserve=5)
    limits[limit] -= 1
    result = budget_table_rows(
        table,
        "W123",
        "Title",
        TableTokenBudget(**limits),
        CharacterTokenizer(),
        CharacterTokenizer(),
    )
    assert not result.rows
    assert result.issues[-1].row_index == 3
    assert "exceeds budget" in result.issues[-1].reason


def test_invalid_reserve_and_missing_title_raise() -> None:
    with pytest.raises(ValueError):
        TableTokenBudget(embedding_limit=10, reranker_limit=10, query_reserve=10)
    with pytest.raises(ValueError, match="title"):
        budget_table_rows(
            _table(),
            "W123",
            "",
            TableTokenBudget(
                embedding_limit=100,
                reranker_limit=100,
                query_reserve=5,
            ),
            CharacterTokenizer(),
            CharacterTokenizer(),
        )
