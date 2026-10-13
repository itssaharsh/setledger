---
id: L-0001
type: lesson
title: Ignore generated-output dirs before the first git add -A
status: active
scope: project
components: .gitignore, data/
triggers: 
evidence: 
verified_at: 2026-10-13@9d0ae13
relates: 
supersedes: 
helpful: 0
harmful: 0
cite_hash: e3b0c44298fc1c14
created: 2026-10-13
source: unknown
---

git add -A in build loops swept 380MB of deterministic generated audio (crate wavs, atempo tmp slices, two copies) into history before T10 caught it. The ignore list must cover every OUTPUT directory (data/crate, data/.tmp_scan, data/out, data/mixes/.tmp) at T01, when the generator script is written, not at the cleanup gate. Removal needed a history rewrite, which the permission layer blocks without the user.