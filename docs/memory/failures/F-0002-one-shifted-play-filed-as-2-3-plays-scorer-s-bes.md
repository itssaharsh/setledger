---
id: F-0002
type: failure
title: One shifted play filed as 2-3 plays; scorer's best-single-fragment rule hid working recall
status: active
scope: project
components: src/setledger/scan.py, src/setledger/bench.py, src/setledger/ledger.py
triggers: partial, fragment, shifted tier, plays count, duplicate rows
evidence: test:pytest tests/test_scan.py -k merge
verified_at: 2026-10-16@a7f4218
relates: 
supersedes: 
helpful: 0
harmful: 0
cite_hash: e3b0c44298fc1c14
created: 2026-10-16
source: unknown
---

Attempt: bench on the single demo set said shifted 2/2. Failure: a 5-gig benchmark said shifted 4/10 (6 partial) AND the ledger showed one track with 5 'plays' in 2.9 min. Root cause: a tempo-shifted play is proven in pieces (exact run + recall-confirmed runs either side, 2-3 s apart); scan emitted each piece as a Play. The scorer credits the best SINGLE overlapping claim, so a play covered 91% by three pieces scored 'partial'; the ledger and the PRS pack counted every piece as a play. Fix: merge_fragments() joins same-track claims <=5 s apart (method embedding if any piece needed recall, confidence = weakest piece). Lesson: benchmark on more than the demo set before quoting a tier rate, and check count-type outputs (plays per track) not just coverage.