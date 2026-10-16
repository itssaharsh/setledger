---
id: ADR-0003
type: decision
title: Deploy target back to DigitalOcean (user reversal of ADR-0002)
status: active
scope: project
components: deploy/, scripts/deploy_do.sh
triggers: 
evidence: test:bash -n scripts/deploy_do.sh
verified_at: 2026-10-16@1f1db32
relates: 
supersedes: ADR-0002
helpful: 0
harmful: 0
cite_hash: e3b0c44298fc1c14
created: 2026-10-16
source: unknown
---

Saharsh reversed the Vercel pivot within a day: the DO $200 category is unclaimed money, worth waiting on DO support (account stuck at a phantom team, payment page unreachable). Kit is pre-validated without an account: doctl v2.0.0 commands/flags checked against --help, DIGITALOCEAN_ACCESS_TOKEN env confirmed honored (fake token -> 401), Caddyfile validated by caddy 2.11.7 adapt with and without a domain, clean-room install from git archive reproduces the demo byte-for-byte. Domain: setledger.risp.tech (subdomain; risp.tech root reserved for his company). Fallback if DO never unblocks before freeze: Vercel connector is live in-session.