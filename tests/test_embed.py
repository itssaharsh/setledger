import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.embed import SpectralEmbedder, index_embeddings, nominate
from setledger.index import index_crate
from setledger.mixgen import build_set
from setledger.scan import scan_full
from setledger.store import MemoryStore
from setledger.trackgen import TrackSpec, render, write_wav

BARS = 32  # ~60 s per track


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    """4-track crate, a 3-play set whose middle play is tempo-shifted +6%."""
    root = tmp_path_factory.mktemp("embed_world")
    crate = root / "crate"
    crate.mkdir()
    files = []
    for i in range(4):
        spec = TrackSpec(seed=5000 + i, title=f"T{i}", artist="A", bpm=122 + 2 * i,
                        key="ACEG"[i])
        write_wav(crate / f"{spec.seed}_T{i}.wav", render(spec, bars=BARS))
        files.append(f"{spec.seed}_T{i}.wav")
    set_wav, truth_json = root / "set.wav", root / "truth.json"
    plays = [{"file": files[0], "seg_s": 40.0, "shift": 1.0},
             {"file": files[1], "seg_s": 40.0, "shift": 1.06},
             {"file": files[2], "seg_s": 35.0, "shift": 1.0}]
    truth = build_set(crate, set_wav, truth_json, plays, crossfade_s=2.0, seed=3)
    store = MemoryStore()
    index_crate(crate, store)
    index_embeddings(crate, store, SpectralEmbedder())
    return {"crate": crate, "set": set_wav, "truth": truth, "store": store,
            "shifted": files[1]}


def test_spectral_embedder_shape_and_norm(world):
    emb = SpectralEmbedder()
    wins = emb.embed_path(world["crate"] / world["shifted"])
    assert wins.ndim == 2 and wins.shape[1] == emb.dim and len(wins) >= 5
    assert np.allclose(np.linalg.norm(wins, axis=1), 1.0, atol=1e-5)


def test_nearest_windows_returns_planted_neighbor(world):
    store = world["store"]
    tr = next(t for t in store.all_tracks() if t.file == world["shifted"])
    q = tr.embedding_windows[2]
    hits = store.nearest_windows(q, k=3)
    assert hits[0][0] == tr.id and hits[0][2] < 1e-6  # itself, distance ~0


def test_nominate_votes_for_true_track(world):
    emb = SpectralEmbedder()
    wins = emb.embed_path(world["crate"] / world["shifted"])
    store = world["store"]
    tr = next(t for t in store.all_tracks() if t.file == world["shifted"])
    assert tr.id in nominate(wins, store)


def test_shifted_play_recovered_via_embedding(world):
    store = world["store"]
    res = scan_full(world["set"], store.all_tracks(), store, SpectralEmbedder(),
                    tmp_dir=world["set"].parent / ".tmp_scan")
    assert not res.degraded
    emb_plays = [p for p in res.plays if p.method == "embedding"]
    assert emb_plays, f"no embedding claims; plays={res.plays} nominated={res.nominated}"
    assert all(p.track_file == world["shifted"] for p in emb_plays)
    truth_seg = next(t for t in world["truth"] if t.track_file == world["shifted"])
    covered = sum(max(0.0, min(p.t_end, truth_seg.t_end) - max(p.t_start, truth_seg.t_start))
                  for p in res.plays if p.track_file == world["shifted"])
    dur = truth_seg.t_end - truth_seg.t_start
    assert covered / dur >= 0.5, f"coverage {covered / dur:.2f} of shifted play"
    # the recall layer must not have invented anything: every claim is a crate track
    assert all(p.track_file is not None for p in res.plays if p.status == "matched")
