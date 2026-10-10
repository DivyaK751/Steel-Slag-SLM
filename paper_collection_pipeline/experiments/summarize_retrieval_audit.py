"""Summarize reviewer labels from the retrieval-audit CSV."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from paper_collection_pipeline.experiments.retrieval_audit import (
    LABELS,
    OUTPUT_DIR,
)


def summarize_labels(sample_path: Path) -> dict[str, Any]:
    """Report exploratory label yields grouped by query profile."""
    profile_counts: dict[str, Counter[str]] = defaultdict(Counter)
    labeled_rows = 0
    unlabeled_rows = 0

    with sample_path.open("r", newline="", encoding="utf-8") as csv_file:
        for row in csv.DictReader(csv_file):
            label = (row.get("human_label") or "").strip()
            if not label:
                unlabeled_rows += 1
                continue
            if label not in LABELS:
                raise ValueError(
                    f"Invalid label {label!r} for {row.get('openalex_id')}; "
                    f"use one of {sorted(LABELS)}."
                )

            labeled_rows += 1
            profiles = (row.get("matched_profiles") or "").split(";")
            for profile in filter(None, (item.strip() for item in profiles)):
                profile_counts[profile][label] += 1

    by_profile: dict[str, dict[str, Any]] = {}
    for profile, counts in sorted(profile_counts.items()):
        total = sum(counts.values())
        by_profile[profile] = {
            "labeled_sample_count": total,
            "yes": counts["Yes"],
            "no": counts["No"],
            "unclear": counts["Unclear"],
            "yes_share_of_labeled_sample": counts["Yes"] / total if total else None,
        }

    return {
        "labeled_unique_works": labeled_rows,
        "unlabeled_unique_works": unlabeled_rows,
        "profiles": by_profile,
        "interpretation_note": (
            "These are exploratory shares for a top/middle/late diagnostic sample. "
            "They are not population precision or recall estimates. Use a separately "
            "labeled calibration set for formal classifier precision/recall/F1."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=OUTPUT_DIR / "retrieval_audit_sample.csv",
        help="Reviewed audit CSV (defaults to the experiment output).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "retrieval_audit_labels_summary.json",
        help="Where to write the label summary JSON.",
    )
    args = parser.parse_args()

    summary = summarize_labels(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Label summary: {args.output}")
    for profile, stats in summary["profiles"].items():
        print(
            f"{profile}: Yes={stats['yes']}, No={stats['no']}, "
            f"Unclear={stats['unclear']} "
            f"(n={stats['labeled_sample_count']})"
        )


if __name__ == "__main__":
    main()