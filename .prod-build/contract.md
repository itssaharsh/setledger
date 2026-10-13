# SetLedger — implementation contract (hackathon mode)

## 1. Intent
A DJ-producer drops a set recording and their crate folder; SetLedger produces a timestamped,
confidence-scored tracklist matched against that crate and exports a PRS-format royalty submission
pack. After use: plays that would have gone unreported exist as a filable claim with evidence.

## 2. Scope
MUST: crate indexing (fingerprints + embeddings) · set scan with exact matches · timestamped
tracklist with per-play confidence · embedding fallback for tempo-shifted plays (one shift family)
· ground-truth benchmark on synthetic mixes · PRS-format CSV/PDF pack export · unreleased-track
flags · web UI (upload → timeline → export) · deployed demo with seeded example.
SHOULD: Gemma tag normalization (messy ID3 → canonical) · pitch-shift robustness · .tech domain ·
Tiger Data cloud as the vector store (dev may use local pgvector).
COULD: PPL spreadsheet export · play-history archive view · MusicBrainz lookup.
WON'T now: multi-DJ accounts, auth, overlap/transition untangling, Temporal re-indexing, payments.

## 3. Core journeys
J1 (golden path): upload crate folder → index → upload set recording → scan → timeline renders →
export PRS pack. J2: benchmark run → ID-rate/precision table. J3 (judge): open seeded demo signed
out → press scan on preloaded set → see timeline → download pack.

