"""Storage layer: one interface, two drivers.

PgStore (production, Tiger Data / any postgres with pgvector) is the real datastore;
MemoryStore backs unit tests and degraded local runs. No ORM — plain SQL via psycopg.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class TrackRow:
    id: int
    file: str
    title: str
    artist: str
    duration: float
    fingerprint: np.ndarray          # uint32 raw chromaprint
    registered: bool = False
    embedding_windows: np.ndarray | None = None  # (n_windows, dim) float32
    tags_raw: dict = field(default_factory=dict)


class MemoryStore:
    def __init__(self) -> None:
        self.tracks: dict[int, TrackRow] = {}
        self._next = 1

    def upsert_track(self, file: str, title: str, artist: str, duration: float,
                     fingerprint: np.ndarray, registered: bool = False) -> int:
        for t in self.tracks.values():
            if t.file == file:
                t.fingerprint = fingerprint
                t.duration = duration
                return t.id
        tid = self._next
        self._next += 1
        self.tracks[tid] = TrackRow(tid, file, title, artist, duration, fingerprint, registered)
        return tid

    def all_tracks(self) -> list[TrackRow]:
        return list(self.tracks.values())

    def set_embeddings(self, track_id: int, windows: np.ndarray) -> None:
        self.tracks[track_id].embedding_windows = windows

    def count(self) -> int:
        return len(self.tracks)


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS tracks (
  id SERIAL PRIMARY KEY,
  file TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  artist TEXT NOT NULL,
  duration REAL NOT NULL,
  fingerprint BYTEA NOT NULL,
  registered BOOLEAN NOT NULL DEFAULT FALSE,
  tags_raw JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS track_windows (
  id SERIAL PRIMARY KEY,
  track_id INT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  w_index INT NOT NULL,
  embedding vector(%(dim)s)
);
CREATE INDEX IF NOT EXISTS track_windows_vec ON track_windows
  USING hnsw (embedding vector_cosine_ops);
"""


class PgStore:
    """Postgres + pgvector driver. Connect with a DSN (Tiger Data cloud or local docker)."""

    def __init__(self, dsn: str, dim: int = 768) -> None:
        import psycopg  # deferred: optional in test envs
        self._psycopg = psycopg
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.dim = dim
        with self.conn.cursor() as cur:
            cur.execute(SCHEMA % {"dim": dim})

    def upsert_track(self, file: str, title: str, artist: str, duration: float,
                     fingerprint: np.ndarray, registered: bool = False) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tracks (file, title, artist, duration, fingerprint, registered)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (file) DO UPDATE SET fingerprint=EXCLUDED.fingerprint,
                     duration=EXCLUDED.duration
                   RETURNING id""",
                (file, title, artist, duration, fingerprint.tobytes(), registered),
            )
            return cur.fetchone()[0]

    def all_tracks(self) -> list[TrackRow]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, file, title, artist, duration, fingerprint, registered, tags_raw FROM tracks")
            rows = []
            for r in cur.fetchall():
                rows.append(TrackRow(r[0], r[1], r[2], r[3], r[4],
                                     np.frombuffer(r[5], dtype=np.uint32), r[6],
                                     tags_raw=r[7] if isinstance(r[7], dict) else json.loads(r[7] or "{}")))
            return rows

    def set_embeddings(self, track_id: int, windows: np.ndarray) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM track_windows WHERE track_id=%s", (track_id,))
            for i, w in enumerate(windows):
                cur.execute(
                    "INSERT INTO track_windows (track_id, w_index, embedding) VALUES (%s,%s,%s)",
                    (track_id, i, "[" + ",".join(f"{x:.6f}" for x in w) + "]"),
                )

    def nearest_windows(self, query: np.ndarray, k: int = 8) -> list[tuple[int, int, float]]:
        """Return [(track_id, w_index, cosine_distance)] for the k nearest windows."""
        vec = "[" + ",".join(f"{x:.6f}" for x in query) + "]"
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT track_id, w_index, embedding <=> %s::vector AS dist
                   FROM track_windows ORDER BY dist ASC LIMIT %s""",
                (vec, k),
            )
            return [(r[0], r[1], float(r[2])) for r in cur.fetchall()]

    def count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM tracks")
            return cur.fetchone()[0]
