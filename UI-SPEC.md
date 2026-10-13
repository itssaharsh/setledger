---
name: SetLedger
scope: sprint
one_line: "SetLedger turns a DJ's set recording into a filed royalty claim"
ambition: L1            # temporal data on a timeline; money stakes → calm, precise; no 3D signal
registers: { workspace: productive, export: celebratory, review: serious }
direction: "derived from the DAW/club-night world: DAWs (Serato, rekordbox) are dark studio tools → dark canvas justified by use scene; cue-marker amber accent on deep navy; timecode mono. Calibrated from Sodium Night with mutations: hue −12°, display face from flyer lettering, surface ladder redesigned. canvas #10141F · surface-1 #171C2B · surface-2 #1F2537 · line #2E3650 · line-input #566083 · ink #F2EADB · ink-muted #A9A293 · accent #FFA41B (accent-ink #1A1000) · success #4CC38A · warning #FFD24A · danger #FF7A7A · data-exact #4CC38A · data-embedding #7AA7FF · data-review #FFD24A (candidates: Big Shoulders Display 700 [flyer condensed], Familjen Grotesk, Azeret Mono [timecode]; 2 non-pool candidates checked: Clash Display (rejected, convergence set), Archivo Expanded (rejected, weaker at condensed flyer register))"
personality: precise
dials: { variance: 4, motion: 4, density: 6 }
stack: { page: "FastAPI-served single page, vanilla JS + canvas timeline", fonts: "Google Fonts", reason: "no routes, no SSR; one screen" }
archetype: dashboard-lite (single work surface + side panel)
viewports: [390x844, 1024x768, 1440x900]
signature: { interaction: "claims draw onto the waveform left-to-right as the scan streams, each landing with its confidence chip", visual: "timecode mono chips in cue-marker amber; method glyph per claim (exact ▮ / embedding ≈)" }
wow: "press Scan on the seeded set → waveform fills with labeled claims in ~20s → the +6% tempo-shifted play lands with an '≈ embedding' chip → residue: the pack row counter ticks to 7 → inspectable: download the PRS pack, open the bench table"
demo: { seed: "baked demo crate + demo_set.wav", flag: "default view IS the seeded demo", state_param: "?state=", reset: "alt+shift+r", replay: "n/a (scan is deterministic on the seed)", guest: true }
live_vs_simulated: [ "fingerprint + embedding matching: live", "tag normalization (Gemma): live or passthrough-flagged", "PRS filing: manual (documented in post); pack generation live" ]
deviations:
  - "dark canvas as default: world-derived (DAW convention, studio use scene) — logged per B2"
  - "no landing page: judges arrive from the DEV post; the tool IS the first screen with a judge card"
---
## 0. Brief + context profile
One line above. User/moment: DJ-producer, morning after a gig, laptop in the studio, recording + crate folder at hand. Promise: "SetLedger" = a ledger of what was played → the claims timeline is the first screen; "PRS-ready" = the pack download + format columns. Primary action: **Scan Saturday's set** (seeded). Hero object: the set waveform with claims drawn on. Product moment: claims + confidences land on the waveform. Demo moment: the ≈ embedding chip catching the tempo-shifted play. Differentiator: per-play evidence (confidence, method) + "never claims what it can't prove" review spans. Artifact: the PRS-format pack (CSV+PDF). World inventory: waveform, cue markers, CDJ display, PRS paper form, club flyer lettering, mixer channel strip. Moving truth: scan progress (real pipeline events). Judging: writing-first DEV post; judges click the demo link; minutes unknown. Budget: ≤6h UI inside T09.
Context: task freq = few times/week → guidance over density · data density mid · stakes = money → calm, explicit uncertainty · audience = DJs (domain terms fine: BPM, cue, PRS) · scene = studio desk → dark OK · emotion: **"finally, receipts"** · sponsor: none shown in UI (partner jobs live in the post).

## 1. Judge tests
5s: dark studio screen, a big waveform mid-scan with green/blue claim blocks and timecode chips; title bar "SetLedger — every play, claimed with evidence"; one amber button "Scan Saturday's set". Stranger names: DJs / getting royalties from a set recording.
30s: press Scan → claims stream on → ≈ chip lands on the shifted play → review span stays amber ("won't claim without evidence") → Export → pack downloads; bench table linked ("How accurate? See the measured numbers").
Demo-critical screen: S1 (the only screen). 3 stills: mid-scan timeline · the ≈ embedding chip close-up · the export panel with the pack rows.
Judge card (top of page, 90s how-to): 1) Press Scan 2) watch claims land with confidence 3) click the ≈ chip — that play is tempo-shifted +6%, caught by embeddings 4) note the amber span: unmatched stays unclaimed 5) Download the PRS pack.

