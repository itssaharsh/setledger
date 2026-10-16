import importlib
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.mixgen import build_set
from setledger.trackgen import TrackSpec, render, write_wav


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("app_world")
    crate = root / "crate"
    crate.mkdir()
    for i in range(3):
        spec = TrackSpec(seed=8000 + i, title=f"W{i}", artist="A", bpm=122 + 2 * i, key="ACE"[i])
        write_wav(crate / f"{spec.seed}_W{i}.wav", render(spec, bars=24))
    set_wav = root / "set.wav"
    build_set(crate, set_wav, root / "truth.json",
              [{"file": "8000_W0.wav", "seg_s": 30.0, "shift": 1.0},
               {"file": "8001_W1.wav", "seg_s": 30.0, "shift": 1.06}],
              crossfade_s=2.0, seed=9)
    os.environ["SETLEDGER_CRATE"] = str(crate)
    os.environ["SETLEDGER_SET"] = str(set_wav)
    os.environ["SETLEDGER_OUT"] = str(root / "out")
    os.environ["SETLEDGER_EMBEDDER"] = "spectral"
    import setledger.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        deadline = time.time() + 60
        while time.time() < deadline:
            if c.get("/api/state").json()["ready"]:
                break
            time.sleep(0.3)
        else:
            raise TimeoutError("world never became ready")
        yield c


def test_home_serves_workspace(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "SetLedger" in body and "judge" in body.lower()
    assert "Scan Saturday" in body


def test_state_has_world(client):
    s = client.get("/api/state").json()
    assert s["ready"] and len(s["tracks"]) == 3
    assert len(s["waveform"]) > 100 and s["duration"] > 30
    assert s["embedder"] == "spectral" and s["degraded"] is False


def test_scan_runs_and_streams_claims(client):
    assert client.post("/api/scan").json()["started"]
    deadline = time.time() + 180
    while time.time() < deadline:
        sc = client.get("/api/scan/progress").json()
        if sc["done"]:
            break
        time.sleep(0.5)
    assert sc["error"] is None, sc["error"]
    assert sc["claims"], "no claims streamed"
    matched = [p for p in sc["plays"] if p["status"] == "matched"]
    assert {p["track_file"] for p in matched} == {"8000_W0.wav", "8001_W1.wav"}
    assert all(p["method"] in ("exact", "embedding") for p in matched)
    assert sc["elapsed"] > 0


def test_export_writes_pack(client):
    d = client.get("/api/export").json()
    assert d["rows"] >= 2
    out = Path(os.environ["SETLEDGER_OUT"])
    assert (out / Path(d["csv"]).name).exists()
    assert (out / Path(d["pdf"]).name).exists()
    # files are served
    assert client.get(d["csv"]).status_code == 200


def test_scan_conflict_and_reset(client):
    assert client.post("/api/reset").json()["reset"]
    sc = client.get("/api/scan/progress").json()
    assert sc["plays"] == [] and sc["claims"] == []


def test_export_without_scan_is_clean_error(client):
    r = client.get("/api/export")
    assert r.status_code == 400


def test_bench_endpoint(client):
    b = client.get("/api/bench").json()
    assert isinstance(b, dict)


def test_pick_embedder_none_is_degraded():
    import setledger.app as appmod
    os.environ["SETLEDGER_EMBEDDER"] = "none"
    try:
        emb, name = appmod._pick_embedder()
        assert emb is None and name == "none"
    finally:
        os.environ["SETLEDGER_EMBEDDER"] = "spectral"


def test_concurrent_scan_is_409_not_a_second_run(client):
    assert client.post("/api/scan").status_code == 200
    second = client.post("/api/scan")
    assert second.status_code == 409          # the UI turns this into "joining a scan"
    deadline = time.time() + 180
    while time.time() < deadline and not client.get("/api/scan/progress").json()["done"]:
        time.sleep(0.5)
    assert client.get("/api/scan/progress").json()["error"] is None
