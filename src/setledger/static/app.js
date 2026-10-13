/* SetLedger workspace: real pipeline events only; ?state= forces demo states. */
"use strict";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const FORCED = params.get("state"); // empty | error | degraded | null

const S = {
  world: null,          // /api/state payload
  claims: [],           // streaming claims (during scan)
  plays: [],            // final timeline (claims + review spans)
  selected: null,
  polling: null,
  drawn: 0,             // staggered draw cursor
};

const fmtT = (s) => {
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
};

// ---------- data ----------

async function loadWorld() {
  const r = await fetch("/api/state");
  S.world = await r.json();
  if (!S.world.ready && !FORCED) {
    $("stage-status").textContent = "loading demo world…";
    setTimeout(loadWorld, 700);
    return;
  }
  renderCrate();
  applyForcedState();
  if (S.world.degraded || FORCED === "degraded") $("degraded-banner").hidden = false;
  if (S.world.scan && S.world.scan.plays.length) {
    S.plays = S.world.scan.plays;
    S.claims = S.plays.filter((p) => p.status === "matched");
    finishScanUI(S.world.scan);
  }
  draw();
  loadBenchSummary();
}

function applyForcedState() {
  if (FORCED === "empty") { S.plays = []; S.claims = [];
    $("claims-hint").textContent = "0 matches — this crate and set share no audio. Index the right crate?"; }
  if (FORCED === "error") showError("Could not decode set recording: bad_upload.aac (demo of the error state)");
  if (params.get("reset") === "1") fetch("/api/reset", { method: "POST" });
}

function showError(msg) { const b = $("error-banner"); b.textContent = msg; b.hidden = false; }

// ---------- scan ----------

async function startScan() {
  $("scan-btn").disabled = true;
  $("error-banner").hidden = true;
  S.claims = []; S.plays = []; S.drawn = 0; S.selected = null;
  $("detail").hidden = true; $("export-box").hidden = true;
  const r = await fetch("/api/scan", { method: "POST" });
  if (!r.ok) { const e = await r.json(); showError(e.error || "scan failed to start"); $("scan-btn").disabled = false; return; }
  S.polling = setInterval(pollScan, 400);
}

async function pollScan() {
  const sc = await (await fetch("/api/scan/progress")).json();
  if (sc.phase === "exact" && sc.total) {
    $("stage-status").textContent = `matching crate ${sc.step}/${sc.total} — ${sc.file}`;
    $("scan-btn").textContent = `Scanning… ${sc.step}/${sc.total}`;
  } else if (sc.phase === "embedding") {
    $("stage-status").textContent = "embedding recall on unmatched segments…";
  }
  S.claims = sc.claims;
  draw();
  if (sc.done) {
    clearInterval(S.polling);
    if (sc.error) { showError(sc.error); $("scan-btn").disabled = false; $("scan-btn").textContent = "Scan Saturday’s set"; return; }
    S.plays = sc.plays;
    finishScanUI(sc);
  }
}

function finishScanUI(sc) {
  $("stage-status").textContent = sc.elapsed ? `scan ${sc.elapsed}s` : "";
  $("scan-btn").hidden = true;
  const n = S.plays.filter((p) => p.status === "matched").length;
  $("export-btn").textContent = `Download PRS pack (${n} plays)`;
  $("export-btn").hidden = false;
  $("again-btn").hidden = false;
  draw(); renderClaims();
}

async function doExport() {
  const r = await fetch("/api/export");
  if (!r.ok) { showError("nothing to export yet"); return; }
  const d = await r.json();
  const rows = S.plays.filter((p) => p.status === "matched");
  const reg = new Map(S.world.tracks.map((t) => [t.file, t.registered]));
  $("export-box").hidden = false;
  $("export-body").innerHTML =
    rows.map((p) => `<div class="pack-row"><span>${esc(title(p.track_file))}</span>` +
      (reg.get(p.track_file) ? `<span class="mono">OK</span>`
                             : `<span class="warn">NO — register to claim</span>`) + `</div>`).join("") +
    `<p><a href="${d.csv}" download>CSV</a> · <a href="${d.pdf}" download>PDF</a> — ${d.rows} rows</p>`;
  window.location = d.csv;
}

