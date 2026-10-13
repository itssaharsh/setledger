import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import json
from setledger.trackgen import TrackSpec, render, write_wav
from setledger.mixgen import build_set
from setledger.index import index_crate
from setledger.store import MemoryStore
from setledger.scan import scan_exact
from setledger.bench import run_bench, table


def test_bench_clean_tier_rate_and_no_foreign_claims(tmp_path):
    crate = tmp_path / "crate"
    crate.mkdir()
    for s in range(5):
        write_wav(crate / f"{s}_Track_{s}.wav", render(TrackSpec(400 + s, f"T{s}", "X", 126, "G"), bars=16))
    plays = [
        {"file": "0_Track_0.wav", "seg_s": 24.0, "shift": 1.0},
        {"file": "1_Track_1.wav", "seg_s": 24.0, "shift": 1.0},
        {"file": None, "seg_s": 20.0, "shift": 1.0},
        {"file": "2_Track_2.wav", "seg_s": 24.0, "shift": 1.0},
        {"file": "3_Track_3.wav", "seg_s": 24.0, "shift": 1.06},
    ]
    out, truthp = tmp_path / "set.wav", tmp_path / "truth.json"
    build_set(crate, out, truthp, plays, crossfade_s=1.5)
    store = MemoryStore()
    index_crate(crate, store)
    results = run_bench(out, truthp, store.all_tracks(), scan_exact)
    print(table(results))
    assert results["tiers"]["clean"]["rate"] >= 0.9, results
    assert results["foreign_claimed"] == 0, "a foreign segment was claimed as a crate track"
    assert results["precision"] >= 0.9, results
    # shifted tier expected to FAIL at exact-only stage; the number is the honest baseline
    assert "shifted" in results["tiers"]
