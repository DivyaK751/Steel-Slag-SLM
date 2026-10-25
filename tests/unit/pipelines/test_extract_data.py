from steel_slag_slm.pipelines.extract_data import select_papers


def test_select_papers_skips_papers_that_already_have_rows() -> None:
    assert select_papers(["W1", "W2", "W3"], None, {"W2"}, None) == ["W1", "W3"]


def test_select_papers_re_extracts_requested_papers_that_are_parsed() -> None:
    assert select_papers(["W1", "W2"], ["W2", "W9"], {"W2"}, None) == ["W2"]


def test_select_papers_caps_the_list_at_max_papers() -> None:
    assert select_papers(["W1", "W2", "W3"], None, set(), 2) == ["W1", "W2"]
