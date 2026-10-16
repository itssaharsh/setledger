"""SetLedger web app: seeded demo workspace over the real pipeline.

One screen (UI-SPEC S1): crate panel, set waveform timeline, streaming scan, claim
chips, review spans, export. The server holds one demo world (env-configurable) and
one scan at a time; everything the UI shows comes from real pipeline events.

Env: SETLEDGER_CRATE, SETLEDGER_SET (demo world), SETLEDGER_EMBEDDER=auto|clap|spectral|none.
"""
from __future__ import annotations

import json
import os
import threading
import time
import wave as wavemod
from dataclasses import asdict
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .embed import ClapEmbedder, SpectralEmbedder
from .export import PackMeta, write_pack
from .index import index_crate
from .scan import scan_full
from .store import MemoryStore

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).parent / "static"
CRATE = Path(os.environ.get("SETLEDGER_CRATE", ROOT / "data" / "crate"))
SET = Path(os.environ.get("SETLEDGER_SET", ROOT / "data" / "mixes" / "demo_set.wav"))
OUT = Path(os.environ.get("SETLEDGER_OUT", ROOT / "data" / "out"))
REGISTERED = {"1000_Midnight_Harbour.wav"}

state: dict = {
    "ready": False, "degraded": True, "embedder": "none", "store": "memory", "tracks": [], "skipped": [],
    "waveform": [], "duration": 0.0,
    "scan": {"running": False, "phase": "idle", "step": 0, "total": 0, "file": "",
             "claims": [], "plays": [], "error": None, "elapsed": 0.0, "done": False},
}
_store = MemoryStore()
_embedder = None
_lock = threading.Lock()


def _pick_embedder():
    want = os.environ.get("SETLEDGER_EMBEDDER", "auto")
    if want == "none":
        return None, "none"
    if want in ("clap", "auto"):
        clap = ClapEmbedder()
        if clap.available():
            return clap, "clap"
    if want == "clap":
        return None, "none"
    return SpectralEmbedder(), "spectral"


def _waveform_peaks(path: Path, bins: int = 1200) -> tuple[list[float], float]:
    with wavemod.open(str(path), "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(n), dtype=np.int16).reshape(-1, ch)
    mono = np.abs(pcm.astype(np.float32)).mean(axis=1) / 32767.0
    step = max(1, len(mono) // bins)
    peaks = [float(mono[i:i + step].max()) for i in range(0, len(mono) - step + 1, step)]
    return peaks[:bins], n / sr


def _open_store(dim: int):
    """pgvector (Tiger Data or any Postgres) when SETLEDGER_PG_DSN is set, else in-memory.
    One schema per embedding width, since a vector column has a fixed dimension."""
    dsn = os.environ.get("SETLEDGER_PG_DSN")
    if dsn:
        from .store import PgStore
        return PgStore(dsn, dim=dim, schema=f"setledger_vec{dim}"), "pgvector"
    return MemoryStore(), "memory"


def _load_world() -> None:
    global _embedder, _store
    emb, name = _pick_embedder()
    _embedder = emb
    _store, store_kind = _open_store(getattr(emb, "dim", 64))
    state["store"] = store_kind
    rep = index_crate(CRATE, _store, registered_files=REGISTERED)
    if emb is not None:
        cache = CRATE.parent / f"crate_embeddings_{name}.npz"
        tracks = _store.all_tracks()
        if cache.exists() and set(np.load(cache).files) >= {t.file for t in tracks}:
            z = np.load(cache)
            for t in tracks:
                _store.set_embeddings(t.id, z[t.file])
        else:
            for t in tracks:
                _store.set_embeddings(t.id, emb.embed_path(CRATE / t.file))
    peaks, dur = _waveform_peaks(SET)
    state.update(
        ready=True, degraded=emb is None, embedder=name,
        waveform=peaks, duration=round(dur, 2), skipped=rep.skipped,
        tracks=[{"id": t.id, "file": t.file, "title": t.title, "artist": t.artist,
                 "duration": round(t.duration, 1), "registered": t.registered}
                for t in _store.all_tracks()],
    )


def _run_scan() -> None:
    sc = state["scan"]
    t0 = time.time()
    try:
        tracks = _store.all_tracks()
        sc.update(total=len(tracks), claims=[], plays=[], error=None, done=False,
                  phase="exact")

        def progress(i, n, f):
            sc.update(step=i, file=f)

        def on_claim(play, phase):
            sc["phase"] = phase
            sc["claims"].append(asdict(play))

        res = scan_full(SET, tracks, _store, _embedder, progress=progress,
                        tmp_dir=OUT / ".tmp_scan", on_claim=on_claim)
        sc["plays"] = [asdict(p) for p in res.plays]
        state["degraded"] = res.degraded
    except Exception as e:  # surfaced to the UI error state, never a blank screen
        sc["error"] = f"{type(e).__name__}: {e}"
    finally:
        sc.update(running=False, done=True, phase="idle", elapsed=round(time.time() - t0, 1))


OUT.mkdir(parents=True, exist_ok=True)  # before StaticFiles mounts below
app = FastAPI(title="SetLedger")


@app.on_event("startup")
def startup() -> None:
    threading.Thread(target=_load_world, daemon=True).start()


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def api_state():
    return {k: v for k, v in state.items() if k != "scan"} | {"scan": state["scan"]}


@app.post("/api/scan")
def api_scan():
    with _lock:
        if not state["ready"]:
            return JSONResponse({"error": "world still loading"}, status_code=503)
        if state["scan"]["running"]:
            return JSONResponse({"error": "scan already running"}, status_code=409)
        state["scan"]["running"] = True
    threading.Thread(target=_run_scan, daemon=True).start()
    return {"started": True}


@app.get("/api/scan/progress")
def api_progress():
    return state["scan"]


@app.post("/api/reset")
def api_reset():
    with _lock:
        if state["scan"]["running"]:
            return JSONResponse({"error": "scan running"}, status_code=409)
        state["scan"].update(claims=[], plays=[], error=None, done=False, step=0,
                             phase="idle", elapsed=0.0)
    return {"reset": True}


@app.get("/api/export")
def api_export():
    plays = state["scan"]["plays"]
    if not plays:
        return JSONResponse({"error": "nothing scanned yet"}, status_code=400)
    from .scan import Play
    objs = [Play(**p) for p in plays]
    meta = PackMeta(time.strftime("%Y-%m-%d"), "Demo Venue", SET.name)
    out = write_pack(objs, {t.id: t for t in _store.all_tracks()}, meta, OUT)
    return {"csv": f"/files/{Path(out['csv']).name}", "pdf": f"/files/{Path(out['pdf']).name}",
            "rows": out["rows"]}


@app.get("/api/bench")
def api_bench():
    """Measured numbers for the bench view (S2): shipped artifacts, read-only."""
    media = ROOT / "docs" / "media"
    out = {}
    for f in sorted(media.glob("bench_*.txt")):
        out[f.stem] = f.read_text()
    for key, name in (("tags", "tags-before-after.md"), ("ledger", "ledger-statement.md")):
        f = media / name
        if f.exists():
            out[key] = f.read_text()
    return out


app.mount("/files", StaticFiles(directory=OUT), name="files")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
