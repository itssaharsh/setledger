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

## PUBLISH BLOCKERS (do not claim in the post until each is true)
1. Tiger: run tests/test_ledger.py + scripts/ledger_demo.py against the Tiger DSN so the
   hypertable/continuous-aggregate test passes and the artifact says TimescaleDB.
   Needs: ~/.config/setledger/tiger_dsn (Saharsh's 30-second terminal save).
2. DigitalOcean: live URL from scripts/deploy_do.sh setledger.risp.tech. Needs DO token.
3. .tech: A record setledger.risp.tech -> droplet IP. Needs droplet IP + DNS panel access.
4. Git history rewrite decision before first push (380 MB of removed audio in history).

## Ops note
Never `pkill -f <pattern>` from a Bash call whose own command line contains the pattern: it
kills itself (exit 144). Track servers by PID in the scratchpad.
