from steel_slag_slm.evaluation.metrics import FAILED, compute_metrics, normalize_label


def test_compute_metrics_counts_agreement_precision_and_recall() -> None:
    pairs = [("Yes", "Yes"), ("Yes", "No"), ("No", "No"), ("No", "Yes"), ("Unclear", FAILED)]
    metrics = compute_metrics(pairs)
    assert (metrics.labelled, metrics.matched) == (5, 2)
    assert metrics.accuracy == 0.4
    yes = metrics.per_label[0]
    # Agent said Yes twice (1 right); human said Yes twice (agent caught 1)
    assert (yes.agent_said, yes.agent_correct, yes.human_said, yes.agent_caught) == (2, 1, 2, 1)
    assert metrics.confusion[("Unclear", FAILED)] == 1


def test_compute_metrics_reports_none_when_nothing_to_divide() -> None:
    metrics = compute_metrics([])
    assert metrics.accuracy is None
    assert metrics.per_label[2].precision is None


def test_normalize_label_accepts_case_and_spaces_but_rejects_other_text() -> None:
    assert normalize_label(" yes ") == "Yes"
    assert normalize_label("UNCLEAR") == "Unclear"
    assert normalize_label("maybe") is None
    assert normalize_label(None) is None
