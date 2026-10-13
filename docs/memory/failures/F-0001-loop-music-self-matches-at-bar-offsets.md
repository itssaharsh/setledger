---
id: F-0001
type: failure
title: loop music self-matches at bar offsets
status: active
scope: global-candidate
components: src/setledger/scan.py
triggers: 
evidence: E0015
verified_at: 2026-10-13@945eeff
relates: 
supersedes: 
helpful: 0
harmful: 0
cite_hash: e3b0c44298fc1c14
created: 2026-10-13
source: unknown
---

Attempted: pick alignment lag by global min BER. Error: first play rejected (run 14.1s < MIN_PLAY_S); best lag was a 4-bar self-similar offset with tiny overlap (BER 0.019). Root cause: loop-based music has near-zero BER at bar-multiple lags; min-BER prefers short diluted self-matches, and max-raw-overlap over-claims across non-play audio. Fix: collect candidate lags <= 1.5*MATCH_BER, evaluate trimmed low-error RUN length per candidate, pick longest run. Lesson: when matching repetitive audio, optimize claimable run length, not raw similarity; min-error and max-overlap objectives fail differently.