## 4. Required surfaces
Web UI (single page: crate panel, scan panel, timeline, export) · CLI (index/scan/bench for the
post's terminal screenshots) · results CSV/JSON.

## 5. States per journey
J1: initial (empty crate prompt) · loading (indexing progress, scan progress) · success (timeline)
· error (unreadable audio file → named file + skip) · empty (no matches → "0 matches, here's why"
panel) · partial (low-confidence plays segregated "review manually") · retry (re-scan button) ·
disabled (export disabled until scan done) · permission-denied n/a (no auth by design, read-only
demo) · offline/degraded (embedding model unavailable → exact-match-only mode, banner says so).
J2: success table · error (missing ground truth → fail loudly). J3 = J1 success path, seeded.

## 6. Data
Entities: Track (path, tags, canonical title/artist, registered_flag, fingerprint, embedding[]) ·
Set (file, duration, scanned_at) · Play (set_id, track_id?, t_start, t_end, method exact|embedding,
confidence, status matched|review|unmatched) · BenchmarkRun (mix_id, truth, results, metrics).
Source of truth: postgres (pgvector) for tracks/embeddings; SQLite fallback forbidden once Tiger is
up (one datastore). Files on disk under data/ (gitignored).

## 7. Interfaces & failure modes
- fpcalc/Chromaprint CLI: missing binary → startup check with install hint; corrupt audio → skip+log.
- ffmpeg decode: fails → named-file error, journey continues with remaining files.
- Embedding model (CLAP/MERT via transformers, CPU dev / DO GPU prod): load fails → exact-only mode.
- Postgres+pgvector (Tiger Data cloud or local docker): down → startup fail with clear message
  (no silent SQLite fallback in prod path).
- Gemma (local/ollama or droplet): unavailable → tags pass through unnormalized, flag in UI.
- No external paid APIs. No auth. Demo is read-only seeded + rate-limited upload size (100 MB).

## 8. Constraints
Judging (quoted): "Writing Quality (weighted most heavily)" · "Relevance to the Prompt and Theme" ·
"Creativity" · "Technical Execution" · "Use of Partner Technology (optional)"; "your write-up
matters most"; theme: "Build something with open-weight models or open-source AI that someone would
actually pay for"; "Bonus points if you land your first user."
Deadline: submission (DEV post) by 2026-10-18 23:59 PDT; freeze 2026-10-18 17:46 IST.
Budget: free credits only (DO/.tech claims pending user). Latency: scan of a 60-min set ≤ 5 min on
droplet; demo scan of seeded 10-min set ≤ 30 s. Platform: Linux/WSL dev, DO droplet prod.
Security: no secrets in repo; upload size cap; no user data (builder's own audio only).

## 9. Acceptance criteria
- WHEN a crate of ≥20 tracks is indexed THE SYSTEM SHALL store fingerprints and embeddings for
  every decodable track and report skipped files. CHECK: pytest tests/test_index.py + CLI run log.
- WHEN a synthetic mix with known tracklist is scanned THE SYSTEM SHALL produce a tracklist whose
  exact-segment ID rate ≥ 90% on unmodified segments. CHECK: pytest tests/test_bench.py (metric asserted).
- WHEN a segment is tempo-shifted +6% THE SYSTEM SHALL match it via embeddings in ≥ 50% of
  benchmark cases, each labeled method=embedding. CHECK: benchmark table artifact + test threshold.
- WHEN no match clears threshold THE SYSTEM SHALL emit status=review with timestamps, never a
  silent omission or invented track. CHECK: tests/test_scan.py::test_unmatched_never_invented.
- WHEN export is requested THE SYSTEM SHALL produce a PRS-format pack whose rows equal matched
  plays and carry confidence + method columns. CHECK: tests/test_export.py golden file.
- WHEN the embedding model is unavailable THE SYSTEM SHALL run exact-only with a visible banner.
  CHECK: tests/test_degraded.py + UI screenshot.
- WHEN the deployed demo is opened signed-out THE SYSTEM SHALL show the seeded set and allow scan
  + export without any account. CHECK: pb.py smoke + browser pass (observed evidence).

## 10. Assumptions
- SAFE: CC-licensed audio from Pixabay/FMA may be used for the demo corpus (verify license lines, cite).
- REVERSIBLE: MERT-class embeddings on 10s windows beat CLAP for music similarity (swap = config).
- REVERSIBLE: 10s window, 5s hop, cosine threshold tuned on bench.
- BLOCKING (user): DO account + .tech claim + Tiger Data signup — build proceeds locally regardless;
  deploy tasks block on these only.
- SAFE: PRS "format" = their reported-fields schema rendered as CSV+PDF; the filing attempt is
  documented honestly in the post (accept or reject).

## 11. Demo script (for post GIF + judge path)
Seeded demo: crate of 25 CC tracks, 12-min synthetic set (8 plays: 5 clean, 2 tempo-shifted,
1 foreign track not in crate). Open demo → press Scan → timeline fills in ~20 s → one chip shows
"embedding +6%" → one segment shows "unmatched — review" → Export → pack opens with 7 rows.
GIF = the timeline fill + chip close-up. Terminal shot = bench table.

## 12. Event & judged path
- Window: start 2026-10-12T19:01:08Z, end 2026-10-19T06:59:00Z ("Week 2 ... starts today!" posted
  Oct 12; "Submissions due October 18"; weekly rules: 11:59 PM PDT). Freeze set 2026-10-18T12:16Z.
- Earlier-work rule (quoted): "Each project must be new and built during that challenge's window.
  Pull requests to existing projects don't count." Done before the clock: idea research, judging
  intel, this contract's inputs (all outside the repo, in hackathon-idea/, git-ignored); fpcalc
  install (tool). First commit 862a3ad inside the window.
- Judge path: DEV feed card (title does first contact) → the post → repo/demo links. Minutes
  unknown (announcement, series post, hub FAQ, winners post checked).
- Judge card (post title ≤180): "Clubs pay licensing; the producers a DJ actually played rarely
  see it. SetLedger turns a set recording into a timestamped, PRS-ready tracklist matched against
  the DJ's own crate."
- Criteria → shown: Writing → Aslice cold open + filing-outcome candid section; Relevance →
  own-money recovery + open models load-bearing + "why open innovation matters" (no SaaS between a
  DJ and their royalties); Creativity → only music entry; own-crate insight; Technical → benchmark
  table with failure causes; Partner → named-job sections ("Tiger holds the crate", "the droplet
  does the listening", "Gemma reads the tags, code writes the claim").
- Capability coverage: DO = host app ✓ (FastAPI on droplet) + GPU open-weight model ✓ (embeddings);
  Gradient not used (disclosed). Tiger = pgvector store ✓ (crate index) + hybrid search ✓ (tag+vector
  query). Gemma = model in workflow ✓ (tag normalization). Entire = sessions published ✓. .tech =
  live demo domain ✓.
- Climax: the exported PRS-format pack with timestamps and per-play confidence, from the builder's
  own mix of own/CC tracks (artifact; camera sees the pack + timeline).
- Promise: "SetLedger" → the confidence timeline is the first screen; "PRS-ready" → the pack in
  their format + documented filing attempt.
- First user (bonus): builder registers one own track with PRS (or documents the attempt) and files
  once; outcome reported truthfully.
