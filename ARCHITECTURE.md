# SetLedger architecture (as built)

One Python package (FastAPI app + scripts), one database (Postgres with pgvector and TimescaleDB,
on Tiger Data), audio files on disk. Measured results are in the README.

## The scan pipeline

1. **Index** (`index.py`): `fpcalc -raw` chromaprint for every crate track, stored with its
   metadata. Undecodable files are skipped and named, never fatal.
2. **Exact layer** (`scan.py: scan_exact`): XOR + popcount between the set's and each track's
   fingerprints at every alignment lag. The winning lag is the one with the *longest low-error
   run*, not the lowest mean error, because loop-based music self-matches at bar offsets
   ([F-0001](docs/memory/failures/)). A claim is a run of 15 s or more under a 0.28 bit-error
   rate; claims are assigned greedily without overlap, and uncovered gaps of 10 s or more become
   review spans.
3. **Recall layer** (`embed.py`, `scan.py: scan_full`): 10 s window embeddings (CLAP
   `laion/clap-htsat-unfused`, or a numpy spectral-envelope driver for small hosts) live in
   pgvector behind an HNSW index. Each review span nominates its top candidate tracks by cosine
   votes; `confirm_candidates` then tempo-corrects the span across a 0.90 to 1.10 atempo sweep
   and re-runs the exact matcher. Only a passing fingerprint run becomes a claim (method
   `embedding`). Sub-gaps go back on a worklist, and `merge_fragments` joins same-track claims
   within 5 s into one play ([F-0002](docs/memory/failures/)).
4. **Tags** (`tags.py`): Gemma 3 1B through ollama (or any OpenAI-compatible endpoint) under a
   two-sided vocabulary guard (no invented words, no dropped words), with placeholder artists
   blanked first and deterministic junk-suffix and case cleanup after. Any failure falls back
   to passthrough.
5. **Export** (`export.py`): CSV + PDF setlist report: date, venue, each work and its duration,
   plus the evidence columns (confidence, method, registered).
6. **Ledger** (`ledger.py`): a `plays` hypertable partitioned on `played_at` and a
   `plays_monthly` continuous aggregate on TimescaleDB; quarterly statements read the monthly
   rollup. On plain Postgres the same names are a table and a view.
7. **Benchmark** (`mixgen.py`, `bench.py`): seeded synthetic sets with ground-truth JSON, scored
   as identified / partial / false claim.

**Storage** (`store.py`): `MemoryStore` for tests and laptops, `PgStore` for pgvector
(schema-namespaced, plus a hybrid query that puts relational filters under the cosine
ordering). The app uses `PgStore` whenever `SETLEDGER_PG_DSN` is set.

## Serving

- **App** (`app.py`): FastAPI. The demo world loads in a background thread; one scan runs at a
  time and concurrent viewers join it; progress is polled; CORS lets the .tech page drive it.
- **Render** (`render.yaml`, `Dockerfile`): Starter instance, spectral driver, Tiger DSN. The
  waveform is cached at build time because decoding the set measured 51 MB to 407 MB.
- **The .tech page** (static, on Vercel): the same UI plus `deploy/static_replay/replay.js`,
  which drives the live API if it answers within 6 s and otherwise replays one recorded real
  scan, labeled in the header (`scripts/record_replay.py`, `scripts/build_static.py`).
- **DigitalOcean** (`scripts/deploy_do.sh`, `deploy/`): doctl droplet deploy behind Caddy and
  systemd. Written and checked offline; never run, because the account was blocked.

## Deliberately not built

Auth, audio uploads (the demo is seeded, and nobody's audio leaves their machine), queues,
Redis, an SPA framework, live MusicBrainz lookups.
