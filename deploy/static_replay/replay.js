/* Static host shim. If a live SetLedger API is configured and answers, every /api/* call goes
   to it (a real scan). If it doesn't answer within 6 s, the page falls back to one recorded real
   scan, replayed on the timeline it actually ran. The header always says which one you're
   seeing. Loaded only by the static build (scripts/build_static.py). */
"use strict";
(() => {
  const realFetch = window.fetch.bind(window);
  const LIVE = (window.SETLEDGER_LIVE || "").replace(/\/$/, "");
  const RUN = realFetch("/replay/run.json").then((r) => r.json());

  const liveProbe = (async () => {
    if (!LIVE) return false;
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 6000);
    try {
      const r = await realFetch(`${LIVE}/api/state`, { signal: ctl.signal, mode: "cors" });
      return r.ok;
    } catch { return false; } finally { clearTimeout(timer); }
  })();

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

  async function replay(path, method) {
    const run = await RUN;
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
  }

  async function live(path, search, opts) {
    const r = await realFetch(`${LIVE}${path}${search}`, { ...opts, mode: "cors" });
    if (path !== "/api/export" || !r.ok) return r;
    const d = await r.json();               // report files live on the API host
    return json({ ...d, csv: LIVE + d.csv, pdf: LIVE + d.pdf });
  }

  window.fetch = async (url, opts = {}) => {
    const u = new URL(url, location.href);
    if (!u.pathname.startsWith("/api/")) return realFetch(url, opts);
    return (await liveProbe) ? live(u.pathname, u.search, opts)
                             : replay(u.pathname, (opts.method || "GET").toUpperCase());
  };

  Promise.all([RUN, liveProbe]).then(([run, isLive]) => {
    const mark = () => {
      const brand = document.querySelector(".brand");
      if (!brand || document.getElementById("replay-chip")) return;
      const repo = window.SETLEDGER_REPO;
      const chip = document.createElement(repo ? "a" : "span");
      chip.id = "replay-chip";
      if (isLive) chip.classList.add("live");
      if (repo) { chip.href = repo; chip.target = "_blank"; chip.rel = "noopener"; }
      chip.textContent = isLive
        ? "Live · real scans on the server (one shared demo: a running scan is joined)"
        : `Replay · real scan recorded ${run.recorded_at_label}` + (repo ? " · run it live from the repo" : "");
      brand.appendChild(chip);
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mark);
    else mark();
  });
})();
