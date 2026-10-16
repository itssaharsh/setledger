"""PgStore against a live pgvector Postgres (Tiger Data, env DSN, or embedded pgserver).

DSN resolution lives in conftest.pg_dsn. Everything runs in a throwaway schema that is
dropped afterwards, so a shared managed service is never touched outside it.
"""
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.store import PgStore


@pytest.fixture(scope="module")
def store(pg_dsn):
    dsn, _source = pg_dsn
    try:
        s = PgStore(dsn, dim=8, schema=f"sl_test_{uuid.uuid4().hex[:8]}")
    except Exception as e:
        pytest.skip(f"Postgres unreachable: {e}")
    yield s
    s.drop_schema()
    s.conn.close()


def _vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=8).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture(scope="module")
def world(store):
    fp = np.arange(64, dtype=np.uint32)
    a = store.upsert_track("a.wav", "Alpha", "Artist One", 120.0, fp, registered=True)
    b = store.upsert_track("b.wav", "Beta", "Artist Two", 118.0, fp + 1)
    store.set_embeddings(a, np.stack([_vec(1), _vec(2)]))
    store.set_embeddings(b, np.stack([_vec(10), _vec(11)]))
    return {"a": a, "b": b}


def test_upsert_is_idempotent(store, world):
    again = store.upsert_track("a.wav", "Alpha", "Artist One", 120.0,
                               np.arange(64, dtype=np.uint32))
    assert again == world["a"] and store.count() == 2


def test_roundtrip_fingerprint(store, world):
    tr = {t.file: t for t in store.all_tracks()}["a.wav"]
    assert tr.fingerprint.dtype == np.uint32 and len(tr.fingerprint) == 64
    assert tr.registered is True


def test_topk_cosine_returns_planted_neighbor(store, world):
    hits = store.nearest_windows(_vec(10), k=3)
    assert hits[0][0] == world["b"] and hits[0][2] < 1e-5
    assert all(h[2] <= hits[-1][2] for h in hits)  # sorted by distance


def test_hybrid_tag_plus_vector(store, world):
    # query vector is b's, but the relational filter pins results to track a
    hits = store.nearest_windows_hybrid(_vec(10), k=5, registered=True)
    assert hits and all(h[0] == world["a"] for h in hits)
    hits2 = store.nearest_windows_hybrid(_vec(10), k=5, artist="Artist Two")
    assert hits2[0][0] == world["b"] and hits2[0][2] < 1e-5


def test_set_embeddings_replaces(store, world):
    store.set_embeddings(world["b"], np.stack([_vec(20)]))
    hits = store.nearest_windows(_vec(20), k=1)
    assert hits[0][0] == world["b"]
    assert not any(h[2] < 1e-5 for h in store.nearest_windows(_vec(10), k=2))


def test_app_uses_pgvector_when_dsn_is_set(pg_dsn, monkeypatch):
    import setledger.app as appmod
    monkeypatch.setenv("SETLEDGER_PG_DSN", pg_dsn[0])
    s, kind = appmod._open_store(8)
    try:
        assert kind == "pgvector" and s.count() == 0
    finally:
        s.drop_schema()
        s.conn.close()
    monkeypatch.delenv("SETLEDGER_PG_DSN")
    _, kind = appmod._open_store(8)
    assert kind == "memory"
