import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.ledger import Ledger, quarter_bounds
from setledger.scan import Play

UTC = timezone.utc
REG = {"a.wav": True, "b.wav": False}


def _plays(shift_conf=0.9):
    return [
        Play(1, "a.wav", 0.0, 60.0, 0.95, "exact", "matched"),
        Play(None, None, 60.0, 75.0, 0.0, "none", "review"),       # never enters the ledger
        Play(2, "b.wav", 75.0, 120.0, shift_conf, "embedding", "matched"),
    ]


@pytest.fixture(scope="module")
def ledger(pg_dsn):
    dsn, _ = pg_dsn
    lg = Ledger(dsn, schema=f"sl_ledger_{uuid.uuid4().hex[:8]}", policy=False)
    # two gigs in Q3 (Aug, Sep), one in Q4 (Oct)
    lg.record_gig("g1", "Club A", datetime(2026, 8, 7, 22, tzinfo=UTC), "g1.wav", _plays(), REG)
    lg.record_gig("g2", "Club B", datetime(2026, 9, 18, 23, tzinfo=UTC), "g2.wav", _plays(0.8), REG)
    lg.record_gig("g3", "Club A", datetime(2026, 10, 9, 22, tzinfo=UTC), "g3.wav", _plays()[:1], REG)
    lg.refresh()
    yield lg
    lg.drop()


def test_quarter_bounds():
    assert quarter_bounds(date(2026, 8, 7)) == (datetime(2026, 7, 1, tzinfo=UTC),
                                                datetime(2026, 10, 1, tzinfo=UTC))
    assert quarter_bounds(date(2026, 11, 30)) == (datetime(2026, 10, 1, tzinfo=UTC),
                                                  datetime(2027, 1, 1, tzinfo=UTC))


def test_q3_statement_rolls_up_across_months(ledger):
    rows = {r.track_file: r for r in ledger.statement(*quarter_bounds(date(2026, 8, 1)))}
    assert set(rows) == {"a.wav", "b.wav"}                 # review span never claimed
    assert rows["a.wav"].plays == 2 and rows["a.wav"].seconds == pytest.approx(120.0)
    assert rows["b.wav"].plays == 2 and rows["b.wav"].seconds == pytest.approx(90.0)
    assert rows["b.wav"].any_embedding and not rows["a.wav"].any_embedding
    assert rows["b.wav"].min_confidence == pytest.approx(0.8)   # weakest evidence surfaces
    assert rows["a.wav"].registered and not rows["b.wav"].registered
    assert ledger.gig_count(*quarter_bounds(date(2026, 8, 1))) == 2


def test_q4_is_separate(ledger):
    rows = ledger.statement(*quarter_bounds(date(2026, 10, 1)))
    assert [(r.track_file, r.plays) for r in rows] == [("a.wav", 1)]


def test_rerecording_a_gig_is_idempotent(ledger):
    assert ledger.record_gig("g1", "Club A", datetime(2026, 8, 7, 22, tzinfo=UTC),
                             "g1.wav", _plays(), REG) == 0
    ledger.refresh()
    rows = {r.track_file: r for r in ledger.statement(*quarter_bounds(date(2026, 8, 1)))}
    assert rows["a.wav"].plays == 2


def test_timescale_objects_when_available(ledger):
    if not ledger.timescale:
        pytest.skip("TimescaleDB not installed on this Postgres (plain-view fallback active)")
    with ledger.conn.cursor() as cur:
        ht = cur.execute("""SELECT count(*) FROM timescaledb_information.hypertables
                            WHERE hypertable_schema=%s AND hypertable_name='plays'""",
                         (ledger.schema,)).fetchone()[0]
        ca = cur.execute("""SELECT count(*) FROM timescaledb_information.continuous_aggregates
                            WHERE view_schema=%s AND view_name='plays_monthly'""",
                         (ledger.schema,)).fetchone()[0]
    assert ht == 1 and ca == 1
