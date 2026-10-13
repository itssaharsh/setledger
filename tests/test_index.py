import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import numpy as np
from setledger.trackgen import TrackSpec, render, write_wav
from setledger.index import index_crate
from setledger.store import MemoryStore


def make_small_crate(tmp_path, n=3):
    crate = tmp_path / "crate"
    crate.mkdir()
    for s in range(n):
        write_wav(crate / f"{s}_Track_{s}.wav", render(TrackSpec(200 + s, f"T{s}", "X", 124, "E"), bars=8))
    return crate


def test_index_crate_and_idempotent(tmp_path):
    crate = make_small_crate(tmp_path)
    store = MemoryStore()
    r1 = index_crate(crate, store)
    assert len(r1.indexed) == 3 and not r1.skipped
    tracks = store.all_tracks()
    assert all(len(t.fingerprint) > 50 for t in tracks)
    r2 = index_crate(crate, store)
    assert store.count() == 3, "re-index must not duplicate"
    assert len(r2.indexed) == 3


def test_corrupt_file_skipped_not_fatal(tmp_path):
    crate = make_small_crate(tmp_path, n=2)
    (crate / "broken.wav").write_bytes(b"not audio at all")
    store = MemoryStore()
    r = index_crate(crate, store)
    assert len(r.indexed) == 2
    assert len(r.skipped) == 1 and r.skipped[0][0] == "broken.wav"


def test_fingerprints_differ_between_tracks(tmp_path):
    crate = make_small_crate(tmp_path)
    store = MemoryStore()
    index_crate(crate, store)
    a, b, _ = store.all_tracks()
    n = min(len(a.fingerprint), len(b.fingerprint))
    assert not np.array_equal(a.fingerprint[:n], b.fingerprint[:n])
