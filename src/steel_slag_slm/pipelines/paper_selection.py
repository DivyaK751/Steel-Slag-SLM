"""Pick the papers a command should process by their OpenAlex IDs."""

from pathlib import Path

from steel_slag_slm.exceptions import PapersNotFoundError


def select_papers(paths: list[Path], paper_ids: list[str] | None) -> list[Path]:
    """Keep the paths named after one of paper_ids, e.g. W123.pdf or the folder W123.

    With no paper_ids every path is kept. Order follows paths.

    Raises:
        PapersNotFoundError: If any requested ID matches no path.
    """
    if not paper_ids:
        return paths
    wanted = set(paper_ids)
    selected = [path for path in paths if path.stem in wanted]
    missing = sorted(wanted - {path.stem for path in selected})
    if missing:
        raise PapersNotFoundError(missing)
    return selected
