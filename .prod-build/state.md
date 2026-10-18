# state (updated Oct 16)

## Done (11/15): T01-T10, T13. Remaining: T11 deploy (DO-gated), T12 final README, T14 final take, T15 post+submit.

## Headline numbers (E0067-E0070)
6 synthetic sets / 48 plays: clean 30/30, tempo-shifted 12/12, foreign 0/6 claimed, 0 false claims.
Demo set scan 25.8 s (CLAP), exact-only 2.6 s. Clean-room install reproduces the world byte-for-byte.
Tags: Gemma 3 1B local, 14/25 normalized, 7 guarded to passthrough, 0 invented words.

## Since Oct 15
- Ledger (src/setledger/ledger.py): plays hypertable + plays_monthly continuous aggregate on
  TimescaleDB; plain table+view fallback. Verified on embedded Postgres 16 (pgserver).
- F-0002 fixed: merge_fragments joins same-track claims <=5 s apart (one play was filed as 2-3).
- T06 closed on real pgvector via pgserver (tests/conftest.py pg_dsn: env > tiger file > pgserver).
- DO deploy kit validated offline (scripts/deploy_do.sh, deploy/*). DO Serverless Inference
  client for Gemma (tags.OpenAICompatClient) ready; key file ~/.config/setledger/do_model_key.
- UI: legend card, job-naming tagline, join-running-scan, driver-honest claim text, Gemma +
  ledger artifacts one click from the crate panel, ?record=1&autoscan=1 recording mode.

## SUBMISSION STATE (Oct 18 ~23:50 IST; deadline Mon Oct 19 12:29 IST)
LIVE: https://setledger.risp.tech (Vercel static page, HTTPS) -> real scans on
https://setledger.onrender.com (Render Starter, spectral, pgvector on Tiger); 6 s fallback to the
labeled replay. Verified signed-out: live scan 25.6 s, 7 plays, report download, 0 errors;
foldcheck PASS (finished-run state) both sizes. Tiger verified (11/11 incl. hypertable + cagg).
Repo: github.com/itssaharsh/setledger PRIVATE (0 secrets in 39 commits).
dev.to draft id 4873010 (unpublished), source ../hackathon-idea/hf26-week2-side-hustle/post-final.md.
Remaining (Saharsh): "make it public" -> gh repo edit --visibility public; then Publish the draft.
After judging: delete the Render service, rotate the Tiger password, revoke the dev.to key.

## Ops note
Never `pkill -f <pattern>` from a Bash call whose own command line contains the pattern: it
kills itself (exit 144). Track servers by PID in the scratchpad.
