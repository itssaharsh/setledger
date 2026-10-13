# SetLedger architecture

One deployable (FastAPI app + CLI in one Python package), one datastore (postgres + pgvector —
Tiger Data cloud in prod, dockerized pg locally), files on disk for audio. Each component names
its forcing requirement (contract §).

- **Decode/segment**: ffmpeg → mono 16k wav windows (10 s, 5 s hop). Forced by §9 scan criteria.
- **Exact matcher**: Chromaprint/fpcalc fingerprints per window vs crate; forced by §2 MUST.
- **Embedding fallback**: MERT-class model (transformers, CPU dev / DO GPU prod) → pgvector cosine
  top-k on shifted windows. Forced by §9 (+6% tempo criterion). Degraded mode = exact-only (§5).
- **Storage**: `store.py` interface; drivers: `pg` (prod, pgvector) — forced by §6 one-datastore +
  Tiger core claim; dev may run docker pg. No ORM; psycopg + SQL.
- **Tag normalizer**: local Gemma via ollama; passthrough fallback (§7). SHOULD-level.
- **Export**: deterministic CSV + PDF (reportlab or weasyprint→check) from Play rows (§9 golden test).
- **Benchmark**: mixgen (builds synthetic sets from crate + truth JSON) + scorer; thresholds live in
  tests (§9). This is the judge-visible hard part.
- **UI**: single page (vanilla JS + htmx-level simplicity), timeline canvas, state banners (§5);
  FastAPI serves it. No SPA framework — nothing forces one.
- **Observability**: structured logs to file; scan progress events (§5 loading states). No more.
- **Deploy**: DO droplet (docker compose: app + pg) behind the .tech domain; seeded demo data baked
  into the image; upload cap 100 MB (§7/8).

Tool checks: ffmpeg ✓ (local bin) · fpcalc 1.5.1 ✓ (static, installed) · docker present, daemon DOWN
(user to start Docker Desktop, or Tiger cloud direct) · transformers/torch CPU to install in venv ·
ollama NOT installed (install at T08; fallback passthrough keeps MUSTs green) · reportlab vs
weasyprint decided at T05 after a 5-min check · Tiger Data cloud signup = user, pending.

Cut list (deliberate): auth, queues, Redis, SPA framework, Temporal, MusicBrainz live calls
(offline tag table instead if Gemma needs grounding).
