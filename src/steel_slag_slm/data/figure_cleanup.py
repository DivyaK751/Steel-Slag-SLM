"""Fix Docling's figure regions: split merged figures, match captions, drop non-figures."""

import re
from dataclasses import replace

from steel_slag_slm.config import FigureExtractionSettings
from steel_slag_slm.schemas import CaptionBlock, Figure, PageBox

# "Fig. 3", "Figure 3a:", "FIGURE 12." at the start of a block; "Fig. 3 shows" mid-sentence is
# excluded by requiring the block to start with it
_CAPTION_START = re.compile(r"^\s*(?:fig(?:ure)?\.?)\s*(\d+[a-z]?)\b", re.IGNORECASE)
# Rounding slack when comparing edges, in PDF points
_EDGE_TOLERANCE_PT = 2.0


def parse_figure_number(text: str) -> str | None:
    """Return the figure number a caption starts with, e.g. "3a" for "Figure 3a: ...", or None."""
    match = _CAPTION_START.match(text)
    return match.group(1).lower() if match else None


def _overlaps_horizontally(first: PageBox, second: PageBox) -> bool:
    return min(first[2], second[2]) - max(first[0], second[0]) > 0


def _contains(outer: PageBox, inner: PageBox) -> bool:
    return (
        inner[0] >= outer[0] - _EDGE_TOLERANCE_PT
        and inner[1] >= outer[1] - _EDGE_TOLERANCE_PT
        and inner[2] <= outer[2] + _EDGE_TOLERANCE_PT
        and inner[3] <= outer[3] + _EDGE_TOLERANCE_PT
    )


def split_merged_figures(
    figures: list[Figure], captions: list[CaptionBlock], min_part_height_pt: float
) -> list[Figure]:
    """Split each region at any caption printed inside it, so stacked figures come apart.

    Docling sometimes returns two figures stacked one above the other as one region, with the
    upper figure's caption between them. A split is made only when both parts are tall enough
    to be figures themselves.
    """
    result: list[Figure] = []
    for figure in figures:
        same_page = sorted(
            (caption for caption in captions if caption.page_number == figure.page_number),
            key=lambda caption: caption.bbox[1],
        )
        parts = [figure]
        for caption in same_page:
            current = parts[-1]
            if not _contains(current.bbox, caption.bbox):
                continue
            upper_height = caption.bbox[1] - current.bbox[1]
            lower_height = current.bbox[3] - caption.bbox[3]
            if upper_height < min_part_height_pt or lower_height < min_part_height_pt:
                continue
            x0, y0, x1, y1 = current.bbox
            # The caption itself is left out of both parts; it is matched back to the upper one
            parts[-1] = replace(current, bbox=(x0, y0, x1, caption.bbox[1]))
            parts.append(replace(current, bbox=(x0, caption.bbox[3], x1, y1)))
        result.extend(parts)
    return result


def _caption_gap(figure: Figure, caption: CaptionBlock) -> float | None:
    """Vertical distance from a figure to a caption beside it, or None if it can't be its own."""
    if caption.page_number != figure.page_number:
        return None
    if not _overlaps_horizontally(figure.bbox, caption.bbox):
        return None
    below = caption.bbox[1] - figure.bbox[3]
    above = figure.bbox[1] - caption.bbox[3]
    gap = below if below >= -_EDGE_TOLERANCE_PT else above
    return max(gap, 0.0) if gap >= -_EDGE_TOLERANCE_PT else None


def match_captions(
    figures: list[Figure], captions: list[CaptionBlock], max_gap_pt: float
) -> list[Figure]:
    """Give each figure the nearest caption directly above or below it, each caption used once.

    Pairs are taken closest first, so a caption between two figures goes to the nearer one.
    Figures left without a caption keep an empty caption and no figure number.
    """
    candidates = sorted(
        (gap, figure_index, caption_index)
        for figure_index, figure in enumerate(figures)
        for caption_index, caption in enumerate(captions)
        if (gap := _caption_gap(figure, caption)) is not None and gap <= max_gap_pt
    )
    caption_for_figure: dict[int, CaptionBlock] = {}
    used_captions: set[int] = set()
    for _, figure_index, caption_index in candidates:
        if figure_index in caption_for_figure or caption_index in used_captions:
            continue
        caption_for_figure[figure_index] = captions[caption_index]
        used_captions.add(caption_index)

    return [
        replace(figure, caption=caption.text, figure_number=caption.figure_number)
        if (caption := caption_for_figure.get(index))
        else replace(figure, caption="", figure_number=None)
        for index, figure in enumerate(figures)
    ]


def is_kept(figure: Figure, settings: FigureExtractionSettings) -> bool:
    """Whether a region is a real figure rather than a logo, icon or uncaptioned decoration."""
    width = figure.bbox[2] - figure.bbox[0]
    height = figure.bbox[3] - figure.bbox[1]
    if min(width, height) < settings.min_side_pt:
        return False
    if figure.figure_type in settings.drop_types:
        return False
    return bool(figure.caption) or settings.keep_uncaptioned


def clean_figures(
    figures: list[Figure], captions: list[CaptionBlock], settings: FigureExtractionSettings
) -> list[Figure]:
    """Split merged regions, match captions and drop non-figures, in reading order."""
    split = split_merged_figures(figures, captions, settings.min_split_height_pt)
    matched = match_captions(split, captions, settings.caption_max_gap_pt)
    kept = [figure for figure in matched if is_kept(figure, settings)]
    return sorted(kept, key=lambda figure: (figure.page_number, figure.bbox[1], figure.bbox[0]))
