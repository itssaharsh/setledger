#!/usr/bin/env python3
"""Full CLI slice on the demo world: index -> scan -> bench -> timeline PNG -> pack."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.index import index_crate
from setledger.store import MemoryStore
from setledger.scan import scan_exact
from setledger.bench import run_bench, table, score
from setledger.export import PackMeta, write_pack

root = Path(__file__).parent.parent
media = root / "docs" / "media"; media.mkdir(parents=True, exist_ok=True)
store = MemoryStore()
t0 = time.time()
rep = index_crate(root / "data/crate", store, registered_files={"1000_Midnight_Harbour.wav"})
print(f"indexed {len(rep.indexed)} tracks ({time.time()-t0:.1f}s), skipped {len(rep.skipped)}")
set_path = root / "data/mixes/demo_set.wav"
truth_path = root / "data/mixes/demo_set.truth.json"
t0 = time.time()
plays = scan_exact(set_path, store.all_tracks(), progress=lambda i,n,f: None)
scan_s = time.time() - t0
print(f"scan: {scan_s:.1f}s")
for p in plays:
    print(f"  {p.t_start:7.1f}-{p.t_end:7.1f}  {p.status:8s} {p.method:9s} conf={p.confidence:.3f}  {p.track_file or '— review manually —'}")
truth = json.loads(truth_path.read_text())
results = score(truth, plays)
tbl = table(results)
print(tbl)
(media / "bench_exact_only.txt").write_text(f"scan {scan_s:.1f}s on {truth[-1]['t_end']:.0f}s set, 25-track crate\n{tbl}\n")

# timeline PNG
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(12, 2.6))
for seg in truth:
    ax.axvspan(seg["t_start"], seg["t_end"], ymin=0.55, ymax=0.95,
               color="#cfd8ea" if seg["track_file"] else "#f3d9d4", alpha=0.9)
for p in plays:
    c = {"matched": "#2e7d32", "review": "#f2a93b"}[p.status]
    ax.axvspan(p.t_start, p.t_end, ymin=0.08, ymax=0.48, color=c, alpha=0.85)
    if p.status == "matched":
        ax.text((p.t_start+p.t_end)/2, 0.28, f"{p.confidence:.2f}", ha="center", va="center", fontsize=7, color="white")
ax.set_xlim(0, truth[-1]["t_end"]); ax.set_ylim(0, 1)
ax.set_yticks([0.28, 0.75]); ax.set_yticklabels(["SetLedger", "truth"])
ax.set_xlabel("seconds"); ax.set_title("Demo set: ground truth (top) vs SetLedger claims (bottom) — exact layer only")
fig.tight_layout(); fig.savefig(media / "timeline_exact_only.png", dpi=150)

out = write_pack(plays, {t.id: t for t in store.all_tracks()}, PackMeta("2026-10-13", "Demo Venue", "demo_set.wav"), media)
print("pack:", out)
