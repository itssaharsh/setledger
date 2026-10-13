# state (M1 complete, 2026-10-13 16:20 IST)
DONE: T01 corpus+mixgen · T02 indexer+store · T03 exact scanner · T04 bench · T05 PRS export · T13 backup take.
Full-scale demo (423s set, 25-track crate): clean 5/5, shifted 0/2 found but 2/2 PARTIAL (correct track,
~30% coverage — exact locks a short stable stretch of ±5-6% shifted plays), foreign 0 claimed,
precision 100%, scan 2.6s. Artifacts in docs/media/.
KEY LESSON (F-0001): loop music self-matches at bar offsets → pick lag by longest low-error run.
NEXT: T06 pgvector (GATED: docker daemon down; need Docker Desktop started OR Tiger Data DSN) ·
T08 Gemma/ollama (user-space install attempt) · then T07 embeddings (critical path) · T09 UI.
USER GATES: DO account, .tech claim (hacktoberfest promos), Tiger signup or Docker Desktop.
Suite: .venv/bin/python -m pytest -q (10 tests green).
