# state

## Done (10/15)
M1 vertical slice: T01-T05, T13. M2 intelligence: T07, T08 (T06 pg user-gated).
M3 surface: T09, T10.

## Headline numbers (E0044, E0050)
clean 5/5 (100%) · shifted 2/2 (100%, via embedding recall) · foreign 0 claimed ·
precision 100% · scan 25.8s (exact-only 2.6s) on 423s set / 25-track crate.
Tags: Gemma 3 1B local, 14/25 normalized, 7 guarded to passthrough, two-sided vocab guard.
UI: foldcheck PASS at 1440x900 + 390x844; stills in docs/media/ui_*.png.

## Key design records
ADR-0001 embeddings nominate, fingerprints confirm. F-0001 loop-music self-match.
Lesson: ignore generated-output dirs before first `git add -A` (380MB history bloat).

## Open decision for Saharsh
Repo history still carries ~380MB of removed generated audio. Fix = one
`git filter-repo --invert-paths` run (classifier-blocked for me). Decide BEFORE first push.

## User gates (block T06, T11, then T12/T14/T15)
1. Docker Desktop running OR Tiger Data DSN -> T06 pgvector online.
2. DigitalOcean account -> T11 deploy.
3. .tech domain claim (hacktoberfest.com/my/promos) -> T11 URL.

## Next without user
DONE Oct 15: README judge-ready (submit check 0 FAIL), DEV post drafted at
../hackathon-idea/hf26-week2-side-hustle/post-draft.md (placeholders: URL, GIF, repo link).
Remaining without user: nothing big — next steps are deploy (DO token), Tiger live run (DSN),
GitHub push (needs history-rewrite decision), final take + publish. Demo world regenerates via
scripts/fetch_corpus.py; server: uvicorn setledger.app:app --app-dir src (port 8000).
