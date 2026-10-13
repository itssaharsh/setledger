import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import numpy as np
from setledger.export import PackMeta, pack_rows, to_csv, write_pack
from setledger.scan import Play
from setledger.store import TrackRow

TRACKS = {
    1: TrackRow(1, "a.wav", "Midnight Harbour", "Kolkata Transit", 70.0, np.zeros(10, np.uint32), registered=True),
    2: TrackRow(2, "b.wav", "Static Circuit", "Low Orbit Club", 70.0, np.zeros(10, np.uint32), registered=False),
}
PLAYS = [
    Play(1, "a.wav", 0.0, 61.0, 0.953, "exact", "matched"),
    Play(None, None, 61.0, 80.0, 0.0, "none", "review"),
    Play(2, "b.wav", 80.0, 142.0, 0.871, "embedding", "matched"),
]
META = PackMeta("2026-10-13", "Demo Venue", "demo_set.wav")

GOLDEN = """performance_date,venue,start_time_s,end_time_s,duration_s,title,artist,registered,confidence,method,note
2026-10-13,Demo Venue,0,61,61,Midnight Harbour,Kolkata Transit,yes,0.953,exact,
2026-10-13,Demo Venue,80,142,62,Static Circuit,Low Orbit Club,NO - register to claim,0.871,embedding,unreleased/unregistered track flagged
"""


def test_rows_equal_matched_plays_and_golden_csv():
    rows = pack_rows(PLAYS, TRACKS, META)
    assert len(rows) == 2  # review segment NOT exported as a claim
    assert to_csv(rows) == GOLDEN


def test_write_pack_pdf_and_csv(tmp_path):
    out = write_pack(PLAYS, TRACKS, META, tmp_path)
    assert out["rows"] == 2
    assert Path(out["csv"]).exists() and Path(out["pdf"]).stat().st_size > 1000
