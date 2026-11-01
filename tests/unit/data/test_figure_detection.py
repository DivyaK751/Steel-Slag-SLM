from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from docling.document_converter import DocumentConverter
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    Size,
)
from docling_core.types.doc.common.reference import ProvenanceItem

from steel_slag_slm.data import figure_detection
from steel_slag_slm.data.figure_detection import (
    detect_figures,
    detect_figures_in_document,
    top_figure_class,
)
from steel_slag_slm.schemas import CaptionBlock, Figure

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


def _layout_document(origin: CoordOrigin) -> DoclingDocument:
    document = DoclingDocument(name="paper")
    document.add_page(page_no=2, size=Size(width=600, height=800))

    picture_box = BoundingBox(
        l=50,
        t=100,
        r=300,
        b=300,
        coord_origin=CoordOrigin.TOPLEFT,
    )
    caption_box = BoundingBox(
        l=50,
        t=310,
        r=300,
        b=330,
        coord_origin=CoordOrigin.TOPLEFT,
    )

    if origin == CoordOrigin.BOTTOMLEFT:
        picture_box = picture_box.to_bottom_left_origin(page_height=800)
        caption_box = caption_box.to_bottom_left_origin(page_height=800)

    document.add_picture(
        prov=ProvenanceItem(
            page_no=2,
            bbox=picture_box,
            charspan=(0, 0),
        )
    )

    caption = "Fig. 2 Strength vs age"
    document.add_text(
        label=DocItemLabel.CAPTION,
        text=caption,
        prov=ProvenanceItem(
            page_no=2,
            bbox=caption_box,
            charspan=(0, len(caption)),
        ),
    )
    document.add_text(
        label=DocItemLabel.TEXT,
        text="Figure 6 shows the results.",
    )
    document.add_text(
        label=DocItemLabel.CAPTION,
        text="Table 1. Results",
    )

    return document


@pytest.mark.parametrize("origin", [CoordOrigin.TOPLEFT, CoordOrigin.BOTTOMLEFT])
def test_detect_figures_in_document_preserves_layout(origin: CoordOrigin) -> None:
    document = _layout_document(origin)
    before = document.model_dump()

    layout = detect_figures_in_document(document)

    assert layout.figures == [
        Figure(
            page_number=2,
            bbox=(50.0, 100.0, 300.0, 300.0),
            figure_type="",
            type_confidence=0.0,
        )
    ]
    assert layout.captions == [
        CaptionBlock(
            page_number=2,
            bbox=(50.0, 310.0, 300.0, 330.0),
            text="Fig. 2 Strength vs age",
            figure_number="2",
        )
    ]
    assert document.model_dump() == before


def test_detect_figures_in_document_returns_empty_layout() -> None:
    layout = detect_figures_in_document(DoclingDocument(name="empty"))

    assert layout.figures == []
    assert layout.captions == []


def test_detect_figures_converts_once_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _layout_document(CoordOrigin.TOPLEFT)
    converter = MagicMock(spec=DocumentConverter)
    converter.convert.return_value.document = document
    detector = MagicMock(wraps=detect_figures_in_document)
    monkeypatch.setattr(figure_detection, "detect_figures_in_document", detector)

    layout = detect_figures(b"%PDF-test", converter)

    converter.convert.assert_called_once()
    detector.assert_called_once_with(document)
    assert layout == detect_figures_in_document(document)