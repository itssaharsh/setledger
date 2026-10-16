"""Setlist report: CSV with what a PRS live-performance report asks for (date, venue, each
work and its duration; writers come from the registered work) + a human PDF cover sheet.

Deterministic by design: rows come only from matched plays; every row carries the
confidence and method that produced it; unregistered tracks are flagged, never hidden.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path

from .scan import Play
from .store import TrackRow

CSV_FIELDS = ["performance_date", "venue", "start_time_s", "end_time_s", "duration_s",
              "title", "artist", "registered", "confidence", "method", "note"]


@dataclass
class PackMeta:
    performance_date: str
    venue: str
    set_name: str


def pack_rows(plays: list[Play], tracks: dict[int, TrackRow], meta: PackMeta) -> list[dict]:
    rows = []
    for p in plays:
        if p.status != "matched" or p.track_id is None:
            continue
        tr = tracks[p.track_id]
        rows.append({
            "performance_date": meta.performance_date,
            "venue": meta.venue,
            "start_time_s": f"{p.t_start:.0f}",
            "end_time_s": f"{p.t_end:.0f}",
            "duration_s": f"{p.t_end - p.t_start:.0f}",
            "title": tr.title,
            "artist": tr.artist,
            "registered": "yes" if tr.registered else "NO - register to claim",
            "confidence": f"{p.confidence:.3f}",
            "method": p.method,
            "note": "" if tr.registered else "unreleased/unregistered track flagged",
        })
    return rows


def to_csv(rows: list[dict]) -> str:
    buf = StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write_pack(plays: list[Play], tracks: dict[int, TrackRow], meta: PackMeta, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = pack_rows(plays, tracks, meta)
    csv_path = out_dir / f"setledger_pack_{meta.performance_date}.csv"
    csv_path.write_text(to_csv(rows))
    pdf_path = out_dir / f"setledger_pack_{meta.performance_date}.pdf"
    _write_pdf(rows, meta, plays, pdf_path)
    return {"csv": str(csv_path), "pdf": str(pdf_path), "rows": len(rows)}


def _write_pdf(rows: list[dict], meta: PackMeta, plays: list[Play], path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm)
    review_n = sum(1 for p in plays if p.status == "review")
    story = [
        Paragraph("SetLedger — setlist report", styles["Title"]),
        Paragraph(f"Performance {meta.performance_date} · {meta.venue} · source set: {meta.set_name}", styles["Normal"]),
        Paragraph(f"{len(rows)} matched plays · {review_n} segments left for manual review · "
                  "every row carries the fingerprint confidence that produced it; no play is "
                  "claimed without evidence.", styles["Normal"]),
        Spacer(1, 6 * mm),
    ]
    data = [CSV_FIELDS] + [[r[f] for f in CSV_FIELDS] for r in rows]
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f5")]),
    ]))
    story.append(t)
    doc.build(story)
