"""Budget whole evidence rows without cutting values away from their conditions."""

from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from steel_slag_slm.data.table_evidence import RenderingIssue, TableEvidence
from steel_slag_slm.rag.chunking import passage_for_embedding, passage_for_reranking
from steel_slag_slm.schemas import Chunk


class PassageTokenizer(Protocol):
    """Expose the tokenizer operations needed for exact, untruncated token counts."""

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]: ...

    def num_special_tokens_to_add(self, pair: bool = False) -> int: ...


class TableTokenBudget(BaseModel):
    """Set model input limits and reserve query tokens excluding pair special tokens."""

    embedding_limit: int = Field(gt=0)
    reranker_limit: int = Field(gt=0)
    query_reserve: int = Field(gt=0)

    @model_validator(mode="after")
    def check_reserve(self) -> "TableTokenBudget":
        if self.query_reserve >= self.reranker_limit:
            raise ValueError("Query reserve must be smaller than the reranker input limit")
        return self


class BudgetedRow(BaseModel):
    """Keep the original row/cell mapping and the measured model input sizes."""

    row_index: int
    cell_indices: list[int]
    chunk: Chunk
    embedding_tokens: int
    reranker_reserved_tokens: int


class BudgetedTable(BaseModel):
    """Retain the unreviewed parent evidence and identify oversize rows explicitly."""

    reference: str
    review_status: Literal["unreviewed"] = "unreviewed"
    rows: list[BudgetedRow]
    issues: list[RenderingIssue]


def budget_table_rows(
    table: TableEvidence,
    paper_id: str,
    title: str,
    budget: TableTokenBudget,
    embedding_tokenizer: PassageTokenizer,
    reranker_tokenizer: PassageTokenizer,
    start_position: int = 0,
) -> BudgetedTable:
    """Keep one complete row per chunk, or flag it if either model input cannot fit.

    Title and special tokens count toward both limits. Reranking reserves query tokens
    in addition to pair special tokens. Later queries exceeding that reserve must be
    rejected or re-budgeted, never silently truncated. Parent references stay external
    to the model text. No original evidence text or conditions are shortened.
    """
    if not paper_id.strip() or not title.strip() or start_position < 0:
        raise ValueError("Provide a paper ID, title and nonnegative starting position")
    result = BudgetedTable(reference=table.reference, rows=[], issues=list(table.issues))
    for row in table.rows:
        chunk = Chunk(
            paper_id=paper_id,
            paper_title=title,
            kind="table",
            section="",
            text=row.text,
            position=start_position + len(result.rows),
            pages=tuple(sorted({p.page_no for p in table.provenance})),
        )
        embedding_tokens = len(
            embedding_tokenizer.encode(
                passage_for_embedding(chunk),
                add_special_tokens=True,
            )
        )
        reranker_tokens = (
            len(reranker_tokenizer.encode(passage_for_reranking(chunk), add_special_tokens=False))
            + reranker_tokenizer.num_special_tokens_to_add(pair=True)
            + budget.query_reserve
        )
        if embedding_tokens > budget.embedding_limit or reranker_tokens > budget.reranker_limit:
            result.issues.append(
                RenderingIssue(
                    row_index=row.row_index,
                    reason=f"Whole row exceeds budget: embedding={embedding_tokens}, "
                    f"reranker_with_query_reserve={reranker_tokens}",
                )
            )
            continue
        result.rows.append(
            BudgetedRow(
                row_index=row.row_index,
                cell_indices=row.cell_indices,
                chunk=chunk,
                embedding_tokens=embedding_tokens,
                reranker_reserved_tokens=reranker_tokens,
            )
        )
    return result
