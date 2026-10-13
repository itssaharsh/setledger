---
id: ADR-0001
type: decision
title: Embeddings nominate, fingerprints confirm (T07 retrieval design)
status: active
scope: project
components: src/setledger/scan.py, src/setledger/store.py, src/setledger/embed.py
triggers: 
evidence: 
verified_at: 2026-10-13@4e427a2
relates: 
supersedes: 
helpful: 0
harmful: 0
cite_hash: e3b0c44298fc1c14
created: 2026-10-13
source: unknown
---

Problem: 25 synthetic techno tracks sound alike; a CPU audio embedding (CLAP) may not discriminate finely enough to CLAIM a tempo-shifted play, and claiming off cosine similarity alone risks false claims (money stakes). Decision: two-stage. (1) RECALL: CLAP window embeddings in pgvector; for each review segment, top-k nearest windows nominate candidate tracks (small crate -> weak discrimination suffices). (2) PRECISION: for each candidate, atempo-sweep the review segment (0.90..1.10 step 0.02 via ffmpeg) and re-run the proven chromaprint exact matcher; claim only on a passing exact run, method=embedding, confidence from that run's BER. Consequences: the embedding layer can never invent a claim (exact verification gates it); tempo window explicit; degraded mode = skip stage 1, exact-only banner. Post line: embeddings nominate, fingerprints confirm.