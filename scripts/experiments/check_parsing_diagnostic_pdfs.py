"""Check that all configured parsing-diagnostic source PDFs are present locally."""

import argparse
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_DIR = PROJECT_ROOT / "data/interim/pdf_parsing/parsing_diagnostics/pdfs"
CONFIG_DIR = PROJECT_ROOT / "configs"


def _evaluation_paper_ids(path: Path) -> set[str]:
    """Collect paper IDs named by the evidence in a retrieval evaluation set."""
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        evidence["paper_id"]
        for question in config["questions"]
        for evidence in question["evidence"]
    }


def required_paper_ids() -> set[str]:
    """Collect the extraction-dev and retrieval tuning/held-out paper IDs."""
    benchmark = yaml.safe_load(
        (CONFIG_DIR / "extraction_benchmark.yaml").read_text(encoding="utf-8")
    )
    dev_ids = set(benchmark["dev"])
    tuning_ids = _evaluation_paper_ids(CONFIG_DIR / "retrieval_eval_human50.yaml")
    heldout_ids = _evaluation_paper_ids(CONFIG_DIR / "retrieval_eval_heldout.yaml")
    return dev_ids | tuning_ids | heldout_ids


def main() -> int:
    """Report any missing PDFs and return a nonzero status when files are absent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=PDF_DIR)
    args = parser.parse_args()

    paper_ids = sorted(required_paper_ids())
    missing = [
        paper_id for paper_id in paper_ids if not (args.pdf_dir / f"{paper_id}.pdf").is_file()
    ]

    print(f"Required PDFs: {len(paper_ids)}")
    print(f"PDF folder: {args.pdf_dir}")
    if missing:
        print(f"Missing PDFs: {len(missing)}")
        for paper_id in missing:
            print(f"  {paper_id}.pdf")
        return 1

    print("All required PDFs are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
