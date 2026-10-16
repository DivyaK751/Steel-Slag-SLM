"""Compare the agent's labels with human labels."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

LABELS = ("Yes", "No", "Unclear")
# Shown in place of a label when the agent gave no usable answer
FAILED = "Failed"


@dataclass(frozen=True, slots=True)
class LabelStats:
    """How the agent did on one label."""

    label: str
    agent_said: int  # papers the agent gave this label
    agent_correct: int  # ... of which the human agrees
    human_said: int  # papers the human gave this label
    agent_caught: int  # ... of which the agent also gave it

    @property
    def precision(self) -> float | None:
        return self.agent_correct / self.agent_said if self.agent_said else None

    @property
    def recall(self) -> float | None:
        return self.agent_caught / self.human_said if self.human_said else None


@dataclass(frozen=True, slots=True)
class EvalMetrics:
    """Agreement between agent and human over the papers that have a human label."""

    labelled: int
    matched: int
    per_label: tuple[LabelStats, ...]
    # (human label, agent label) -> number of papers
    confusion: dict[tuple[str, str], int]

    @property
    def accuracy(self) -> float | None:
        return self.matched / self.labelled if self.labelled else None


def compute_metrics(pairs: Sequence[tuple[str, str]]) -> EvalMetrics:
    """Score (human label, agent label) pairs; agent label may be FAILED."""
    confusion = Counter(pairs)
    per_label = tuple(
        LabelStats(
            label=label,
            agent_said=sum(1 for _, agent in pairs if agent == label),
            agent_correct=confusion[(label, label)],
            human_said=sum(1 for human, _ in pairs if human == label),
            agent_caught=confusion[(label, label)],
        )
        for label in LABELS
    )
    return EvalMetrics(
        labelled=len(pairs),
        matched=sum(1 for human, agent in pairs if human == agent),
        per_label=per_label,
        confusion=dict(confusion),
    )


def normalize_label(value: str | None) -> str | None:
    """Turn ' yes ' or 'YES' into 'Yes'; None for blanks or anything that isn't a label."""
    cleaned = (value or "").strip().capitalize()
    return cleaned if cleaned in LABELS else None