## 3. Screen inventory
| id | route | why | primary action | states |
|---|---|---|---|---|
| S1 | / | the whole product: crate panel, waveform timeline, claims list, export | Scan | first-run (no crate → seeded demo banner), scanning (progress on waveform), success (claims), empty (0 matches + why panel), error (bad file, named), degraded (no embedding model → banner "exact-only mode"), review spans |
| S2 | /bench | the measured numbers (linked from post + S1 footer) | — | static table render |

## 5. S1 treatment table
| Element | Tier | Register | Levers (grayscale first) | States | Transition |
|---|---|---|---|---|---|
| Waveform timeline with claims drawn on | primary | productive | full-width, 55% of viewport height, edge-to-edge dark stage; claims as blocks UNDER the wave; only saturated marks on screen | scanning: progress sweep + per-track tick counter "matching crate 14/25"; empty: wave alone + "0 matches — crate and set share no audio" panel | claims draw left→right in timestamp order, 40ms stagger |
| Claim chips (timecode + confidence + method glyph) | secondary | productive | 12px Azeret Mono, amber on surface-2; ▮ exact / ≈ embedding | click → claim detail (track, span, confidence, method) in side panel | chip lands with 120ms scale .96→1 |
| Review spans | secondary | serious | hatched amber outline, no fill; label "review — not claimed" | click → snippet player + "why unmatched" | none (serious register) |
| Scan button | interactive | productive | the one filled amber button, header right | loading: "Scanning… 3/25" width-locked; done → morphs to "Export pack" secondary +scan again ghost | label morph 180ms |
| Export panel / pack rows | secondary | celebratory (on download) | right panel 320px, rows not cards; registered=NO rows carry warning glyph + "register to claim" | success: download starts, row counter ticks (NumberFlow-style) | panel slides 240ms |
| Crate panel | tertiary | system | collapsed list, 13px, count + skipped files line | indexing: per-file progress | — |
| Degraded banner | system | system | full-width line under header: "Embedding model unavailable — exact-only mode" | — | — |
| Bench footer link | tertiary | system | "Measured: clean 100% · shifted (exact) 0% full/2 partial — see benchmark" | — | — |

## 9. Key copy
Title: "SetLedger". Descriptor: "every play, claimed with evidence". Scan: "Scan Saturday's set". Empty: "0 matches — this crate and set share no audio. Index the right crate?" Review span: "review — not claimed". Degraded: "Embedding model unavailable — running exact-only. Shifted plays may be missed, never misclaimed." Export: "Download PRS pack (7 plays)". Unregistered row: "NO — register to claim". Sample label: "Demo: builder-owned synthetic crate + set (see corpus notes)".

## 11. Real-product checks
Value: yes — the pack is filable. Clarity: judge card + one button. Trust: every claim carries confidence+method; review spans visible; no invented tracks by construction. Feedback: scan progress streams real pipeline events. Failure: bad audio → named file skip; model missing → exact-only banner. 10× data: timeline virtualizes by zoom; claims list paginates at 50. Maintainability: vanilla JS + one canvas component; each dep one line.

## 14. Tokens
:root{ --canvas:#10141F; --surface-1:#171C2B; --surface-2:#1F2537; --surface-sunken:#0C0F18;
--line:#2E3650; --line-input:#566083; --ink:#F2EADB; --ink-muted:#A9A293;
--accent:#FFA41B; --accent-ink:#1A1000; --focus:var(--accent);
--success:#4CC38A; --warning:#FFD24A; --danger:#FF7A7A;
--data-exact:#4CC38A; --data-embedding:#7AA7FF; --data-review:#FFD24A;
--ff-display:"Big Shoulders Display",system-ui; --ff-body:"Familjen Grotesk",system-ui; --ff-mono:"Azeret Mono",ui-monospace;
--r-sm:4px; --r-md:8px; --r-lg:12px; }
Contrast gate run at build (ink/canvas, ink-muted/canvas, accent on canvas ≥3:1 for focus, chips composited).
