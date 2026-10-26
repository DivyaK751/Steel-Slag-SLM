"""Write an HTML page that puts each figure beside its reading, checks and cost, for review."""

import base64
import html
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from steel_slag_slm.evaluation.figure_checks import CheckTotals, FigureCheck
from steel_slag_slm.schemas import FigureReading

# Thumbnails keep the page small enough to open and share; the full PNGs stay on disk
_THUMBNAIL_MAX_PX = 520
_THUMBNAIL_JPEG_QUALITY = 82


@dataclass(frozen=True, slots=True)
class ReviewRow:
    """One figure on the review page."""

    heading: str  # e.g. "W2114885577 · Fig. 3"
    image_path: Path
    caption: str
    reading: FigureReading | None  # None when the model call failed
    check: FigureCheck | None
    cost_usd: float
    error: str | None = None


_STYLE = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#666;--card:#f5f5f7;--ok:#1a7f37;--warn:#b35900;--bad:#c62828}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161618;--fg:#eee;
--muted:#aaa;--card:#222226;--ok:#4cc26b;--warn:#f0a040;--bad:#ff6b6b}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;
max-width:1200px;margin:auto;padding:16px}
h2{margin-top:32px}.summary{background:var(--card);border-radius:8px;padding:10px 14px}
.row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;background:var(--card);
border-radius:8px;padding:12px;margin:12px 0}
@media (max-width:700px){.row{grid-template-columns:1fr}}
img{width:100%;background:#fff;border-radius:4px}.muted{color:var(--muted)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
table{border-collapse:collapse;font-size:12px}td,th{border-bottom:1px solid #8884;padding:2px 6px;
text-align:left}code{font-size:12px}
"""


def _thumbnail_data_uri(image_path: Path) -> str:
    image = Image.open(image_path).convert("RGB")
    image.thumbnail((_THUMBNAIL_MAX_PX, _THUMBNAIL_MAX_PX))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=_THUMBNAIL_JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def _check_html(check: FigureCheck | None) -> str:
    if check is None:
        return ""
    if not check.has_pdf_text:
        labels = "<span class=muted>➖ labels not checkable (figure is a picture)</span>"
    elif check.unsupported_labels:
        missing = ", ".join(html.escape(label) for label in check.unsupported_labels)
        labels = f"<span class=warn>⚠️ not in PDF text: {missing}</span>"
    else:
        labels = f"<span class=ok>✅ all {check.labels_checked} labels found in PDF text</span>"
    if check.values_out_of_range:
        values = (
            f"<span class=warn>⚠️ {check.values_out_of_range} of {check.values_checked} "
            "values outside the axes</span>"
        )
    elif check.values_checked:
        values = f"<span class=ok>✅ {check.values_checked} values inside the axes</span>"
    else:
        values = ""
    if check.rows_malformed:
        values += f"<br><span class=warn>⚠️ {check.rows_malformed} rows with missing cells</span>"
    return f"<p>{labels}<br>{values}</p>"


def _reading_html(reading: FigureReading) -> str:
    parts = [f"<p><b>{reading.figure_type}</b>: {html.escape(reading.summary)}</p>"]
    if reading.columns:
        header = "".join(
            f"<th>{html.escape(column.label)}"
            f"{f' [{html.escape(column.unit)}]' if column.unit else ''}"
            f"<br><span class=muted>{column.source}</span></th>"
            for column in reading.columns
        )
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
            for row in reading.rows
        )
        parts.append(f"<table><tr>{header}</tr>{body}</table>")
    if reading.phases:
        parts.append(f"<div>phases: {html.escape(', '.join(reading.phases))}</div>")
    for step in reading.steps:
        conditions = f" ({html.escape('; '.join(step.conditions))})" if step.conditions else ""
        parts.append(f"<div>{step.id}: {html.escape(step.label)}{conditions}</div>")
    for flow in reading.flows:
        label = f" [{html.escape(flow.label)}]" if flow.label else ""
        parts.append(f"<div>{flow.source} → {flow.target}{label}</div>")
    return "".join(parts)


def write_review_page(
    rows: list[ReviewRow], summary_lines: list[str], checks: CheckTotals, path: Path
) -> None:
    """Write the review page to path, creating its folder; summary_lines head the page."""
    sections = [
        "<div class=summary>"
        + "<br>".join(html.escape(line) for line in summary_lines)
        + f"<br>Labels not in PDF text: {checks.labels_unsupported} of {checks.labels_checked}"
        + f" · values outside axes: {checks.values_out_of_range} of {checks.values_checked}"
        + f" · rows with missing cells: {checks.rows_malformed}"
        + "</div>"
    ]
    for row in rows:
        if row.error:
            body = f"<p class=bad>❌ {html.escape(row.error)}</p>"
        else:
            body = _reading_html(row.reading) if row.reading else ""
        sections.append(
            f"<div class=row><div><img src='{_thumbnail_data_uri(row.image_path)}'>"
            f"<p class=muted><b>{html.escape(row.heading)}</b><br>{html.escape(row.caption)}</p>"
            f"</div><div>{body}{_check_html(row.check)}"
            f"<p class=muted>${row.cost_usd:.4f}</p></div></div>"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Figure readings</title><style>{_STYLE}</style></head><body>"
        "<h1>Figure readings</h1>" + "".join(sections) + "</body></html>",
        encoding="utf-8",
    )
