#!/usr/bin/env python3
"""Ledger demo: N synthetic gigs across two quarters -> real scans -> ledger -> quarterly
royalty statements, plus a scanner benchmark aggregated over every gig.

DSN: SETLEDGER_PG_DSN, else ~/.config/setledger/tiger_dsn, else an embedded pgserver.
Every gig is generated from seeds (builder-owned synthetic audio), so the run is
reproducible; the artifact records which backend (TimescaleDB or plain Postgres) ran.
"""
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import numpy as np

from setledger.bench import score
from setledger.embed import ClapEmbedder, SpectralEmbedder
from setledger.index import index_crate
from setledger.ledger import Ledger, quarter_bounds
from setledger.mixgen import build_set, standard_demo_plays
from setledger.scan import scan_full
from setledger.store import MemoryStore

ROOT = Path(__file__).parent.parent
CRATE = ROOT / "data" / "crate"
GIGS = ROOT / "data" / "gigs"
OUT = ROOT / "docs" / "media" / "ledger-statement.md"
UTC = timezone.utc
REGISTERED = {"1000_Midnight_Harbour.wav", "1003_Neon_Garden.wav", "1010_Midnight_Circuit.wav",
              "1014_Hollow_Signal.wav", "1017_Glass_Meridian.wav"}
GIG_PLAN = [  # (gig_id, venue, start, playlist seed)
    ("2026-08-07-warehouse", "Warehouse 9", datetime(2026, 8, 7, 22, tzinfo=UTC), 21),
    ("2026-08-29-rooftop", "Rooftop Social", datetime(2026, 8, 29, 21, tzinfo=UTC), 34),
    ("2026-09-19-warehouse", "Warehouse 9", datetime(2026, 9, 19, 23, tzinfo=UTC), 55),
    ("2026-10-03-basement", "The Basement", datetime(2026, 10, 3, 22, tzinfo=UTC), 89),
    ("2026-10-10-rooftop", "Rooftop Social", datetime(2026, 10, 10, 21, tzinfo=UTC), 144),
]


def dsn_and_label():
    if os.environ.get("SETLEDGER_PG_DSN"):
        return os.environ["SETLEDGER_PG_DSN"], "env DSN", None
    f = Path.home() / ".config" / "setledger" / "tiger_dsn"
    if f.exists() and f.read_text().strip():
        return f.read_text().strip(), "Tiger Data", None
    import pgserver
    srv = pgserver.get_server(tempfile.mkdtemp(prefix="sl_pg_"), cleanup_mode="delete")
    return srv.get_uri(), "embedded Postgres 16 (pgserver)", srv


def main() -> int:
    store = MemoryStore()
    index_crate(CRATE, store, registered_files=REGISTERED)
    tracks = store.all_tracks()
    clap = ClapEmbedder()
    emb, emb_name = (clap, "clap") if clap.available() else (SpectralEmbedder(), "spectral")
    cache = ROOT / "data" / f"crate_embeddings_{emb_name}.npz"
    if cache.exists():
        z = np.load(cache)
        for t in tracks:
            store.set_embeddings(t.id, z[t.file])
    else:
        for t in tracks:
            store.set_embeddings(t.id, emb.embed_path(CRATE / t.file))

    dsn, backend, srv = dsn_and_label()
    lg = Ledger(dsn, schema="setledger_demo")
    reg = {t.file: t.registered for t in tracks}
    totals = {t: {"total": 0, "identified": 0, "partial": 0} for t in ("clean", "shifted", "foreign")}
    false_claims = foreign_claimed = 0
    GIGS.mkdir(parents=True, exist_ok=True)
    crate_files = sorted(t.file for t in tracks)
    t_all = time.time()
    for gig_id, venue, start, seed in GIG_PLAN:
        wav, truth_path = GIGS / f"{gig_id}.wav", GIGS / f"{gig_id}.truth.json"
        if not wav.exists():
            build_set(CRATE, wav, truth_path, standard_demo_plays(crate_files, rng_seed=seed),
                      seed=seed)
        t0 = time.time()
        res = scan_full(wav, tracks, store, emb, tmp_dir=GIGS / ".tmp_scan")
        r = score(json.loads(truth_path.read_text()), res.plays)
        for tier, v in r["tiers"].items():
            for k in ("total", "identified", "partial"):
                totals[tier][k] += v[k]
        false_claims += r["false_claims"]
        foreign_claimed += r["foreign_claimed"]
        added = lg.record_gig(gig_id, venue, start, wav.name, res.plays, reg)
        print(f"{gig_id}: scanned {time.time() - t0:.1f}s, {added} plays recorded, "
              f"false claims {r['false_claims']}")
    lg.refresh()

    lines = ["# Quarterly royalty statements (synthetic gigs)", "",
             f"Backend: **{backend}**, "
             f"{'TimescaleDB hypertable `plays` + continuous aggregate `plays_monthly`' if lg.timescale else 'plain table + view (TimescaleDB not installed here)'}. "
             f"Recall driver: {emb_name}. {len(GIG_PLAN)} builder-owned synthetic gigs, scanned for real "
             f"in {time.time() - t_all:.0f}s total.", ""]
    for q in (date(2026, 8, 1), date(2026, 10, 1)):
        a, b = quarter_bounds(q)
        rows = lg.statement(a, b)
        qn = (a.month - 1) // 3 + 1
        lines += [f"## Q{qn} {a.year} — {lg.gig_count(a, b)} gigs", "",
                  "| track | plays | minutes | weakest evidence | via ≈ | registered |",
                  "|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r.track_file} | {r.plays} | {r.seconds / 60:.1f} | {r.min_confidence:.2f} "
                         f"| {'yes' if r.any_embedding else ''} | {'YES' if r.registered else 'no — register to claim'} |")
        lines.append("")
    c, s = totals["clean"], totals["shifted"]
    lines += ["## Scanner benchmark across all gigs", "",
              "| tier | plays | found (≥50% of the play) | partial |", "|---|---|---|---|",
              f"| clean | {c['total']} | {c['identified']} | {c['partial']} |",
              f"| tempo-shifted | {s['total']} | {s['identified']} | {s['partial']} |",
              f"| foreign (not in crate) | {totals['foreign']['total']} | claimed: {foreign_claimed} | |",
              "", f"False claims across every gig: **{false_claims}**."]
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[-3:]))
    print(f"-> {OUT.relative_to(ROOT)}")
    lg.conn.close()
    if srv is not None:
        srv.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
