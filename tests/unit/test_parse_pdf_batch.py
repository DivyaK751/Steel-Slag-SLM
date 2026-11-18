"""Exercise the workflow's handoff decisions without touching Drive or GitHub."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/parse_pdf_batch.sh"
SUMMARY = "Parsing summary (Drive folder _parsed_v3/)\n"
DEFERRED = "      8  Not started yet (beyond --max-papers)\n"
PUBLISHED = "2026-11-18 00:01:00,000 INFO Published W123 with 2 figures (0 read)\n"
SKIPPED = "2026-11-18 00:01:00,000 WARNING Skipped W123: invalid_manifest\n"


@pytest.mark.parametrize(
    ("budget", "attempt", "parse_code", "log", "exit_code", "next_budget"),
    [
        ("12", "1", 0, PUBLISHED + SUMMARY + DEFERRED, 0, "11"),
        ("1", "1", 124, PUBLISHED, 0, "0"),
        ("12", "1", 124, SKIPPED, 0, "11"),
        ("12", "1", 0, SUMMARY, 0, None),
        ("12", "1", 124, "", 1, None),
        ("12", "1", 0, SUMMARY + DEFERRED, 1, None),
        ("12", "1", 0, PUBLISHED, 1, None),
        ("12", "1", 1, PUBLISHED + SUMMARY + DEFERRED, 1, None),
        ("12", "1", 137, PUBLISHED, 1, None),
        ("12", "2", 0, PUBLISHED + SUMMARY + DEFERRED, 1, None),
        ("13", "1", 0, PUBLISHED + SUMMARY + DEFERRED, 1, None),
        ("0", "1", 0, PUBLISHED + SUMMARY + DEFERRED, 0, None),
    ],
)
def test_batch_only_continues_when_requested_and_making_progress(
    tmp_path: Path,
    budget: str,
    attempt: str,
    parse_code: int,
    log: str,
    exit_code: int,
    next_budget: str | None,
) -> None:
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/bash\nprintf "%s" "$PARSE_LOG"\nexit "$PARSE_CODE"\n')
    uv.chmod(0o755)
    timeout = tmp_path / "timeout"
    timeout.write_text(
        '#!/bin/bash\n[[ "$1" == "--kill-after=30s" && "$2" == 150m ]] || exit 99\n'
        'shift 2\nexec "$@"\n'
    )
    timeout.chmod(0o755)
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_REF": "refs/heads/exp/table-corpus-audit",
            "GITHUB_RUN_ATTEMPT": attempt,
            "GITHUB_OUTPUT": str(output),
            "FOLLOW_UP_RUNS": budget,
            "MAX_PAPERS": "1000",
            "PAPER_IDS": "",
            "PARSE_LOG": log,
            "PARSE_CODE": str(parse_code),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    if next_budget is None:
        assert not output.exists()
    else:
        assert output.read_text() == f"follow_up_runs={next_budget}\n"
