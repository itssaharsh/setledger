#!/usr/bin/env python3
"""Record a real scan from a running server, for the static replay demo.

Polls the live API the way the browser does and writes every progress event and claim with
the time it actually happened, plus the final plays, the generated setlist report files and
the benchmark artifacts. Usage: python scripts/record_replay.py [base_url] [out_dir]
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).parent.parent / "site")


def call(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    while not call("/api/state")["ready"]:
        time.sleep(1)
    state = call("/api/state")
    state.pop("scan", None)
    call("/api/reset", "POST")
    call("/api/scan", "POST")
    t0 = time.monotonic()
    started = datetime.now(timezone.utc)
    events, claims, seen = [], [], 0
    while True:
        sc = call("/api/scan/progress")
        t = round(time.monotonic() - t0, 2)
        ev = {k: sc[k] for k in ("phase", "step", "total", "file")}
        if not events or {k: events[-1][k] for k in ev} != ev:
            events.append({"t": t, **ev})
        for c in sc["claims"][seen:]:
            claims.append({"t": t, "play": c})
        seen = len(sc["claims"])
        if sc["done"]:
            break
        time.sleep(0.2)
    if sc["error"]:
        print("scan error:", sc["error"])
        return 1
    export = call("/api/export")
    files = OUT / "files"
    files.mkdir(parents=True, exist_ok=True)
    for key in ("csv", "pdf"):
        name = Path(export[key]).name
        urllib.request.urlretrieve(BASE + export[key], files / name)
        export[key] = f"files/{name}"
    ist = started.astimezone(timezone(timedelta(hours=5, minutes=30)))
    run = {"recorded_at_utc": started.isoformat(timespec="seconds"),
           "recorded_at_label": ist.strftime("%d %b %Y, %H:%M IST"),
           "elapsed": sc["elapsed"], "total": sc["total"], "events": events,
           "claims": claims, "plays": sc["plays"], "export": export, "state": state}
    (OUT / "replay").mkdir(parents=True, exist_ok=True)
    (OUT / "replay" / "run.json").write_text(json.dumps(run))
    (OUT / "replay" / "bench.json").write_text(json.dumps(call("/api/bench")))
    matched = sum(p["status"] == "matched" for p in sc["plays"])
    print(f"recorded {run['recorded_at_label']}: {sc['elapsed']}s, {len(events)} events, "
          f"{len(claims)} streamed claims, {matched} final plays, embedder {state['embedder']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