function resetAll() {
  fetch("/api/reset", { method: "POST" }).then(() => {
    S.claims = []; S.plays = []; S.selected = null;
    $("scan-btn").hidden = false; $("scan-btn").disabled = false;
    $("scan-btn").textContent = "Scan Saturday’s set";
    $("export-btn").hidden = true; $("again-btn").hidden = true;
    $("detail").hidden = true; $("export-box").hidden = true;
    $("claims-list").innerHTML = `<p class="muted">Press Scan.</p>`;
    $("stage-status").textContent = "";
    draw();
  });
}

// ---------- rendering ----------

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const title = (f) => (f || "").replace(/^\d+_/, "").replace(/\.wav$/, "").replaceAll("_", " ");

function methodGlyph(m) { return m === "embedding" ? "≈" : "▮"; }
function methodColor(m) {
  return m === "embedding" ? cssVar("--data-embedding") : cssVar("--data-exact");
}
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function draw() {
  const cv = $("timeline"), ctx = cv.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr; ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);
  if (!S.world || !S.world.waveform.length) return;
  const dur = S.world.duration, peaks = S.world.waveform;
  const waveTop = 24, waveH = H * 0.52, mid = waveTop + waveH / 2;
  const laneY = waveTop + waveH + 14, laneH = Math.max(34, H * 0.2);

  // waveform
  ctx.fillStyle = cssVar("--line-input");
  const bw = W / peaks.length;
  peaks.forEach((p, i) => {
    const h = Math.max(1, p * waveH);
    ctx.fillRect(i * bw, mid - h / 2, Math.max(1, bw - 0.4), h);
  });

  // claims lane
  const items = (S.plays.length ? S.plays : S.claims);
  items.forEach((p, i) => {
    const x = (p.t_start / dur) * W, w = Math.max(2, ((p.t_end - p.t_start) / dur) * W);
    ctx.save();
    ctx.beginPath(); ctx.rect(x, laneY, w, laneH); ctx.clip();
    if (p.status === "review") {
      ctx.strokeStyle = cssVar("--data-review"); ctx.setLineDash([5, 4]); ctx.lineWidth = 1.5;
      ctx.strokeRect(x + 1, laneY + 1, w - 2, laneH - 2);
      ctx.setLineDash([]);
      ctx.fillStyle = cssVar("--data-review"); ctx.font = `10px ${cssVar("--ff-mono")}`;
      const label = w > 150 ? "review — not claimed" : "review";
      if (w > 48) ctx.fillText(label, x + 6, laneY + laneH / 2 + 3);
    } else {
      ctx.fillStyle = methodColor(p.method);
      ctx.globalAlpha = S.selected === i ? 1 : 0.85;
      ctx.fillRect(x, laneY, w, laneH);
      ctx.globalAlpha = 1;
      ctx.fillStyle = "#0b0f18"; ctx.font = `11px ${cssVar("--ff-mono")}`;
      if (w > 44) ctx.fillText(p.confidence.toFixed(2), x + 5, laneY + 14);
      if (w > 120) ctx.fillText(title(p.track_file), x + 5, laneY + laneH - 7);
    }
    ctx.restore();
  });

  // time axis (skip a final label that would clip at the right edge)
  ctx.fillStyle = cssVar("--ink-muted"); ctx.font = `10px ${cssVar("--ff-mono")}`;
  for (let t = 0; t <= dur; t += 60) {
    const x = (t / dur) * W + 2;
    if (x < W - 36) ctx.fillText(fmtT(t), x, H - 4);
  }

  drawChips(items);
  cv.onclick = (ev) => {
    const r = cv.getBoundingClientRect();
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    if (my < laneY || my > laneY + laneH) return;
    const idx = items.findIndex((p) => mx >= (p.t_start / dur) * W && mx <= (p.t_end / dur) * W);
    if (idx >= 0) selectClaim(items, idx);
  };
}

