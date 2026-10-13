#!/usr/bin/env python3
"""Full CLI slice on the demo world: index -> scan (exact, then + embedding recall)
-> bench -> timeline PNGs -> pack. Embeddings cache to data/ so re-runs are fast.

SETLEDGER_EMBEDDER=clap|spectral|none picks the recall driver (default: clap if its
stack loads, else spectral; 'none' demonstrates degraded exact-only mode)."""
import json, os, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.index import index_crate
from setledger.store import MemoryStore
from setledger.scan import scan_exact, scan_full
from setledger.bench import table, score
from setledger.export import PackMeta, write_pack
from setledger.embed import ClapEmbedder, SpectralEmbedder

root = Path(__file__).parent.parent
media = root / "docs" / "media"; media.mkdir(parents=True, exist_ok=True)
store = MemoryStore()
t0 = time.time()
rep = index_crate(root / "data/crate", store, registered_files={"1000_Midnight_Harbour.wav"})
print(f"indexed {len(rep.indexed)} tracks ({time.time()-t0:.1f}s), skipped {len(rep.skipped)}")
set_path = root / "data/mixes/demo_set.wav"
truth = json.loads((root / "data/mixes/demo_set.truth.json").read_text())
set_dur = truth[-1]["t_end"]


def pick_embedder():
    want = os.environ.get("SETLEDGER_EMBEDDER", "auto")
    if want == "none":
        return None, "none"
    if want in ("clap", "auto"):
        clap = ClapEmbedder()
        if clap.available():
            return clap, "clap"
        if want == "clap":
            print("WARN clap unavailable, falling back to spectral")
    return SpectralEmbedder(), "spectral"


def cached_embeddings(embedder, name):
    cache = root / "data" / f"crate_embeddings_{name}.npz"
    tracks = store.all_tracks()
    if cache.exists():
        z = np.load(cache)
        if set(z.files) == {t.file for t in tracks}:
            for t in tracks:
                store.set_embeddings(t.id, z[t.file])
            print(f"embeddings: cache hit ({name})")
            return
    t0 = time.time()
    arrays = {}
    for i, t in enumerate(tracks):
        wins = embedder.embed_path(root / "data/crate" / t.file)
        store.set_embeddings(t.id, wins)
        arrays[t.file] = wins
        print(f"  embed {i+1}/{len(tracks)} {t.file}", flush=True)
    np.savez_compressed(cache, **arrays)
    print(f"embeddings: {sum(len(a) for a in arrays.values())} windows ({time.time()-t0:.0f}s) -> {cache.name}")


def timeline_png(plays, out, title):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 2.6))
    for seg in truth:
        ax.axvspan(seg["t_start"], seg["t_end"], ymin=0.55, ymax=0.95,
                   color="#cfd8ea" if seg["track_file"] else "#f3d9d4", alpha=0.9)
    for p in plays:
        c = {"matched": {"exact": "#2e7d32", "embedding": "#1e5fa8"}.get(p.method, "#2e7d32"),
             "review": "#f2a93b"}[p.status]
        ax.axvspan(p.t_start, p.t_end, ymin=0.08, ymax=0.48, color=c, alpha=0.85)
        if p.status == "matched":
            ax.text((p.t_start+p.t_end)/2, 0.28, f"{p.confidence:.2f}", ha="center",
                    va="center", fontsize=7, color="white")
    ax.set_xlim(0, set_dur); ax.set_ylim(0, 1)
    ax.set_yticks([0.28, 0.75]); ax.set_yticklabels(["SetLedger", "truth"])
    ax.set_xlabel("seconds"); ax.set_title(title)
    fig.tight_layout(); fig.savefig(media / out, dpi=150)


def report(plays, label, scan_s, fname, png, title):
    print(f"[{label}] scan: {scan_s:.1f}s")
    for p in plays:
        print(f"  {p.t_start:7.1f}-{p.t_end:7.1f}  {p.status:8s} {p.method:9s} "
              f"conf={p.confidence:.3f}  {p.track_file or '— review manually —'}")
    tbl = table(score(truth, plays))
    print(tbl)
    (media / fname).write_text(f"scan {scan_s:.1f}s on {set_dur:.0f}s set, 25-track crate\n{tbl}\n")
    timeline_png(plays, png, title)


t0 = time.time()
plays_exact = scan_exact(set_path, store.all_tracks(), progress=lambda i, n, f: None)
report(plays_exact, "exact only", time.time() - t0, "bench_exact_only.txt",
       "timeline_exact_only.png",
       "Demo set: ground truth (top) vs SetLedger claims (bottom) — exact layer only")

embedder, name = pick_embedder()
if embedder is not None:
    cached_embeddings(embedder, name)
    t0 = time.time()
    res = scan_full(set_path, store.all_tracks(), store, embedder,
                    tmp_dir=root / "data" / ".tmp_scan")
    print("nominated:", res.nominated)
    report(res.plays, f"exact + {name} recall", time.time() - t0, f"bench_full_{name}.txt",
           f"timeline_full_{name}.png",
           f"Demo set: ground truth (top) vs claims (bottom) — exact (green) + {name} recall (blue)")
    pack_plays = res.plays
else:
    print("degraded: exact-only mode")
    pack_plays = plays_exact

out = write_pack(pack_plays, {t.id: t for t in store.all_tracks()},
                 PackMeta("2026-10-13", "Demo Venue", "demo_set.wav"), media)
print("pack:", out)
