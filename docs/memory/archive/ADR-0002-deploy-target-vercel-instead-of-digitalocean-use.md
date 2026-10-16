---
id: ADR-0002
type: decision
title: Deploy target: Vercel instead of DigitalOcean (user call)
status: superseded
scope: project
components: src/setledger/app.py, vercel.json, api/
triggers: 
evidence: 
verified_at: 2026-10-15@b021220
relates: 
supersedes: 
helpful: 0
harmful: 0
cite_hash: e3b0c44298fc1c14
created: 2026-10-15
source: unknown
superseded_by: ADR-0003
---

Saharsh: DO console unusable for him (team-permission wall on payment method), use Vercel. Consequences: (1) the DigitalOcean $200 partner lane is forfeited unless he later fixes DO billing (3-min, optional); (2) .tech lane survives via subdomain of his existing portfolio .tech (CNAME -> Vercel); (3) architecture adapts to serverless: synchronous /api/scan (no polling state), spectral embedder only (no torch), fpcalc+ffmpeg static binaries bundled, crate FINGERPRINTS+EMBEDDINGS shipped as committed npz (no crate audio at runtime), demo_set.wav served as a static asset fetched to /tmp on cold start, export via POST with plays. Vercel MCP connector available in-session -> deploys need nothing from Saharsh.