function drawChips(items) {
  $("chips").innerHTML = items.map((p, i) =>
    p.status === "review"
      ? `<span class="chip chip-review">${fmtT(p.t_start)} review</span>`
      : `<span class="chip" data-i="${i}"><span class="glyph-${p.method}">${methodGlyph(p.method)}</span> ` +
        `${fmtT(p.t_start)}–${fmtT(p.t_end)} · ${p.confidence.toFixed(2)}</span>`).join("");
  [...document.querySelectorAll(".chip[data-i]")].forEach((el) =>
    el.addEventListener("click", () => selectClaim(items, +el.dataset.i)));
}

function selectClaim(items, i) {
  S.selected = i;
  const p = items[i];
  $("detail").hidden = false;
  $("detail-body").innerHTML = `<dl>
    <dt>track</dt><dd>${esc(title(p.track_file))} <span class="mono muted">${esc(p.track_file || "")}</span></dd>
    <dt>span</dt><dd class="mono">${fmtT(p.t_start)}–${fmtT(p.t_end)} (${(p.t_end - p.t_start).toFixed(0)}s)</dd>
    <dt>confidence</dt><dd class="mono">${p.confidence.toFixed(3)}</dd>
    <dt>method</dt><dd>${p.method === "embedding"
      ? "≈ embedding recall — nominated by CLAP vectors, confirmed by a tempo-swept fingerprint run"
      : "▮ exact — contiguous low bit-error chromaprint run"}</dd></dl>`;
  draw();
}

function renderClaims() {
  const rows = S.plays.map((p) =>
    p.status === "review"
      ? `<div class="claim-row review-row"><span class="t">${fmtT(p.t_start)}–${fmtT(p.t_end)}</span>review — not claimed</div>`
      : `<div class="claim-row"><span class="t">${fmtT(p.t_start)}–${fmtT(p.t_end)}</span>` +
        `<span>${esc(title(p.track_file))}</span>` +
        `<span class="conf"><span class="glyph-${p.method}">${methodGlyph(p.method)}</span> ${p.confidence.toFixed(2)}</span></div>`);
  $("claims-list").innerHTML = rows.join("") || `<p class="muted">0 matches — this crate and set share no audio.</p>`;
}

function renderCrate() {
  $("crate-count").textContent = S.world.tracks.length;
  $("crate-list").innerHTML = S.world.tracks.map((t) =>
    `<li>${esc(t.title)} — ${esc(t.artist)} ${t.registered ? '<span class="reg">REGISTERED</span>' : ""}</li>`).join("");
  if (S.world.skipped.length)
    $("crate-list").innerHTML += `<li class="muted">${S.world.skipped.length} file(s) skipped (undecodable)</li>`;
}

async function loadBenchSummary() {
  try {
    const b = await (await fetch("/api/bench")).json();
    const full = b.bench_full_clap || b.bench_exact_only || "";
    const line = (full.match(/precision[^\n]*/) || [""])[0];
    $("bench-summary").textContent = line || "see benchmark";
    $("bench-link").onclick = (e) => {
      e.preventDefault();
      $("bench-body").textContent = Object.entries(b).filter(([k]) => k !== "tags")
        .map(([k, v]) => `── ${k} ──\n${v}`).join("\n");
      $("bench-dialog").showModal();
    };
  } catch { $("bench-summary").textContent = "unavailable"; }
}

// ---------- wiring ----------

$("scan-btn").addEventListener("click", startScan);
$("export-btn").addEventListener("click", doExport);
$("again-btn").addEventListener("click", resetAll);
$("judge-dismiss").addEventListener("click", () => ($("judge-card").hidden = true));
addEventListener("keydown", (e) => {
  if (e.altKey && e.shiftKey && e.key.toLowerCase() === "r") resetAll();
});
addEventListener("resize", draw);
loadWorld();
