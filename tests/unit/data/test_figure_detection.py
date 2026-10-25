from types import SimpleNamespace

import pytest

from steel_slag_slm.data.figure_detection import detect_figures, top_figure_class


def _prediction(name: str, confidence: float) -> SimpleNamespace:
    return SimpleNamespace(class_name=name, confidence=confidence)


def test_top_figure_class_reads_meta_classification() -> None:
    picture = SimpleNamespace(
        meta=SimpleNamespace(
            classification=SimpleNamespace(
                predictions=[_prediction("photograph", 0.2), _prediction("line_chart", 0.7)]
            )
        )
    )
    assert top_figure_class(picture) == ("line_chart", 0.7)


def test_top_figure_class_falls_back_to_older_annotations() -> None:
    picture = SimpleNamespace(
        meta=None,
        annotations=[SimpleNamespace(predicted_classes=[_prediction("bar_chart", 0.9)])],
    )
    assert top_figure_class(picture) == ("bar_chart", 0.9)


def test_top_figure_class_is_empty_when_unclassified() -> None:
    assert top_figure_class(SimpleNamespace(meta=None, annotations=[])) == ("", 0.0)


def test_detect_figures_rejects_empty_pdf_bytes() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        detect_figures(b"", converter=None)  # type: ignore[arg-type]
