#!/usr/bin/env python3
"""Build the demo corpus: 25 procedurally generated, builder-owned tracks + the standard demo set."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.trackgen import make_crate
from setledger.mixgen import build_set, standard_demo_plays

root = Path(__file__).parent.parent / "data"
make_crate(root / "crate", n=25)
crate_files = sorted(p.name for p in (root / "crate").glob("*.wav"))
(root / "mixes").mkdir(exist_ok=True)
plays = standard_demo_plays(crate_files)
truth = build_set(root / "crate", root / "mixes" / "demo_set.wav", root / "mixes" / "demo_set.truth.json", plays)
print(f"crate: {len(crate_files)} tracks | set: {truth[-1].t_end:.0f}s, {len(truth)} plays")
