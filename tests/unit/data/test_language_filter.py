import pytest

from steel_slag_slm.data.language_filter import non_english_reason
from steel_slag_slm.schemas import PaperForScreening

ENGLISH_ABSTRACT = (
    "Basic oxygen furnace slag was ground and used to replace 20% of the cement in mortars. "
    "The compressive strength at 28 days and the heat of hydration were measured."
)


def _paper(title: str, abstract: str = "", keywords: tuple[str, ...] = ()) -> PaperForScreening:
    return PaperForScreening("W1", title, abstract, 2020, keywords)


def test_english_paper_with_stray_symbols_passes() -> None:
    paper = _paper("Hydration of BOF slag at 20 ℃", ENGLISH_ABSTRACT + " Particles were 10 µm.")
    assert non_english_reason(paper) is None


def test_korean_abstract_is_caught_by_script() -> None:
    abstract = "전기로 산화슬래그를 콘크리트용 잔골재로 사용하기 위해서는 에이징처리하여야 한다"
    reason = non_english_reason(_paper("Use of EAF slag as fine aggregate", abstract))
    assert reason is not None and "Hangul" in reason


def test_latin_script_abstract_is_caught_by_langdetect() -> None:
    abstract = (
        "La escoria de acería es un residuo sólido generado durante el proceso de fabricación "
        "del acero, y en este trabajo se estudia su uso como conglomerante activado."
    )
    assert (
        non_english_reason(_paper("Steel slag binder", abstract)) == "abstract is in language 'es'"
    )


@pytest.mark.parametrize("title", ["PEMANFAATAN SLAG PADA BETON", "BOF slag cement"])
def test_titles_alone_are_never_judged_by_langdetect(title: str) -> None:
    # Short or ALL-CAPS text fools langdetect, so only the prompt rule handles these
    assert non_english_reason(_paper(title)) is None
