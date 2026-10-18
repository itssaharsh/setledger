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

## PUBLISH BLOCKERS (Oct 18 21:30 IST; deadline Mon Oct 19 12:29 IST)
DigitalOcean dropped (support never unblocked the account). Hosted demo = static replay of a
real recorded scan on Vercel: https://setledger.vercel.app (verified signed-out, foldcheck PASS
both sizes, judge path end to end). setledger.risp.tech attached on Vercel.
1. Tiger: needs ~/.config/setledger/tiger_dsn -> run tests/test_ledger.py + ledger_demo.py on it.
   If it never arrives: rewrite the Tiger paragraph to "verified on Postgres 16 + pgvector; the
   TimescaleDB path is untested" and decide whether to still list the category.
2. DNS: A record setledger.risp.tech -> 76.76.21.21 at get.tech (orderbox-dns).
3. GitHub: "push as-is" or "rewrite approved", then gh repo create + push; rebuild site with
   repo URL (scripts/build_static.py <url>) and redeploy so the replay chip links to it.
4. Post: fill the GIF (upload docs/media/demo.gif in the dev.to editor) and repo link; Saharsh
   publishes. Draft: ../hackathon-idea/hf26-week2-side-hustle/post-draft.md

## Ops note
Never `pkill -f <pattern>` from a Bash call whose own command line contains the pattern: it
kills itself (exit 144). Track servers by PID in the scratchpad.
