import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.trackgen import TrackSpec, render, write_wav, make_crate
from setledger.mixgen import build_set, read_wav, SR


def test_render_deterministic_and_distinct(tmp_path):
    a1 = render(TrackSpec(1, "A", "X", 124, "C"), bars=4)
    a2 = render(TrackSpec(1, "A", "X", 124, "C"), bars=4)
    b = render(TrackSpec(2, "B", "X", 124, "C"), bars=4)
    assert (a1 == a2).all()
    assert a1.shape[1] == 2 and len(a1) > SR * 5
    assert not (a1[: len(b)] == b[: len(a1)]).all()


def test_build_set_truth_boundaries(tmp_path):
    crate = tmp_path / "crate"
    crate.mkdir()
    for s in range(3):
        write_wav(crate / f"t{s}.wav", render(TrackSpec(100 + s, f"T{s}", "X", 126, "D"), bars=12))
    plays = [
        {"file": "t0.wav", "seg_s": 10.0, "shift": 1.0},
        {"file": None, "seg_s": 8.0, "shift": 1.0},
        {"file": "t1.wav", "seg_s": 10.0, "shift": 1.06},
    ]
    out = tmp_path / "set.wav"
    truthp = tmp_path / "truth.json"
    truth = build_set(crate, out, truthp, plays, crossfade_s=1.0)
    data = json.loads(truthp.read_text())
    assert len(data) == 3
    assert data[1]["track_file"] is None
    assert data[2]["tempo_shift"] == 1.06
    audio = read_wav(out)
    assert abs(len(audio) / SR - truth[-1].t_end) < 0.02
    # segments overlap only by the crossfade
    assert abs((data[1]["t_start"]) - (data[0]["t_end"] - 1.0)) < 0.02
