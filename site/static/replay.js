/* Static demo: answers the page's /api/* calls from one recorded real scan, on the timeline it
   actually happened. Loaded only by the static build (scripts/build_static.py); the FastAPI
   app never serves it. The header says it's a replay and when the run was recorded. */
"use strict";
(() => {
  const realFetch = window.fetch.bind(window);
  const RUN = realFetch("/replay/run.json").then((r) => r.json());
  let t0 = null;

  const json = (obj, status = 200) =>
    new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
  const elapsed = () => (t0 === null ? -1 : (performance.now() - t0) / 1000);
  const idle = { running: false, phase: "idle", step: 0, total: 0, file: "", claims: [], plays: [],
                 error: null, elapsed: 0, done: false };

  function progress(run) {
    const e = elapsed();
    if (e < 0) return idle;
    if (e >= run.elapsed)
      return { running: false, phase: "idle", step: run.total, total: run.total, file: "",
               claims: run.claims.map((c) => c.play), plays: run.plays, error: null,
               elapsed: run.elapsed, done: true };
    let ev = run.events[0];
    for (const x of run.events) { if (x.t <= e) ev = x; else break; }
    return { running: true, phase: ev.phase, step: ev.step, total: ev.total, file: ev.file,
             claims: run.claims.filter((c) => c.t <= e).map((c) => c.play), plays: [],
             error: null, elapsed: 0, done: false };
  }

  window.fetch = async (url, opts = {}) => {
    const path = new URL(url, location.href).pathname;
    if (!path.startsWith("/api/")) return realFetch(url, opts);
    const run = await RUN;
    const method = (opts.method || "GET").toUpperCase();
    const p = progress(run);
    if (path === "/api/state") return json({ ...run.state, scan: p });
    if (path === "/api/scan/progress") return json(p);
    if (path === "/api/scan" && method === "POST") {
      if (p.running) return json({ error: "scan already running" }, 409);
      t0 = performance.now();
      return json({ started: true });
    }
    if (path === "/api/reset" && method === "POST") { t0 = null; return json({ reset: true }); }
    if (path === "/api/export") return p.done ? json(run.export) : json({ error: "nothing scanned yet" }, 400);
    if (path === "/api/bench") return realFetch("/replay/bench.json");
    return json({ error: "not available in the replay" }, 404);
  };

  RUN.then((run) => {
    const mark = () => {
      const brand = document.querySelector(".brand");
      if (!brand || document.getElementById("replay-chip")) return;
      const repo = window.SETLEDGER_REPO;
      const chip = document.createElement(repo ? "a" : "span");
      chip.id = "replay-chip";
      if (repo) { chip.href = repo; chip.target = "_blank"; chip.rel = "noopener"; }
      chip.textContent = `Replay · real scan recorded ${run.recorded_at_label}` +
        (repo ? " · run it live from the repo" : "");
      brand.appendChild(chip);
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mark);
    else mark();
  });
})();
