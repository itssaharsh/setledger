import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.trackgen import TrackSpec, render, write_wav
from setledger.mixgen import build_set
from setledger.index import index_crate
from setledger.store import MemoryStore
from setledger.scan import scan_exact


def setup_world(tmp_path, plays):
    crate = tmp_path / "crate"
    crate.mkdir()
    for s in range(4):
        write_wav(crate / f"{s}_Track_{s}.wav", render(TrackSpec(300 + s, f"T{s}", "X", 125, "F"), bars=16))
    out = tmp_path / "set.wav"
    truth = tmp_path / "truth.json"
    build_set(crate, out, truth, plays, crossfade_s=1.5)
    store = MemoryStore()
    index_crate(crate, store)
    return out, json.loads(truth.read_text()), store


def test_exact_scan_finds_clean_plays_with_timestamps(tmp_path):
    plays = [
        {"file": "0_Track_0.wav", "seg_s": 25.0, "shift": 1.0},
        {"file": "2_Track_2.wav", "seg_s": 25.0, "shift": 1.0},
    ]
    set_path, truth, store = setup_world(tmp_path, plays)
    found = scan_exact(set_path, store.all_tracks())
    matched = [p for p in found if p.status == "matched"]
    assert [p.track_file for p in matched] == ["0_Track_0.wav", "2_Track_2.wav"]
    for p, t in zip(matched, truth):
        assert abs(p.t_start - t["t_start"]) <= 5.0  # crossfade zones are blended audio; conservative trims are correct
        assert abs(p.t_end - t["t_end"]) <= 5.0
        assert p.confidence > 0.75
        assert p.method == "exact"


def test_unmatched_never_invented(tmp_path):
    plays = [
        {"file": "1_Track_1.wav", "seg_s": 25.0, "shift": 1.0},
        {"file": None, "seg_s": 22.0, "shift": 1.0},   # foreign track, not in crate
        {"file": "3_Track_3.wav", "seg_s": 25.0, "shift": 1.0},
    ]
    set_path, truth, store = setup_world(tmp_path, plays)
    found = scan_exact(set_path, store.all_tracks())
    matched = [p for p in found if p.status == "matched"]
    review = [p for p in found if p.status == "review"]
    assert [p.track_file for p in matched] == ["1_Track_1.wav", "3_Track_3.wav"]
    # the foreign play's span must be covered by a review segment, not a crate track
    f = truth[1]
    assert any(r.t_start <= f["t_start"] + 3 and r.t_end >= f["t_end"] - 3 for r in review), \
        f"foreign segment {f} not represented as review: {review}"
    assert all(p.track_id is not None for p in matched)
    assert all(p.track_id is None for p in review)


def test_merge_fragments_joins_one_shifted_play():
    from setledger.scan import Play, merge_fragments
    pieces = [Play(5, "x.wav", 373.8, 393.8, 0.88, "embedding", "matched"),
              Play(5, "x.wav", 396.5, 418.9, 0.80, "exact", "matched"),
              Play(5, "x.wav", 418.9, 431.8, 0.79, "embedding", "matched")]
    [m] = merge_fragments(pieces)
    assert (m.t_start, m.t_end, m.fragments) == (373.8, 431.8, 3)
    assert m.method == "embedding" and m.confidence == 0.79   # weakest evidence wins


def test_merge_fragments_keeps_distinct_plays_apart():
    from setledger.scan import Play, merge_fragments
    a = Play(1, "a.wav", 0.0, 50.0, 0.95, "exact", "matched")
    b = Play(2, "b.wav", 50.5, 100.0, 0.95, "exact", "matched")        # different track
    a2 = Play(1, "a.wav", 140.0, 190.0, 0.95, "exact", "matched")      # same track, far later
    out = merge_fragments([a2, b, a])
    assert [(p.track_file, p.fragments) for p in out] == [("a.wav", 1), ("b.wav", 1), ("a.wav", 1)]
    assert all(p.method == "exact" for p in out)
