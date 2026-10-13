import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.scan import scan_full
from setledger.index import index_crate
from setledger.mixgen import build_set
from setledger.store import MemoryStore
from setledger.trackgen import TrackSpec, render, write_wav


class DownEmbedder:
    dim = 64

    def available(self):
        return False

    def embed_path(self, path, **kw):  # pragma: no cover - must never be called
        raise AssertionError("embed_path called while unavailable")


def _tiny_world(tmp_path):
    crate = tmp_path / "crate"
    crate.mkdir()
    spec = TrackSpec(seed=7000, title="Solo", artist="A", bpm=124, key="A")
    write_wav(crate / "7000_Solo.wav", render(spec, bars=24))
    set_wav = tmp_path / "set.wav"
    build_set(crate, set_wav, tmp_path / "truth.json",
              [{"file": "7000_Solo.wav", "seg_s": 30.0, "shift": 1.0},
               {"file": None, "seg_s": 25.0, "shift": 1.0}],
              crossfade_s=2.0, seed=5)
    store = MemoryStore()
    index_crate(crate, store)
    return set_wav, store


def test_no_embedder_is_degraded_exact_only(tmp_path):
    set_wav, store = _tiny_world(tmp_path)
    res = scan_full(set_wav, store.all_tracks(), store, embedder=None)
    assert res.degraded
    assert all(p.method != "embedding" for p in res.plays)
    # the foreign segment stays review, never claimed
    assert any(p.status == "review" for p in res.plays)


def test_unavailable_embedder_is_degraded(tmp_path):
    set_wav, store = _tiny_world(tmp_path)
    res = scan_full(set_wav, store.all_tracks(), store, embedder=DownEmbedder())
    assert res.degraded
    assert all(p.method != "embedding" for p in res.plays)
