from steel_slag_slm.data.search_progress import format_progress, parse_progress


def test_progress_round_trips_queries_with_quotes_and_spaces() -> None:
    progress = {'"steel slag" cement': 11, '"BOF slag"': 1}
    assert parse_progress(format_progress(progress)) == progress


def test_parse_progress_ignores_blank_and_malformed_lines() -> None:
    text = '"EAF slag"\t6\n\nno tab here\n"LD slag"\tnot-a-number\n'
    assert parse_progress(text) == {'"EAF slag"': 6}
