"""The ledger over time: every confirmed play from every gig, rolled up into the quarterly
royalty statement a DJ actually files.

On TimescaleDB (Tiger Data) `plays` is a hypertable partitioned on played_at and
`plays_monthly` is a continuous aggregate, so a statement reads pre-rolled monthly buckets
instead of rescanning every play ever recorded. On plain Postgres the same names exist as
a regular table and view; queries are identical, only the storage differs.
`Ledger.timescale` says which one is live, and callers report it rather than assume it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

BASE = """
CREATE SCHEMA IF NOT EXISTS {schema};
SET search_path TO {schema}, public;
CREATE TABLE IF NOT EXISTS gigs (
  gig_id TEXT PRIMARY KEY,
  venue TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  recording TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plays (
  played_at TIMESTAMPTZ NOT NULL,
  gig_id TEXT NOT NULL,
  track_file TEXT NOT NULL,
  seconds REAL NOT NULL,
  confidence REAL NOT NULL,
  method TEXT NOT NULL,
  registered BOOLEAN NOT NULL,
  PRIMARY KEY (gig_id, track_file, played_at)
);
"""

MONTHLY_SELECT = """
  SELECT {bucket} AS month,
         track_file,
         count(*) AS plays,
         sum(seconds) AS seconds,
         min(confidence) AS min_confidence,
         bool_or(method = 'embedding') AS any_embedding,
         bool_and(registered) AS registered
  FROM plays GROUP BY 1, 2
"""

HYPERTABLE = """SELECT create_hypertable('plays', 'played_at',
  chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE)"""
# its own execute(): Timescale rejects continuous-aggregate DDL in a multi-statement string
CAGG = ("CREATE MATERIALIZED VIEW IF NOT EXISTS plays_monthly WITH (timescaledb.continuous) AS"
        + MONTHLY_SELECT.format(bucket="time_bucket(INTERVAL '1 month', played_at)")
        + " WITH NO DATA")

PLAIN = "CREATE OR REPLACE VIEW plays_monthly AS " + MONTHLY_SELECT.format(
    bucket="date_trunc('month', played_at)")


@dataclass
class StatementRow:
    track_file: str
    plays: int
    seconds: float
    min_confidence: float
    any_embedding: bool
    registered: bool


def quarter_bounds(d: date) -> tuple[datetime, datetime]:
    """[start, end) of the calendar quarter containing d, UTC midnight."""
    q0 = 3 * ((d.month - 1) // 3) + 1
    start = datetime(d.year, q0, 1, tzinfo=timezone.utc)
    end = datetime(d.year + (q0 == 10), 1 if q0 == 10 else q0 + 3, 1, tzinfo=timezone.utc)
    return start, end


class Ledger:
    def __init__(self, dsn: str, schema: str = "setledger", policy: bool = True) -> None:
        import psycopg
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.schema = schema
        self.timescale = self._enable_timescale()
        with self.conn.cursor() as cur:
            cur.execute(BASE.format(schema=schema))
            if self.timescale:
                cur.execute(HYPERTABLE)
                cur.execute(CAGG)
                if policy:
                    cur.execute("""SELECT add_continuous_aggregate_policy('plays_monthly',
                        start_offset => INTERVAL '12 months', end_offset => NULL,
                        schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE)""")
            else:
                cur.execute(PLAIN)

    def _enable_timescale(self) -> bool:
        with self.conn.cursor() as cur:
            if cur.execute("SELECT 1 FROM pg_extension WHERE extname='timescaledb'").fetchone():
                return True
            if not cur.execute("SELECT 1 FROM pg_available_extensions WHERE name='timescaledb'").fetchone():
                return False
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
                return True
            except Exception:
                return False

    def record_gig(self, gig_id: str, venue: str, started_at: datetime, recording: str,
                   plays, registered: dict[str, bool]) -> int:
        """Insert a gig and its matched plays. Idempotent: re-recording a gig adds nothing.
        `plays` are scan Play objects (or dicts); only status='matched' rows are kept."""
        g = (lambda p, k: p[k]) if plays and isinstance(plays[0], dict) else getattr
        n = 0
        with self.conn.cursor() as cur:
            cur.execute("SET search_path TO %s, public" % self.schema)
            cur.execute("""INSERT INTO gigs VALUES (%s,%s,%s,%s) ON CONFLICT (gig_id) DO NOTHING""",
                        (gig_id, venue, started_at, recording))
            for p in plays:
                if g(p, "status") != "matched":
                    continue
                cur.execute(
                    """INSERT INTO plays VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (started_at + timedelta(seconds=g(p, "t_start")), gig_id, g(p, "track_file"),
                     g(p, "t_end") - g(p, "t_start"), g(p, "confidence"), g(p, "method"),
                     registered.get(g(p, "track_file"), False)))
                n += cur.rowcount
        return n

    def refresh(self) -> None:
        """Materialize the continuous aggregate now (a no-op view on plain Postgres)."""
        if self.timescale:
            with self.conn.cursor() as cur:
                cur.execute("SET search_path TO %s, public" % self.schema)
                cur.execute("CALL refresh_continuous_aggregate('plays_monthly', NULL, NULL)")

    def statement(self, start: datetime, end: datetime) -> list[StatementRow]:
        """Per-track totals for [start, end), read from the monthly rollup."""
        with self.conn.cursor() as cur:
            cur.execute("SET search_path TO %s, public" % self.schema)
            cur.execute(
                """SELECT track_file, sum(plays)::int, sum(seconds)::float8,
                          min(min_confidence)::float8, bool_or(any_embedding), bool_and(registered)
                   FROM plays_monthly WHERE month >= %s AND month < %s
                   GROUP BY track_file ORDER BY sum(seconds) DESC, track_file""",
                (start, end))
            return [StatementRow(*r) for r in cur.fetchall()]

    def gig_count(self, start: datetime, end: datetime) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SET search_path TO %s, public" % self.schema)
            return cur.execute("SELECT count(*) FROM gigs WHERE started_at >= %s AND started_at < %s",
                               (start, end)).fetchone()[0]

    def drop(self) -> None:
        with self.conn.cursor() as cur:
            if self.timescale:
                cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {self.schema}.plays_monthly CASCADE")
            cur.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        self.conn.close()
