#!/usr/bin/env python3
"""pb.py - deterministic helper for the prod-build skill (stdlib only, Python 3.8+).

It keeps the parts of a build that should not depend on an agent's memory or honesty:
the task graph, the evidence ledger, test-weakening checks, relay intake, project
memory records, the delivery-report check and, for events, the build window and
what a judge will open.

  init      create .prod-build/ and docs/memory/ (never overwrites)
  status    one-screen board: tasks, next ready work, recent evidence, memory health
  plan      check | waves | next         validate plan.json and compute parallel lanes
  task      <ID> start|done|skip|block|reopen [--evidence E0001,..] [--note ..]
  run       [--task ID] [--kind K] [--where local|preview|prod] [--expect-fail] -- <cmd>
  verify    <ID>                         run all of a task's verify commands through the ledger
  smoke     <base-url> [--routes / /x]   HTTP + asset check of a running or deployed app
  evidence  add | list | redact          record an observation, list the ledger, scrub a leaked secret
  diffcheck [--base REF | --task ID]     find skipped/deleted tests, loosened configs, secrets
  intake    <ID> [--base REF]            check a relay agent's work before accepting it
  mem       add | find | check | index | mark     project memory in docs/memory/
  report    <file>                       every verification claim must cite passing evidence
  clock     [set --start I --end I ...]  the event's build window: phase, time left, what is allowed now
  window    [--start I --end I]          the repository's history against the build window
  submit    check [--description ..] [--video ..] [--online]
                                         what a judge opens: card, README first screen, links, history, video

Run from anywhere inside the project; the root is the nearest folder holding .prod-build/
or .git (override with --root). Exit codes: 0 ok, 1 check failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

VERSION = "0.2.1"
ARGV_TEXT = [""]  # the command line as typed, for the ledger
MODES = ("hackathon", "product", "hybrid")
CHANNELS = ("direct", "relay", "chat")
STATUSES = ("todo", "doing", "done", "skip", "blocked")
KINDS = ("build", "test", "red", "lint", "typecheck", "e2e", "smoke", "security", "deploy",
         "eval", "perf", "migration", "review", "manual", "other")
MEM_TYPES = {  # type -> (id prefix, folder)
    "decision": ("ADR", "decisions"),
    "failure": ("F", "failures"),
    "lesson": ("L", "lessons"),
    "assumption": ("A", "assumptions"),
    "feedback": ("U", "feedback"),
    "pattern": ("P", "patterns"),
}
MEM_STATUSES = ("active", "superseded", "archived", "invalidated")
MEM_REQUIRED = ("id", "type", "title", "status", "scope", "components", "evidence", "verified_at")
CAPS = {"index_lines": 150, "record_lines": 60, "active": 150}
HOT_FILES = re.compile(
    r"(^|/)(package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|bun\.lockb?|"
    r"pyproject\.toml|poetry\.lock|uv\.lock|requirements[^/]*\.txt|go\.mod|go\.sum|Cargo\.(toml|lock)|"
    r"schema\.prisma|drizzle\.config\.[^/]+|tsconfig[^/]*\.json|next\.config\.[^/]+|vite\.config\.[^/]+|"
    r"docker-compose[^/]*\.ya?ml|\.env[^/]*)$|(^|/)(migrations|alembic|supabase/migrations)/|(^|/)\.github/workflows/")
TEST_FILE = re.compile(
    r"(^|/)(tests?|__tests__|spec|e2e)/|\.(test|spec)\.[cm]?[jt]sx?$|_test\.go$|(^|/)test_[^/]+\.py$|_test\.py$|_spec\.rb$")
CONFIG_FILE = re.compile(
    r"(^|/)(\.eslintrc[^/]*|eslint\.config\.[cm]?[jt]s|tsconfig[^/]*\.json|jest\.config\.[^/]+|"
    r"vitest\.config\.[^/]+|playwright\.config\.[^/]+|pytest\.ini|setup\.cfg|tox\.ini|\.coveragerc|"
    r"\.golangci\.ya?ml|biome\.jsonc?|\.prettierrc[^/]*|knip\.jsonc?|\.semgrep[^/]*|\.gitleaks\.toml)$"
    r"|(^|/)\.github/workflows/|(^|/)\.pre-commit-config\.yaml$"
    r"|(^|/)(AGENTS|CLAUDE|GEMINI)\.md$|(^|/)\.claude/(settings[^/]*\.json|agents/|hooks/)|(^|/)\.gemini/settings\.json$")
WEAKENING = [  # (regex on an added line, severity, message)
    (r"\b(describe|it|test|context|suite)\.(skip|only)\s*\(", "error", "focused/skipped JS test"),
    (r"\b(xit|xdescribe|fit|fdescribe|xtest)\s*\(", "error", "focused/skipped JS test"),
    (r"\btest\.fixme\s*\(|\btest\.fail\s*\(", "warn", "Playwright test marked fixme/fail"),
    (r"[{,]\s*skip\s*:\s*(true|[\"'])", "error", "test skipped via options ({ skip: … })"),
    (r"[{,]\s*todo\s*:\s*true", "warn", "test marked todo"),
    (r"@pytest\.mark\.(skip|xfail)|pytest\.skip\s*\(|@unittest\.skip", "error", "skipped Python test"),
    (r"\bt\.Skip(Now|f)?\s*\(", "error", "skipped Go test"),
    (r"#\[ignore\]", "error", "ignored Rust test"),
    (r"@ts-ignore|@ts-nocheck|@ts-expect-error", "warn", "type check suppressed"),
    (r"eslint-disable", "warn", "lint rule disabled"),
    (r"#\s*type:\s*ignore|#\s*noqa|//\s*nolint", "warn", "check suppressed"),
    (r"--no-verify", "error", "git hooks bypassed"),
    (r"\bdebugger\s*;", "warn", "debugger statement"),
    (r"--passWithNoTests|--pass-with-no-tests", "warn", "suite allowed to pass with no tests"),
]
SECRET_PATTERNS = [
    re.compile(r"\b(sk|rk)_(live|test)_[0-9A-Za-z]{16,}"),
    re.compile(r"\bwhsec_[0-9A-Za-z]{16,}"),
    re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}"),
    re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/]{20,}"),
    re.compile(r"\bSK[0-9a-fA-F]{32}\b"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-(proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
SECRET_ASSIGN = re.compile(
    r"(?i)\b([A-Z0-9_]*(api[_-]?key|secret|token|passw(or)?d|client[_-]?secret))\b(\s*[:=]\s*[\"']?)([^\s\"'`]{8,})")
DB_URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.\-]*://[^:/\s@]+:)([^@\s]{3,})(@)")
URL_SECRET_PARAM = re.compile(r"(?i)([?&#](?:key|token|secret|password|pass|sig|signature|access_token|api_key|apikey|auth)=)[^&\s#\"']+")
ENV_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET|WEBHOOK)[A-Z0-9_]*)\s*=\s*[\"']?([^\s\"'#]{8,})")
SECRET_LITERAL = re.compile(
    r"(?i)(api[_-]?key|secret|token|passw(or)?d|client[_-]?secret|private[_-]?key)\w*[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_\-+/=.]{16,})[\"']")
PLACEHOLDER = re.compile(r"(?i)(example|placeholder|dummy|sample|your[_-]|xxxx|changeme|replace[_-]?me|redacted|<[^>]+>|\$\{)")
EVIDENCE_ID = re.compile(r"\bE\d{4,}\b")
NOT_RUN = re.compile(r"(?i)(\bnot run\b|\bnot performed\b|\bnot applicable\b|\bn/a\b|\bnot deployed\b|\bnot verified\b)")
REPORT_SECTIONS = ("built", "architecture", "verification", "deployment", "bugs", "known limitations",
                   "cleanup", "optimization", "memory", "next steps")
CLAIM_SECTIONS = ("verification", "deployment", "bugs")


# ----------------------------------------------------------------------------- utilities

def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return _dt.date.today().isoformat()


def die(msg: str, code: int = 2) -> None:
    print(f"pb: {msg}", file=sys.stderr)
    sys.exit(code)


def find_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / ".prod-build").is_dir():
            return p
    for p in [here, *here.parents]:
        if (p / ".git").exists():
            return p
    return here


def git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=check)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", "git not found")


def git_info(root: Path) -> dict:
    head = git(root, "rev-parse", "--short", "HEAD")
    if head.returncode != 0:
        return {"sha": None, "dirty": None}
    dirty = git(root, "status", "--porcelain", "--", ".", ":(exclude).prod-build")
    return {"sha": head.stdout.strip(), "dirty": bool(dirty.stdout.strip())}


def tree_hash(root: Path):
    """Content fingerprint of the working tree (tracked + untracked, minus ignored files and .prod-build/).
    Stored with each evidence entry so `task done` can refuse evidence gathered on different code."""
    if git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return None
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(td, "index"))

        def g(*a):
            return subprocess.run(["git", *a], cwd=root, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        g("read-tree", "HEAD")
        g("rm", "-r", "-q", "--cached", "--ignore-unmatch", ".prod-build")
        if g("add", "-A", "--", ".", ":(exclude).prod-build").returncode != 0:
            return None
        w = g("write-tree")
        return w.stdout.strip()[:12] if w.returncode == 0 else None


def redact(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    text = SECRET_ASSIGN.sub(lambda m: m.group(1) + m.group(4) + "[REDACTED]", text)
    text = DB_URL_PASSWORD.sub(lambda m: m.group(1) + "[REDACTED]" + m.group(3), text)
    text = URL_SECRET_PARAM.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return text


def looks_like_secret(line: str) -> bool:
    """High-precision check used for findings (redact() is deliberately greedy and used for logs)."""
    if PLACEHOLDER.search(line):
        return False
    if any(p.search(line) for p in SECRET_PATTERNS):
        return True
    m = SECRET_LITERAL.search(line)
    if m and len(set(m.group(3))) >= 8:
        return True
    m = ENV_ASSIGN.search(line)
    if m and len(set(m.group(2))) >= 6 and not m.group(2).startswith(("process.env", "os.environ", "$")):
        return True
    m = DB_URL_PASSWORD.search(line)
    return bool(m and not re.search(r"(?i)^(pass(word)?|postgres|root|admin|user)$", m.group(2)))


def norm_path(p: str) -> str:
    p = p.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def has_glob(p: str) -> bool:
    return bool(re.search(r"[*?\[]", p))


def static_prefix(p: str) -> str:
    m = re.search(r"[*?\[]", p)
    if not m:
        return p
    pre = p[: m.start()]
    return pre[: pre.rfind("/") + 1] if "/" in pre else ""


def glob_regex(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append({"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c)))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def paths_overlap(a: str, b: str) -> bool:
    """Conservative: True when two write surfaces could touch the same file."""
    a, b = norm_path(a), norm_path(b)
    if not a or not b:
        return False
    if a == b:
        return True
    ga, gb = has_glob(a), has_glob(b)

    def contains(d: str, p: str) -> bool:
        d = d.rstrip("/")
        return p.startswith(d + "/")

    if not ga and not gb:
        return contains(a, b) or contains(b, a)
    if ga and gb:
        pa, pb_ = static_prefix(a), static_prefix(b)
        return pa.startswith(pb_) or pb_.startswith(pa)
    pat, concrete = (a, b) if ga else (b, a)
    if glob_regex(pat).match(concrete):
        return True
    pre = static_prefix(pat)
    return contains(concrete, pre) or (pre != "" and concrete.rstrip("/") + "/" == pre)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        die(f"{path} is not valid JSON: {e}", 1)


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pb_dir(root: Path) -> Path:
    return root / ".prod-build"


def mem_dir(root: Path) -> Path:
    return root / "docs" / "memory"


# ----------------------------------------------------------------------------- init

CONTRACT_TMPL = """# Implementation contract - {name}
mode: {mode} | channel: {channel} | created: {date}
Upstream read: <idea package / UI-SPEC.md / DESIGN.md / product brief, or "none">

## 1. Intent
- User and moment of use:
- What they can do:
- What changes after they use it (before -> after):

## 2. Scope
MUST (core product, main workflow, judged/required):
SHOULD (materially improves the main workflow):
COULD (later; not in this build):
WON'T now (hypothetical-future infrastructure, explicitly deferred):

## 3. Core journeys (smallest set that constitutes the product)
J1: user action -> system response -> observable outcome

## 4. Surfaces (only the necessary ones)

## 5. States per journey
| Journey | initial | loading | success | error | empty | partial | retry | disabled | permission-denied | offline/degraded |

## 6. Data
| Entity | Key fields | Relationships | Owner | Persistence | Source of truth | PII/retention |

## 7. Interfaces
| Interface | Purpose | Contract file | Failure mode | Fallback |

## 8. Constraints
Security / privacy / budget / latency / platform / deploy target / deadline / sponsor rules (verbatim) / judging criteria (verbatim + weights):

## 9. Acceptance criteria
AC-1: WHEN <event> THE SYSTEM SHALL <result>. Verify: <command or check>. Priority: must

## 10. Assumptions
A-1 [safe|reversible|blocking]: <claim> - validate by <method>

## 11. Demo script (hackathon/hybrid) or launch checklist (product)
"""

CONTRACT_EVENT_TMPL = """
## 12. Event and judged path
(This file is committed with the repository, so a judge may read it. Quote the event's own public pages, with
the date each was read. Nothing about individual judges and no guesses about what they like.)
- Window: start / freeze / end as instants (UTC and local), the page they came from, and the rule on earlier work, verbatim:
- How an entry is judged: what a judge opens first, second and third (queue card -> video -> live link ->
  repository), minutes per project, and whether anyone runs the build; each as the organisers state it, or "not published":
- Criteria: the organisers' wording and weights, and where each was read (the judges' own form when it is public, else the event page):
- Criteria coverage: per criterion, the moment in the run that earns it and its state (met / partial / open):
- Capability coverage: each capability the prize text names -> where it happens in the planned run, on the
  sponsor's own surface when the sponsor asked to see it:
- Main result: the outcome a camera can see (physical / native state / number / artifact), on material we did
  not author, and the one staged failure the product holds through:
- Promise coverage: each noun in the title and tagline -> the data or feature behind it on the first screen:
- Before the clock: what was prepared before the start (planning, account checks) and the README sentence that says so:
"""

STATE_TMPL = """# Build state - {name}
Updated: {date} | mode: {mode} | channel: {channel} | phase: 0-intake
(Keep this under 60 lines. Overwrite it at every phase boundary and before stopping.)

## Now
- Phase / current task:
- Last good commit:            Last good deploy:

## What worked (with evidence IDs)

## What failed (memory IDs) - do not retry without a new root cause

## Not tried yet / open questions

## Changes that need care (migrations applied, deploys, destructive ops)

## Exact next step
"""

MEM_README = """# Project memory

Decisions, failures, lessons, assumptions, user feedback and working patterns that should change
what the next agent does. Records are hints until verified against the code they cite.
Generated index: INDEX.md. Tooling: `python3 .prod-build/pb.py mem find|add|check|mark`.
Never store secrets, transcripts, logs, or anything the code already says.
"""

AGENTS_BLOCK = """<!-- prod-build:memory-protocol -->
## Build memory protocol
- Before planning or changing a component: read `docs/memory/INDEX.md`; run
  `python3 .prod-build/pb.py mem find --paths <files> --terms <keywords>` and apply what fits.
- On any error: search failures by the error text first (`mem find --terms "<signature>"`); do not retry
  an approach a failure record says failed unless you have a new root cause.
- A record is a hint until its cited files still match (`mem check` flags stale ones).
- Run checks through `python3 .prod-build/pb.py run --task <ID> -- <command>` so results land in the
  evidence ledger; "done" means passing evidence exists, not that code was written.
- Task briefs: `.prod-build/briefs/<ID>.md`. Reports: `.prod-build/reports/<ID>.md`.
<!-- /prod-build:memory-protocol -->
"""


def cmd_init(args, root: Path) -> int:
    d = pb_dir(root)
    created = []
    event_old = load_event(root, repair=event_flags_given(args))
    event = build_event(args, event_old) if event_flags_given(args) else event_old  # validated before anything is created
    if args.mode_given and args.mode == "hackathon" and not has_clock(event):
        die("hackathon mode needs the event clock before anything is built:\n"
            "  pb.py init --mode hackathon --start <instant> --end <instant> --source <event page>\n"
            "or, where the event has no build window, --no-window '<the rule, quoted>'.\n"
            "Instants carry a zone (2026-03-14T09:00:00Z or 2026-03-14T14:30:00+05:30) and come from the event's "
            "own page, not from memory.")
    for sub in ("briefs", "reports", "logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    if event and event != event_old:
        write_json(event_path(root), event)
        created.append(event_path(root))
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("logs/\nevidence.*.jsonl\n", encoding="utf-8")
        created.append(gi)
    name = args.name or root.name
    plan = d / "plan.json"
    if not plan.exists():
        write_json(plan, {"project": name, "mode": args.mode, "channel": args.channel,
                          "base": git_info(root)["sha"], "created": today(), "milestones": [], "tasks": []})
        created.append(plan)
    for fname, tmpl in (("contract.md", CONTRACT_TMPL), ("state.md", STATE_TMPL)):
        f = d / fname
        if not f.exists():
            body = tmpl.format(name=name, mode=args.mode, channel=args.channel, date=today())
            if fname == "contract.md" and (args.mode != "product" or event):
                body += CONTRACT_EVENT_TMPL
            f.write_text(body, encoding="utf-8")
            created.append(f)
    ev = d / "evidence.jsonl"
    if not ev.exists():
        ev.touch()
        created.append(ev)
    me = Path(__file__).resolve()
    target = d / "pb.py"
    if me != target.resolve() and (not target.exists() or target.read_bytes() != me.read_bytes()):
        shutil.copyfile(me, target)
        created.append(target)
    m = mem_dir(root)
    for _, (_, folder) in MEM_TYPES.items():
        (m / folder).mkdir(parents=True, exist_ok=True)
    (m / "archive").mkdir(parents=True, exist_ok=True)
    if not (m / "README.md").exists():
        (m / "README.md").write_text(MEM_README, encoding="utf-8")
        created.append(m / "README.md")
    write_index(root)
    agents = root / "AGENTS.md"
    text = agents.read_text(encoding="utf-8") if agents.exists() else ""
    if "prod-build:memory-protocol" not in text:
        sep = "\n" if text and not text.endswith("\n") else ""
        header = "" if text else f"# {name}\n\n"
        agents.write_text(text + sep + header + AGENTS_BLOCK, encoding="utf-8")
        created.append(agents)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("\n".join(GITIGNORE_BASE) + "\n", encoding="utf-8")
        created.append(gitignore)
    else:
        have = gitignore.read_text(encoding="utf-8", errors="replace")
        lacking = [w for w in ("node_modules", ".env") if w not in have]
        if lacking:
            print("note: .gitignore lacks " + ", ".join(lacking) + "; add them before the first `git add`")
    if event:
        for line in ensure_ignores(root):
            print(line)
    if args.checklists:
        refs = Path(__file__).resolve().parent.parent / "references"
        dest = d / "checklists"
        if refs.is_dir():
            dest.mkdir(exist_ok=True)
            for name in ("security.md", "verification.md", "deploy.md", "ai-products.md"):
                if (refs / name).exists() and not (dest / name).exists():
                    shutil.copyfile(refs / name, dest / name)
                    created.append(dest / name)
        else:
            print("note: --checklists needs the skill's references/ folder next to scripts/; run init from the skill copy")
    claude = root / "CLAUDE.md"
    note = ""
    if not claude.exists():
        claude.write_text("@AGENTS.md\n", encoding="utf-8")
        created.append(claude)
    elif "@AGENTS.md" not in claude.read_text(encoding="utf-8"):
        note = "CLAUDE.md exists without '@AGENTS.md': add that line so Claude Code also reads AGENTS.md."
    if args.checklists:
        asset = Path(__file__).resolve().parent.parent / "assets" / "smoke.spec.ts"
        dest = d / "checklists" / "smoke.spec.ts"
        if asset.exists() and (d / "checklists").is_dir() and not dest.exists():
            shutil.copyfile(asset, dest)
            created.append(dest)
    for c in created:
        print(f"created {c.relative_to(root)}")
    if not args.mode_given:
        print(f"note: mode defaulted to '{args.mode}'; after classifying (Step 1) set \"mode\" in .prod-build/plan.json")
    if not created:
        print("nothing to create; already initialised")
    if note:
        print("note: " + note)
    code = 0
    if event:
        for line in clock_lines(event):
            print(line)
        if event.get("start") and event.get("history", "strict") == "strict":
            res = window_findings(root, parse_instant(event["start"]), parse_instant(event["end"]),
                                  event.get("tz_min"))
            for f in res["fails"]:
                print(f"FAIL  {f}")
            if res["fails"]:
                print("      " + NOT_FIXABLE)
                code = 1
    elif args.mode in ("hackathon", "hybrid"):
        print("note: no event clock. If this is for an event with a build window, run `pb.py clock set --start .. --end ..` "
              "before any code or commit.")
    if has_clock(event) and event.get("start") and event_phase(event)["phase"] == "before":
        print("note: the window has not opened. These are planning files; leave them uncommitted until it does.")
    print("next: fill .prod-build/contract.md, then write tasks into .prod-build/plan.json and run `pb.py plan check`.")
    return code


# ----------------------------------------------------------------------------- plan

def load_plan(root: Path) -> dict:
    plan = read_json(pb_dir(root) / "plan.json")
    if plan is None:
        die("no .prod-build/plan.json (run `pb.py init`)", 1)
    return plan


def save_plan(root: Path, plan: dict) -> None:
    write_json(pb_dir(root) / "plan.json", plan)


def task_map(plan: dict) -> dict:
    return {t.get("id"): t for t in plan.get("tasks", []) if isinstance(t, dict)}


def find_cycle(tasks: dict):
    color, stack = {}, []

    def visit(n):
        color[n] = 1
        stack.append(n)
        for d in tasks[n].get("deps", []) or []:
            if d not in tasks:
                continue
            if color.get(d) == 1:
                return stack[stack.index(d):] + [d]
            if color.get(d) is None:
                r = visit(d)
                if r:
                    return r
        stack.pop()
        color[n] = 2
        return None

    for n in tasks:
        if color.get(n) is None:
            r = visit(n)
            if r:
                return r
    return None


def layers(tasks: dict) -> list:
    active = {k: v for k, v in tasks.items() if v.get("status") != "skip"}
    indeg = {k: 0 for k in active}
    for k, v in active.items():
        for d in v.get("deps", []) or []:
            if d in active:
                indeg[k] += 1
    out, ready = [], sorted(k for k, n in indeg.items() if n == 0)
    seen = set()
    while ready:
        out.append(ready)
        seen.update(ready)
        nxt = []
        for k, v in active.items():
            if k in seen or k in nxt:
                continue
            if all(d in seen or d not in active for d in (v.get("deps", []) or [])):
                nxt.append(k)
        ready = sorted(nxt)
    return out


def conflict_reasons(a: dict, b: dict) -> list:
    reasons = []
    wa, wb = a.get("writes", []) or [], b.get("writes", []) or []
    for x in wa:
        for y in wb:
            if paths_overlap(x, y):
                reasons.append(f"both write {x if len(x) <= len(y) else y}")
                break
        if reasons:
            break
    hot_a = [x for x in wa if HOT_FILES.search(norm_path(x))]
    hot_b = [y for y in wb if HOT_FILES.search(norm_path(y))]
    if hot_a and hot_b and not reasons:
        reasons.append(f"both touch shared config/lockfile/migrations ({hot_a[0]}, {hot_b[0]})")
    ra, rb = a.get("runtime") or {}, b.get("runtime") or {}
    ports = set(ra.get("ports", []) or []) & set(rb.get("ports", []) or [])
    if ports:
        reasons.append(f"same port {sorted(ports)}")
    if ra.get("db") and ra.get("db") == rb.get("db") and ra.get("db") not in ("none", "in-memory"):
        reasons.append(f"same database '{ra.get('db')}'")
    if "migration" in (a.get("risk") or []) and "migration" in (b.get("risk") or []):
        reasons.append("both change the schema")
    return reasons


def lanes(ids: list, tasks: dict) -> list:
    """Greedy grouping of ids into lanes that may run in parallel with each other."""
    groups = []
    for i in ids:
        placed = False
        for g in groups:
            if not any(conflict_reasons(tasks[i], tasks[j]) for j in g):
                g.append(i)
                placed = True
                break
        if not placed:
            groups.append([i])
    return groups


def check_plan(plan: dict) -> tuple:
    errors, warns = [], []
    mode = plan.get("mode")
    if mode not in MODES:
        errors.append(f"mode must be one of {MODES}, got {mode!r}")
    tasks_list = plan.get("tasks")
    if not isinstance(tasks_list, list):
        return [*errors, "tasks must be a list"], warns
    seen = set()
    for t in tasks_list:
        if not isinstance(t, dict):
            errors.append("every task must be an object")
            continue
        tid = t.get("id", "?")
        if not re.match(r"^T\d{2,3}[a-z]?$", str(tid)):
            errors.append(f"{tid}: id must look like T01, T12 or T07a")
        if tid in seen:
            errors.append(f"{tid}: duplicate id")
        seen.add(tid)
        for field in ("title", "objective"):
            if not str(t.get(field, "")).strip():
                errors.append(f"{tid}: missing {field}")
        if t.get("status", "todo") not in STATUSES:
            errors.append(f"{tid}: status must be one of {STATUSES}")
        if not t.get("verify"):
            errors.append(f"{tid}: no verify commands (how will anyone know it works?)")
        if not t.get("acceptance"):
            errors.append(f"{tid}: no acceptance criteria")
        if not t.get("writes"):
            if not re.search(r"(?i)review|verify|audit|deploy|smoke|qa|gate|record|submit",
                             f"{t.get('role', '')} {t.get('kind', '')}"):
                warns.append(f"{tid}: empty write surface (set \"kind\": \"review\"|\"deploy\"|\"qa\"|\"record\"|"
                             "\"submit\" if it writes no files)")
        elif len(t.get("writes")) > 15:
            warns.append(f"{tid}: writes {len(t['writes'])} paths; split it (one intent, <=15 files)")
        if not str(t.get("rollback", "")).strip():
            (errors if mode == "product" and "migration" in (t.get("risk") or []) else warns).append(
                f"{tid}: no rollback note")
        if t.get("size") and t.get("size") not in ("S", "M", "L"):
            warns.append(f"{tid}: size should be S, M or L")
        if t.get("size") == "L" and mode == "product":
            warns.append(f"{tid}: size L in product mode; consider splitting")
        if t.get("status") == "skip" and not str(t.get("notes", "")).strip():
            errors.append(f"{tid}: skipped without a reason in notes")
    tasks = task_map(plan)
    if tasks and not suite_items(plan):
        warns.append("no top-level \"suite\" (the full test command): `pb.py verify` can't run the whole suite per task")
    for tid, t in tasks.items():
        for d in t.get("deps", []) or []:
            if d not in tasks:
                errors.append(f"{tid}: depends on unknown task {d}")
    cyc = find_cycle(tasks)
    if cyc:
        errors.append("dependency cycle: " + " -> ".join(cyc))
    active = [t for t in tasks_list if isinstance(t, dict) and t.get("status") != "skip"]
    if len(active) > 12 and not plan.get("milestones"):
        warns.append(f"{len(active)} tasks and no milestones; group into milestones of 3-12 tasks")
    if not cyc and plan.get("parallel", True) is not False:
        for layer in layers(tasks):
            for i, a in enumerate(layer):
                for b in layer[i + 1:]:
                    r = conflict_reasons(tasks[a], tasks[b])
                    if r:
                        warns.append(f"{a} and {b} are unordered but conflict ({'; '.join(r)}): "
                                     "they will run one after the other unless isolated")
    return errors, warns


def check_briefs(root: Path, plan: dict) -> tuple:
    """Each brief must be the brief for ITS task: id + title, the verify path, the report block, a BASE line."""
    errors, warns = [], []
    bdir = pb_dir(root) / "briefs"
    if not bdir.is_dir():
        return errors, warns
    tasks = task_map(plan)
    bodies = {}
    for f in sorted(bdir.glob("*.md")):
        m = re.match(r"^(T\d{2,3}[a-z]?)\b", f.stem)
        if not m or m.group(1) not in tasks:
            continue
        tid, t = m.group(1), tasks[m.group(1)]
        text = f.read_text(encoding="utf-8")
        head = "\n".join(text.splitlines()[:3])
        if tid not in head:
            errors.append(f"briefs/{f.name}: heading doesn't name {tid}")
        if t.get("title") and words(t["title"]) and len(words(t["title"]) & words(text)) < min(2, len(words(t["title"]))):
            warns.append(f"briefs/{f.name}: doesn't mention its task title '{t['title']}'")
        runs_verify = re.search(rf"pb\.py\s+verify\s+{tid}\b", text)
        missing = [v for v in (t.get("verify") or []) if not v.lower().startswith(("observe:", "manual:"))
                   and norm_cmd(v) not in norm_cmd(text)]
        if not runs_verify and missing:
            errors.append(f"briefs/{f.name}: neither `pb.py verify {tid}` nor its verify command(s): " + "; ".join(missing[:3]))
        if "STATUS:" not in text:
            errors.append(f"briefs/{f.name}: no report block (STATUS: …)")
        if not re.search(r"(?i)\bBASE\b", text):
            warns.append(f"briefs/{f.name}: no BASE commit line")
        other = [o for o in re.findall(r"\bT\d{2,3}[a-z]?\b", text) if o != tid and o in tasks]
        if other.count(max(set(other), key=other.count) if other else "") > text.count(tid):
            warns.append(f"briefs/{f.name}: mentions {max(set(other), key=other.count)} more often than {tid}: copied brief?")
        body = re.sub(r"\bT\d{2,3}[a-z]?\b", "Txx", text)
        if body in bodies:
            errors.append(f"briefs/{f.name}: same content as briefs/{bodies[body]}")
        bodies[body] = f.name
    return errors, warns


def cmd_plan(args, root: Path) -> int:
    plan = load_plan(root)
    tasks = task_map(plan)
    if args.action == "check":
        errors, warns = check_plan(plan)
        be, bw = check_briefs(root, plan)
        errors, warns = errors + be, warns + bw
        ev = load_event(root)
        if plan.get("mode") == "hackathon" and not has_clock(ev):
            warns.append("hackathon plan with no event clock: `pb.py clock set --start .. --end .. --source ..` "
                         "(no task will start without it)")
        if ev and ev.get("provisional"):
            warns.append(f"the event clock is provisional ({ev['provisional']}): re-read the instants from the "
                         "event page and confirm them with `pb.py clock set --start .. --end .. --confirmed`")
        if ev and ev.get("start") and tasks:
            kinds = {str(t.get("kind") or "").lower() for t in tasks.values() if t.get("status") != "skip"}
            if "record" not in kinds:
                warns.append("no task of kind \"record\": schedule two, a backup take for the moment the end-to-end "
                             "path first works and the final take once the build is frozen")
            if "submit" not in kinds:
                warns.append("no task of kind \"submit\": the page, the form answers and `pb.py submit check`")
        for w in warns:
            print(f"WARN  {w}")
        for e in errors:
            print(f"ERROR {e}")
        print(f"plan check: {len(errors)} error(s), {len(warns)} warning(s), {len(tasks)} task(s)")
        return 1 if errors or (args.strict and warns) else 0
    if find_cycle(tasks):
        die("plan has a dependency cycle; run `pb.py plan check`", 1)
    serial = plan.get("parallel", True) is False
    if args.action == "waves":
        for n, layer in enumerate(layers(tasks), 1):
            groups = [[i] for i in layer] if serial else lanes(layer, tasks)
            desc = "  then  ".join(" || ".join(g) for g in groups)
            print(f"wave {n}: {desc}")
        path = critical_path(tasks)
        if path:
            print("critical path: " + " -> ".join(path))
        return 0
    if args.action == "next":
        ready = [k for k, t in tasks.items() if t.get("status", "todo") == "todo"
                 and all(tasks.get(d, {}).get("status") in ("done", "skip") for d in (t.get("deps") or []))]
        doing = [k for k, t in tasks.items() if t.get("status") == "doing"]
        if doing:
            print("in progress (re-check exit criteria before trusting): " + ", ".join(sorted(doing)))
        if not ready:
            print("no ready tasks" + ("" if doing else " (all done, blocked, or waiting on deps)"))
            return 0
        groups = [[i] for i in sorted(ready)] if serial else lanes(sorted(ready), tasks)
        print("ready now. Tasks on one line may run in parallel; run the lines one after another:")
        for g in groups:
            print("  " + " || ".join(f"{i} {tasks[i].get('title', '')}" for i in g))
        return 0
    return 2


def critical_path(tasks: dict) -> list:
    weight = {"S": 1, "M": 2, "L": 3}
    order = [i for layer in layers(tasks) for i in layer]
    best, prev = {}, {}
    for i in order:
        w = weight.get(tasks[i].get("size"), 1)
        cands = [(best[d], d) for d in (tasks[i].get("deps") or []) if d in best]
        if cands:
            b, d = max(cands)
            best[i], prev[i] = b + w, d
        else:
            best[i] = w
    if not best:
        return []
    end = max(best, key=lambda k: best[k])
    path = [end]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    return list(reversed(path))


# ----------------------------------------------------------------------------- evidence ledger

def ledger_path(root: Path) -> Path:
    """The canonical ledger, or a local one (PB_LEDGER) for parallel lanes and relay clones."""
    alt = os.environ.get("PB_LEDGER")
    if alt:
        p = Path(alt)
        return p if p.is_absolute() else root / p
    return pb_dir(root) / "evidence.jsonl"


def read_ledger(root: Path) -> list:
    p = ledger_path(root)
    if not p.exists():
        return []
    out = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"id": f"?line{n}", "_corrupt": True})
    return out


def record_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def verify_chain(entries: list) -> list:
    problems, prev = [], ""
    for e in entries:
        if e.get("_corrupt"):
            problems.append(f"{e['id']}: unparseable ledger line")
            continue
        if e.get("prev", "") != prev:
            problems.append(f"{e.get('id')}: chain broken (entry edited, removed or hand-written)")
        if record_hash(e) != e.get("hash"):
            problems.append(f"{e.get('id')}: hash mismatch (entry edited by hand)")
        prev = e.get("hash", "")
    return problems


def append_evidence(root: Path, rec: dict) -> dict:
    pb_dir(root).mkdir(parents=True, exist_ok=True)
    entries = read_ledger(root)
    nums = [int(e["id"][1:]) for e in entries if re.match(r"^E\d+$", str(e.get("id", "")))]
    rec["id"] = f"E{(max(nums) + 1 if nums else 1):04d}"
    rec["ts"] = now_iso()
    rec["agent"] = rec.get("agent") or os.environ.get("PB_AGENT", "unknown")
    rec["prev"] = entries[-1].get("hash", "") if entries else ""
    rec["hash"] = record_hash(rec)
    with ledger_path(root).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def shell_command(parts: list) -> list:
    cmd = parts[0] if len(parts) == 1 else (
        subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts))
    if os.name != "nt" and shutil.which("bash"):
        return ["bash", "-o", "pipefail", "-c", cmd], cmd
    return cmd, cmd


def cmd_run(args, root: Path) -> int:
    parts = list(args.command or [])
    if parts and parts[0] == "--":
        parts = parts[1:]
    if not parts:
        die("usage: pb.py run [--task ID] [--kind K] -- <command>")
    runnable, display = shell_command(parts)
    if os.name == "nt" and "|" in display:
        print("pb: warning: cmd.exe has no pipefail; a pipe can hide the real exit code", file=sys.stderr)
    logs = pb_dir(root) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    start = time.time()
    try:
        proc = subprocess.run(runnable, cwd=root, shell=isinstance(runnable, str), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=args.timeout)
        code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        code = 124
        output = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) \
            + f"\n[pb] timed out after {args.timeout}s"
    dur = round(time.time() - start, 2)
    clean = redact(output)
    tail = clean.splitlines()[-args.tail:] if args.tail > 0 else []
    expect = "fail" if args.expect_fail else "pass"
    ok = (code != 0) if args.expect_fail else (code == 0)
    kind = args.kind or ("red" if args.expect_fail else "other")
    rec = append_evidence(root, {
        "task": args.task, "kind": kind, "method": "ran", "cmd": redact(display),
        "cwd": ".", "exit": code, "expect": expect, "ok": ok, "duration_s": dur,
        "git": git_info(root), "tree": tree_hash(root), "where": args.where, "url": args.url, "tail": tail[-20:],
    })
    log = logs / f"{rec['id']}.log"
    log.write_text(f"$ {redact(display)}\n# exit {code} in {dur}s at {rec['ts']}\n\n{clean}", encoding="utf-8")
    for line in tail:
        print(line)
    verdict = "PASS" if ok else "FAIL"
    extra = " (expected failure)" if args.expect_fail else ""
    print(f"[{rec['id']}] {verdict}{extra} exit={code} {dur}s  {rec['cmd']}  -> .prod-build/logs/{log.name}")
    if args.expect_fail:
        return 0 if ok else 1  # RED recorded as expected -> success; an unexpected pass is the failure
    return code


def cmd_evidence(args, root: Path) -> int:
    if args.action == "redact":
        if not args.id or not args.pattern or not args.reason:
            die("usage: pb.py evidence redact --id E0012 --pattern '<regex>' --reason '<why>'")
        entries = read_ledger(root)
        broken = verify_chain(entries)
        if broken:
            die("ledger already fails its chain check (" + broken[0] + "); investigate before redacting", 1)
        rx = re.compile(args.pattern)
        target = next((e for e in entries if e.get("id") == args.id), None)
        if not target:
            die(f"unknown evidence id {args.id}", 1)
        old_hash = target.get("hash", "")
        before = json.dumps(target, sort_keys=True)
        for k in ("cmd", "summary", "url"):
            if isinstance(target.get(k), str):
                target[k] = rx.sub("[REDACTED]", target[k])
        if isinstance(target.get("tail"), list):
            target["tail"] = [rx.sub("[REDACTED]", x) for x in target["tail"]]
        if json.dumps(target, sort_keys=True) == before:
            die(f"pattern not found in {args.id}", 1)
        idx = entries.index(target)
        prev = entries[idx - 1]["hash"] if idx > 0 else ""
        for e in entries[idx:]:
            e["prev"] = prev
            e["hash"] = record_hash(e)
            prev = e["hash"]
        ledger_path(root).write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
        log = pb_dir(root) / "logs" / f"{args.id}.log"
        if log.exists():
            log.write_text(rx.sub("[REDACTED]", log.read_text(encoding="utf-8")), encoding="utf-8")
        rec = append_evidence(root, {"task": target.get("task"), "kind": "manual", "method": "observed",
                                     "cmd": None, "summary": f"redacted {args.id} (old hash {old_hash[:16]}): {args.reason}",
                                     "exit": None,
                                     "expect": "pass", "ok": True, "git": git_info(root), "where": "local"})
        print(f"redacted {args.id} and re-chained the ledger; audit entry {rec['id']}. Rotate the secret: "
              "history already committed elsewhere still has it.")
        return 0
    if args.action == "add":
        if not args.summary:
            die("evidence add needs --summary")
        rec = append_evidence(root, {
            "task": args.task, "kind": args.kind or "manual", "method": args.method, "cmd": None,
            "summary": redact(args.summary), "exit": None, "expect": "pass", "ok": not args.failed,
            "git": git_info(root), "tree": tree_hash(root), "where": args.where, "url": args.url, "files": args.file or [],
        })
        print(f"[{rec['id']}] recorded {args.method} evidence: {rec['summary']}")
        return 0
    entries = read_ledger(root)
    if args.task:
        entries = [e for e in entries if e.get("task") == args.task]
    if args.failed:
        entries = [e for e in entries if not e.get("ok")]
    for e in entries[-args.limit:]:
        what = e.get("cmd") or e.get("summary") or ""
        print(f"{e.get('id')} {('ok ' if e.get('ok') else 'FAIL'):4} {e.get('method', ''):8} "
              f"{str(e.get('task') or '-'):5} {e.get('kind', ''):9} {e.get('where', '')}  {what[:90]}")
    problems = verify_chain(read_ledger(root))
    for p in problems:
        print(f"LEDGER {p}")
    return 1 if problems else 0


# ----------------------------------------------------------------------------- smoke

class _Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("src"):
            self.urls.append(a["src"])
        elif tag == "link" and a.get("href") and any(
                r in (a.get("rel") or "").lower() for r in ("stylesheet", "icon", "preload", "modulepreload", "manifest")):
            self.urls.append(a["href"])
        elif tag == "img" and a.get("src") and not a["src"].startswith("data:"):
            self.urls.append(a["src"])


ERROR_MARKERS = ("Application error: a client-side exception", "Internal Server Error",
                 "This page could not be found", "An unexpected error has occurred",
                 "Traceback (most recent call last)", "FUNCTION_INVOCATION_FAILED", "DEPLOYMENT_NOT_FOUND")


def fetch(url: str, timeout: float, method: str = "GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "prod-build-smoke/1.0"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(2_000_000) if method == "GET" else b""
            return r.status, body, round(time.time() - t, 2), r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read(200_000) if method == "GET" else b"", round(time.time() - t, 2), ""
    except Exception as e:  # noqa: BLE001 - report any network failure as a finding
        return None, str(e).encode(), round(time.time() - t, 2), ""


def cmd_smoke(args, root: Path) -> int:
    base = args.url.rstrip("/")
    findings, lines = [], []
    for route in args.routes:
        url = base + (route if route.startswith("/") else "/" + route)
        status, body, secs, ctype = fetch(url, args.timeout)
        lines.append(f"{status} {secs:>5}s {url}")
        if status is None or status >= 400:
            findings.append(f"{url} -> {status or body.decode('utf-8', 'replace')[:120]}")
            continue
        if secs > args.slow:
            findings.append(f"{url} slow: {secs}s (cold start? warm it before a demo)")
        text = body.decode("utf-8", "replace")
        for marker in ERROR_MARKERS:
            if marker in text:
                findings.append(f"{url} page contains '{marker}'")
        if "html" in ctype or text.lstrip().lower().startswith("<!doctype html"):
            p = _Assets()
            try:
                p.feed(text)
            except Exception:  # noqa: BLE001
                pass
            for ref in dict.fromkeys(p.urls):
                aurl = urllib.parse.urljoin(url, ref)
                if urllib.parse.urlparse(aurl).netloc != urllib.parse.urlparse(base).netloc and not args.external:
                    continue
                st, _, _, _ = fetch(aurl, args.timeout, "HEAD")
                if st in (405, 501):
                    st, _, _, _ = fetch(aurl, args.timeout)
                if st is None or st >= 400:
                    findings.append(f"asset {aurl} -> {st}")
    for l in lines:
        print(l)
    for f in findings:
        print(f"FINDING {f}")
    ok = not findings
    rec = append_evidence(root, {
        "task": args.task, "kind": "smoke", "method": "ran",
        "cmd": f"pb.py smoke {args.url} --routes " + " ".join(args.routes),
        "exit": 0 if ok else 1, "expect": "pass", "ok": ok, "git": git_info(root), "tree": tree_hash(root), "where": args.where,
        "url": base, "tail": (lines + [f"FINDING {f}" for f in findings])[-20:],
    })
    print(f"[{rec['id']}] {'PASS' if ok else 'FAIL'} smoke {base} ({len(args.routes)} route(s), {len(findings)} finding(s)). "
          "Console errors need a browser check; record it with `pb.py evidence add --method observed`.")
    return 0 if ok else 1


# ----------------------------------------------------------------------------- tasks

def norm_cmd(cmd: str) -> str:
    """Normalise a command for matching: squash spaces and unify the pb.py spelling. VAR=val prefixes are
    significant (a run that only passes with an extra variable is a different check), so they are kept."""
    c = re.sub(r"\s+", " ", redact(cmd or "")).strip()
    c = re.sub(r"(^|&&\s*|;\s*)(?:python3?|py)\s+(?:\./)?(?:\.prod-build/|\S*/)?pb\.py\b", r"\1pb.py", c)
    return c.strip("'\" ")


def pass_evidence(e: dict) -> bool:
    """Evidence that shows something WORKS: passed, expected to pass, and ran or was observed."""
    return bool(e.get("ok")) and e.get("expect", "pass") == "pass" and e.get("method") in ("ran", "observed")


def suite_items(plan: dict) -> list:
    s = plan.get("suite") or []
    return [s] if isinstance(s, str) else list(s)


def verify_items(t: dict, plan: dict) -> list:
    items = list(t.get("verify") or [])
    if t.get("suite", True) is not False:
        items += [x for x in suite_items(plan) if norm_cmd(x) not in {norm_cmd(i) for i in items}]
    return items


def evidence_num(e: dict) -> int:
    m = re.match(r"^E(\d+)$", str(e.get("id", "")))
    return int(m.group(1)) if m else 0


def words(text: str) -> set:
    stop = {"with", "that", "this", "from", "into", "have", "been", "were", "they", "then", "than", "when", "what"}
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 4 and w not in stop}


def match_observations(items: list, observed: list) -> dict:
    """Assign each observe: item its own observed entry (best word overlap, each entry used once)."""
    out, used = {}, set()
    for item in items:
        want = words(item.split(":", 1)[1] if ":" in item else item)
        best, best_score = None, -1
        for e in reversed(observed):
            if e["id"] in used:
                continue
            score = len(want & words(e.get("summary", "")))
            if score > best_score:
                best, best_score = e, score
        need = 1 if len(want) <= 2 else 2
        if best is not None and (best_score >= need or not want):
            out[item] = best
            used.add(best["id"])
    return out


def verify_status(t: dict, entries: list, plan: dict = None) -> tuple:
    """For each verify item: (item, state, latest_entry) with state pass | fail | missing, from the LATEST run
    recorded since the task was (re)started."""
    out = []
    since = int(t.get("started_after") or 0)
    mine = [e for e in entries if e.get("task") == t.get("id") and e.get("expect", "pass") == "pass"
            and evidence_num(e) > since]
    items = verify_items(t, plan or {})
    observe = [v for v in items if v.strip().lower().startswith(("observe:", "manual:"))]
    matched = match_observations(observe, [e for e in mine if e.get("method") == "observed"])
    for v in items:
        if v in observe:
            e = matched.get(v)
            out.append((v, "missing" if not e else ("pass" if e.get("ok") else "fail"), e))
            continue
        runs = [e for e in mine if e.get("cmd") and norm_cmd(e["cmd"]) == norm_cmd(v)]
        out.append((v, "missing" if not runs else ("pass" if runs[-1].get("ok") else "fail"), runs[-1] if runs else None))
    return out


def cmd_task(args, root: Path) -> int:
    plan = load_plan(root)
    tasks = task_map(plan)
    t = tasks.get(args.id)
    if not t:
        die(f"unknown task {args.id}", 1)
    note = (args.note or "").strip()
    stamp = f"{today()} {args.action}" + (" [test clock]" if fake_now() else "")
    if args.action in ("skip", "block", "reopen") and not note:
        die(f"`task {args.id} {args.action}` needs --note with the reason")
    if args.action == "start":
        blockers = [d for d in (t.get("deps") or []) if tasks.get(d, {}).get("status") not in ("done", "skip")]
        if blockers and not args.force:
            die(f"{args.id} depends on unfinished {', '.join(blockers)} (use --force to override)", 1)
        if load_event(root):
            for line in ensure_ignores(root):       # a scaffold may have replaced .gitignore since the clock was set
                print(line)
        gate, override = event_gate_for_start(root, plan, t, args.force, note)
        if gate:
            print(gate)
        if override:
            note = override + note
            append_evidence(root, {"task": args.id, "kind": "manual", "method": "reported", "summary": note[:300],
                                   "ok": True, "where": "local", "git": git_info(root), "tree": tree_hash(root)})
        gi = git_info(root)
        if gi["dirty"]:
            print("WARN  uncommitted changes outside .prod-build/: commit the plan/briefs first so BASE is meaningful")
        t["status"] = "doing"
        t["base"] = gi["sha"]
        if not plan.get("base"):
            plan["base"] = gi["sha"]
        t["started_after"] = max([evidence_num(e) for e in read_ledger(root)] or [0])
        print(f"{args.id} BASE {gi['sha']} (put this in the brief; intake diffs from here). "
              f"Only evidence after E{t['started_after']:04d} counts toward done.")
    elif args.action == "done":
        entries = read_ledger(root)
        ledger = {e.get("id"): e for e in entries}
        ids = [x.strip() for x in (args.evidence or "").split(",") if x.strip()]
        missing = [i for i in ids if i not in ledger]
        if missing:
            die(f"unknown evidence id(s): {', '.join(missing)}", 1)
        for_task = [e for e in entries if e.get("task") == args.id]
        pool = [ledger[i] for i in ids] or for_task
        passing = [e for e in pool if pass_evidence(e)]
        states = verify_status(t, entries, plan)
        failing = [v for v, st, _ in states if st == "fail"]
        unmatched = [v for v, st, _ in states if st == "missing"]
        now_tree = tree_hash(root)
        stale = [f"{v} ({e['id']})" for v, st, e in states
                 if st == "pass" and e and e.get("tree") and now_tree and e["tree"] != now_tree
                 and not v.strip().lower().startswith(("observe:", "manual:"))]
        problems = []
        if stale:
            problems.append("code changed since the passing run of: " + "; ".join(stale) + f" (re-run `pb.py verify {args.id}`)")
        if not passing and not [e for _, st, e in states if st == "pass"]:
            problems.append("no passing evidence (RED/expected-failure runs don't count)")
        if failing:
            problems.append("latest run FAILED for: " + "; ".join(failing))
        if unmatched:
            problems.append("never run: " + "; ".join(unmatched) + f" (`pb.py verify {args.id}` runs every item exactly as written)")
        if problems and not args.force:
            die(f"{args.id} not done: " + " | ".join(problems) + ". Fix and re-run, or --force --note '<why>'.", 1)
        if args.force and not note:
            die("--force needs --note explaining why the evidence is missing")
        latest = [e["id"] for _, st, e in states if st == "pass" and e]
        t["status"] = "done"
        t["evidence"] = sorted(set(latest + [ledger[i]["id"] for i in ids if pass_evidence(ledger[i])]))
        if any(e.get("method") == "reported" for e in pool):
            print("WARN  some evidence is only 'reported' by an agent; re-run the checks yourself before release")
    elif args.action == "skip":
        t["status"] = "skip"
    elif args.action == "block":
        t["status"] = "blocked"
    elif args.action == "reopen":
        t["status"] = "todo"
    if note or args.force:
        t["notes"] = (str(t.get("notes", "")).rstrip() + f"\n[{stamp}] {note}").strip()
    save_plan(root, plan)
    print(f"{args.id} -> {t['status']}")
    return 0


def cmd_verify(args, root: Path) -> int:
    """Run every verify command of a task through the ledger, exactly as written in the plan."""
    plan = load_plan(root)
    t = task_map(plan).get(args.id)
    if not t:
        die(f"unknown task {args.id}", 1)
    worst = 0
    if not suite_items(plan):
        print("note: plan.json has no \"suite\" (the full test command); only the task's own checks will run")
    for v in verify_items(t, plan):
        if v.strip().lower().startswith(("observe:", "manual:")):
            print(f"SKIP  {v}  (record it with `pb.py evidence add --task {args.id} --method observed --summary ...`)")
            continue
        ns = argparse.Namespace(command=[v], task=args.id, kind=None, where=args.where, url=None,
                                expect_fail=False, timeout=args.timeout, tail=args.tail)
        code = cmd_run(ns, root)
        worst = worst or (1 if code else 0)
        if code and not args.keep_going:
            print(f"stopped at the first failure (use --keep-going to run the rest)")
            break
    return worst


# ----------------------------------------------------------------------------- diffcheck / intake

SKIP_DIRS = re.compile(r"(^|/)(node_modules|\.venv|venv|env|dist|build|\.next|\.nuxt|\.svelte-kit|coverage|"
                       r"__pycache__|\.pytest_cache|target|vendor|\.turbo|\.cache|\.git)/")


def untracked(root: Path) -> list:
    files = [f for f in git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines() if f.strip()]
    return [f for f in files if not SKIP_DIRS.search(f)]


def changed_files(root: Path, base: str) -> list:
    out = git(root, "diff", "--name-status", "--no-renames", base)
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0][0], norm_path(parts[-1])))
    for f in untracked(root):
        if not f.startswith(".prod-build/logs/"):
            rows.append(("A", norm_path(f)))
    return rows


def added_lines(root: Path, base: str) -> list:
    out = git(root, "diff", "--no-color", "-U0", "--no-renames", base).stdout
    rows, cur, ln = [], None, 0
    for line in out.splitlines():
        if line.startswith("+++ "):
            cur = norm_path(line[6:]) if line[4:6] == "b/" else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and cur:
            rows.append((cur, ln, line[1:]))
            ln += 1
    for f in untracked(root)[:2000]:
        p = root / f
        if f.startswith(".prod-build/") or not p.is_file() or p.stat().st_size > 1_000_000:
            continue
        try:
            for n, text in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                rows.append((norm_path(f), n, text))
        except (UnicodeDecodeError, OSError):
            continue
    return rows


def removed_assertions(root: Path, base: str) -> dict:
    out = git(root, "diff", "--no-color", "-U0", "--no-renames", base).stdout
    per, cur = {}, None
    for line in out.splitlines():
        if line.startswith("--- "):
            cur = norm_path(line[6:]) if line[4:6] == "a/" else None
        elif line.startswith("+++ "):
            continue
        elif cur and TEST_FILE.search(cur) and re.search(r"\b(expect|assert\w*|should)\b", line[1:] if line[:1] in "+-" else ""):
            if line.startswith("-"):
                per[cur] = per.get(cur, 0) + 1
            elif line.startswith("+"):
                per[cur] = per.get(cur, 0) - 1
    return {k: v for k, v in per.items() if v > 0}


ENV_FILE = re.compile(r"(^|/)\.env(\.[A-Za-z0-9_-]+)?$")
ENV_TEMPLATE = re.compile(r"\.(example|sample|template|dist|defaults)$")


def diff_findings(root: Path, base: str) -> list:
    if git(root, "rev-parse", "--verify", base + "^{commit}").returncode != 0:
        die(f"unknown git ref {base!r}", 1)
    found = []
    if not (root / ".gitignore").exists() and (root / "node_modules").is_dir():
        found.append(("warn", ".gitignore", 0, "missing .gitignore while node_modules/ exists: it could get committed"))
    for status, path in changed_files(root, base):
        if path.startswith(".prod-build/"):
            continue
        if status == "D" and TEST_FILE.search(path):
            found.append(("error", path, 0, "test file deleted"))
        elif CONFIG_FILE.search(path):
            found.append(("warn", path, 0, "lint/test/type/CI/agent config changed: justify it in the report"))
        if status != "D" and ENV_FILE.search(path) and not ENV_TEMPLATE.search(path):
            tracked = git(root, "ls-files", "--error-unmatch", path).returncode == 0
            if tracked:
                found.append(("error", path, 0, "env file is tracked by git: untrack it, add it to .gitignore, rotate its secrets"))
    for path, ln, text in added_lines(root, base):
        if path.startswith(".prod-build/"):
            continue  # the helper, plan, briefs and ledger are orchestration files, not product code
        if not path.startswith("docs/memory/"):
            for rx, sev, msg in WEAKENING:
                if re.search(rx, text):
                    found.append((sev, path, ln, msg))
        if not path.endswith((".lock", "lock.json", "lock.yaml")) and looks_like_secret(text):
            in_tests = bool(TEST_FILE.search(path) or re.search(r"(^|/)(fixtures?|__fixtures__|testdata)/", path))
            found.append(("warn" if in_tests else "error", path, ln,
                          "possible secret added" + (" in test code (use an obvious fake)" if in_tests else
                                                     " (rotate it if it is real)")))
        if TEST_FILE.search(path) and re.search(r"\b(expect|assert)\w*\s*\(\s*(true|1)\s*\)", text):
            found.append(("warn", path, ln, "tautological assertion"))
    for path, n in removed_assertions(root, base).items():
        found.append(("warn", path, 0, f"{n} more assertion line(s) removed than added"))
    old_pkg = git(root, "show", f"{base}:package.json")
    if old_pkg.returncode == 0 and (root / "package.json").exists():
        try:
            before = json.loads(old_pkg.stdout).get("scripts") or {}
            after = json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
            for k, v in before.items():
                if re.match(r"^(test|lint|typecheck|type-check|check|verify|build|e2e)(:|$)", k) and after.get(k) != v:
                    found.append(("error", "package.json", 0,
                                  f"script '{k}' changed ({v!r} -> {after.get(k)!r}): a changed check can fake a pass"))
        except (json.JSONDecodeError, AttributeError):
            pass
    old_plan = git(root, "show", f"{base}:.prod-build/plan.json")
    if old_plan.returncode == 0 and (pb_dir(root) / "plan.json").exists():
        try:
            before = task_map(json.loads(old_plan.stdout))
            after = task_map(load_plan(root))
            for tid, t in before.items():
                a = after.get(tid)
                if a is None:
                    found.append(("error", ".prod-build/plan.json", 0, f"{tid} removed from the plan"))
                    continue
                for field in ("acceptance", "verify", "writes"):
                    if t.get(field) != a.get(field):
                        found.append(("warn", ".prod-build/plan.json", 0,
                                      f"{tid}.{field} changed since {base[:10]} (executors may only change status/notes)"))
        except json.JSONDecodeError:
            pass
    return found


def report_base(root: Path, tid: str):
    report = pb_dir(root) / "reports" / f"{tid}.md"
    if report.exists():
        m = re.search(r"(?im)^\s*COMMITS:\s*([0-9a-f]{6,40})\s*\.\.", report.read_text(encoding="utf-8"))
        if m and git(root, "rev-parse", "--verify", m.group(1) + "^{commit}").returncode == 0:
            return m.group(1)
    return None


def pick_base(root: Path, plan: dict, tid, explicit):
    if explicit:
        return explicit
    t = task_map(plan).get(tid) if tid else None
    return (t or {}).get("base") or (report_base(root, tid) if tid else None) or plan.get("base") or "HEAD"


def cmd_diffcheck(args, root: Path) -> int:
    plan = read_json(pb_dir(root) / "plan.json") or {}
    base = pick_base(root, plan, args.task, args.base)
    found = diff_findings(root, base)
    for sev, path, ln, msg in found:
        loc = f"{path}:{ln}" if ln else path
        print(f"{sev.upper():5} {loc}  {msg}")
    errors = sum(1 for f in found if f[0] == "error")
    print(f"diffcheck vs {base}: {errors} error(s), {len(found) - errors} warning(s)")
    return 1 if errors else 0


def cmd_intake(args, root: Path) -> int:
    plan = load_plan(root)
    t = task_map(plan).get(args.id)
    if not t:
        die(f"unknown task {args.id}", 1)
    base = pick_base(root, plan, args.id, args.base)
    problems, notes = [], []
    if base == "HEAD":
        problems.append(f"no BASE recorded for {args.id}: run `pb.py task {args.id} start` before dispatching, "
                        "or pass --base <commit the work started from>")
    report = pb_dir(root) / "reports" / f"{args.id}.md"
    status = None
    if report.exists():
        m = re.search(r"(?im)^\s*STATUS:\s*([A-Z_]+)", report.read_text(encoding="utf-8"))
        status = m.group(1) if m else None
        if not status:
            problems.append(f"{report.relative_to(root)} has no 'STATUS:' line")
    else:
        problems.append(f"no report at .prod-build/reports/{args.id}.md")
    if status in ("BLOCKED", "NEEDS_CONTEXT"):
        notes.append(f"agent reports {status}: answer the blocker before anything else")
    anc = git(root, "merge-base", "--is-ancestor", base, "HEAD")
    if anc.returncode == 1:
        problems.append(f"base {base} is not an ancestor of HEAD (work started from the wrong commit)")
    found = diff_findings(root, base)
    for sev, path, ln, msg in found:
        (problems if sev == "error" else notes).append(f"{path}{':' + str(ln) if ln else ''} {msg}")
    writes = t.get("writes") or []
    if writes:
        allowed = writes + [".prod-build/**", "docs/memory/**"]
        stray = [p for _, p in changed_files(root, base) if not any(paths_overlap(p, w) for w in allowed)]
        if stray:
            notes.append("changed outside the task's write surface: " + ", ".join(stray[:12]))
    ledger = read_ledger(root)
    problems += [f"ledger: {c}" for c in verify_chain(ledger)]
    states = verify_status(t, ledger, plan)
    if not any(pass_evidence(e) for e in ledger if e.get("task") == args.id):
        problems.append(f"no passing evidence recorded for {args.id} (RED runs don't count)")
    for v, st, _ in states:
        if st != "pass":
            notes.append(f"verify '{v}': {st}")
    for p in problems:
        print(f"PROBLEM {p}")
    for n in notes:
        print(f"NOTE    {n}")
    verdict = "REJECT" if problems else "RE-VERIFY"
    print(f"intake {args.id} vs {base}: {verdict}. Re-run the checks yourself (`pb.py verify {args.id}`) "
          f"before `pb.py task {args.id} done`.")
    return 1 if problems else 0


# ----------------------------------------------------------------------------- memory

def parse_record(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[end + 4:]
    meta["_path"] = path
    meta["_body"] = body
    meta["_lines"] = text.count("\n") + 1
    return meta


def split_list(v: str) -> list:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def all_records(root: Path, include_archive: bool = True) -> list:
    m = mem_dir(root)
    recs = []
    folders = [f for _, f in MEM_TYPES.values()] + (["archive"] if include_archive else [])
    for folder in folders:
        d = m / folder
        if d.is_dir():
            for p in sorted(d.glob("*.md")):
                recs.append(parse_record(p))
    return recs


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "record"


BODIES = {
    "decision": "## Context\n\n## Decision\n\n## Alternatives rejected (and why)\n\n## Consequences\n\n## Revisit when\n",
    "failure": ("## Attempted\n\n## Failure (exact error signature)\n\n## Root cause (verified, not guessed)\n\n"
                "## Fix (commit / evidence IDs)\n\n## Lesson\nWhen <situation>, do <action>, because <reason>.\n\n"
                "## Early check\n<the cheapest check that would have caught this>\n"),
    "lesson": "When <situation>, do <action>, because <reason>.\n\nDon't apply when: <boundary>\n\nSource: <F-id or URL (date)>\n",
    "assumption": "## Claim\n\n## Risk if wrong\n\n## Validate by\n\n## Status\nopen\n",
    "feedback": "## What the user said (quote)\n\n## Context\n\n## Decision\naccepted | rejected | pending\n\n## Implication (incl. do-not-propose)\n",
    "pattern": "## Task type\n\n## Approach / agent / prompt shape\n\n## Outcome\n\n## Use when\n",
}


def cmd_mem(args, root: Path) -> int:
    act = args.action
    if act == "add":
        if args.type not in MEM_TYPES:
            die(f"type must be one of {', '.join(MEM_TYPES)}")
        if not args.title:
            die("mem add needs --title")
        prefix, folder = MEM_TYPES[args.type]
        recs = all_records(root)
        nums = [int(r["id"].split("-")[-1]) for r in recs if str(r.get("id", "")).startswith(prefix + "-")
                and r["id"].split("-")[-1].isdigit()]
        rid = f"{prefix}-{(max(nums) + 1 if nums else 1):04d}"
        similar = [r for r in recs if r.get("type") == args.type and r.get("status") == "active"
                   and difflib.SequenceMatcher(None, r.get("title", "").lower(), args.title.lower()).ratio() >= 0.8]
        if similar and not args.force:
            die(f"similar active record exists: {similar[0]['id']} '{similar[0].get('title')}'. "
                "Update it (or mark it superseded) instead; --force to add anyway", 1)
        gi = git_info(root)
        meta = {
            "id": rid, "type": args.type, "title": args.title.replace("\n", " "), "status": "active",
            "scope": args.scope, "components": ", ".join(split_list(args.components)),
            "triggers": ", ".join(split_list(args.triggers)), "evidence": ", ".join(split_list(args.evidence)),
            "verified_at": f"{today()}@{gi['sha'] or 'nogit'}", "relates": ", ".join(split_list(args.relates)),
            "supersedes": args.supersedes or "", "helpful": "0", "harmful": "0",
            "cite_hash": cite_hash(root, split_list(args.evidence)),
            "created": today(), "source": args.source or os.environ.get("PB_AGENT", "unknown"),
        }
        d = mem_dir(root) / folder
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{rid}-{slugify(args.title)}.md"
        body = args.body.replace("\\n", "\n") if args.body else BODIES[args.type]
        path.write_text("---\n" + "".join(f"{k}: {v}\n" for k, v in meta.items()) + "---\n\n" + body,
                        encoding="utf-8")
        if args.supersedes:
            mark(root, args.supersedes, "superseded", by=rid)
        write_index(root)
        print(f"created {path.relative_to(root)} ({rid}); fill in the body, keep it under {CAPS['record_lines']} lines")
        if not meta["evidence"]:
            print("WARN  no --evidence: cite a file (path#L10-40), commit:sha or test:cmd so it can be re-verified")
        return 0
    if act == "find":
        return mem_find(root, args)
    if act == "check":
        return mem_check(root)
    if act == "index":
        write_index(root)
        print("wrote docs/memory/INDEX.md")
        return 0
    if act == "mark":
        return mark(root, args.id, args.how, by=args.by)
    return 2


def citations(rec_or_list) -> list:
    """File citations as (path, anchor) pairs; skips commit:, test:, url:, evidence ids and the ledger itself."""
    raw = split_list(rec_or_list.get("evidence", "")) if isinstance(rec_or_list, dict) else rec_or_list
    out = []
    for c in raw:
        if re.match(r"^(commit|test|url|http|https):", c) or re.match(r"^E\d{4,}$", c):
            continue
        path, _, anchor = c.partition("#")
        path = norm_path(path)
        if path and not path.startswith(".prod-build/"):
            out.append((path, anchor))
    return out


def cited_paths(rec: dict) -> list:
    return [p for p, _ in citations(rec)]


def md_section(text: str, anchor: str):
    """Text of the markdown section whose heading matches the anchor (by text or slug), or None."""
    want = slugify(anchor)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m and (slugify(m.group(2)) == want or m.group(2).strip().lower() == anchor.strip().lower()):
            level, body = len(m.group(1)), []
            for nxt in lines[i + 1:]:
                n = re.match(r"^(#{1,6})\s", nxt)
                if n and len(n.group(1)) <= level:
                    break
                body.append(nxt)
            return "\n".join(body)
    return None


def cite_hash(root: Path, rec_or_list) -> str:
    h = hashlib.sha256()
    for path, anchor in citations(rec_or_list):
        f = root / path
        if not f.is_file():
            h.update(f"missing:{path}".encode())
            continue
        data = f.read_bytes()
        if anchor and path.lower().endswith(".md") and not re.match(r"^L\d", anchor):
            sec = md_section(data.decode("utf-8", "replace"), anchor)
            data = (sec if sec is not None else f"missing-section:{anchor}").encode()
        h.update(path.encode() + b"\0" + data)
    return h.hexdigest()[:16]


def staleness(root: Path, rec: dict) -> str:
    cites = citations(rec)
    if not cites:
        return "uncited"
    if any(not (root / p).exists() for p, _ in cites):
        return "dead-citation"
    for p, a in cites:
        if a and p.lower().endswith(".md") and not re.match(r"^L\d", a):
            if md_section((root / p).read_text(encoding="utf-8", errors="replace"), a) is None:
                return "dead-citation"
    if rec.get("cite_hash") and rec.get("cite_hash") != cite_hash(root, rec):
        return "needs-verify"
    return "fresh"


def mem_find(root: Path, args) -> int:
    paths = [norm_path(p) for p in split_list(args.paths)]
    terms = [t.lower() for t in split_list(args.terms)]
    segs = {s.lower() for p in paths for s in re.split(r"[/._\-]", p) if len(s) > 2}
    scored = []
    for r in all_records(root, include_archive=False):
        if r.get("status") != "active" or (args.type and r.get("type") != args.type):
            continue
        comps = {c.lower() for c in split_list(r.get("components", ""))}
        trig = " ".join(split_list(r.get("triggers", ""))).lower()
        text = (r.get("title", "") + " " + r["_body"]).lower()
        score = 3 * len(comps & segs)
        score += sum(3 for c in comps if any(c in p.lower() for p in paths))
        score += sum(2 for t in terms if t in trig)
        score += sum(min(text.count(t), 3) for t in terms)
        score += sum(2 for cp in cited_paths(r) for p in paths if paths_overlap(cp, p) or cp.startswith(p))
        if score >= args.min_score or (not paths and not terms):
            h, hm = int(r.get("helpful", "0") or 0), int(r.get("harmful", "0") or 0)
            scored.append((score + h - 2 * hm, r))
    scored.sort(key=lambda x: (-x[0], x[1].get("id", "")))
    if not scored:
        print("no matching memory")
        return 0
    for score, r in scored[: args.limit]:
        gist = next((l.strip() for l in r["_body"].splitlines()
                     if l.strip() and not l.startswith("#") and not l.startswith("<")), "")
        st = staleness(root, r)
        flag = "" if st == "fresh" else f" [{st}]"
        print(f"{r.get('id')} ({r.get('type')}, score {score}){flag} {r.get('title')}\n    {gist[:160]}\n    "
              f"{r['_path'].relative_to(root)}")
    return 0


def mem_check(root: Path) -> int:
    errors, warns = [], []
    recs = all_records(root)
    ids = {}
    for r in recs:
        rid = r.get("id") or r["_path"].name
        missing = [k for k in MEM_REQUIRED if k not in r]
        if missing:
            errors.append(f"{rid}: missing field(s) {', '.join(missing)}")
        if rid in ids:
            errors.append(f"{rid}: duplicate id ({ids[rid].name}, {r['_path'].name})")
        ids[rid] = r["_path"]
        if r.get("type") not in MEM_TYPES:
            errors.append(f"{rid}: unknown type {r.get('type')!r}")
        if r.get("status") not in MEM_STATUSES:
            errors.append(f"{rid}: unknown status {r.get('status')!r}")
        if r["_lines"] > CAPS["record_lines"]:
            warns.append(f"{rid}: {r['_lines']} lines (cap {CAPS['record_lines']}); condense it")
        if any(looks_like_secret(l) for l in r["_body"].splitlines()):
            errors.append(f"{rid}: looks like it contains a secret")
        if r.get("status") == "active":
            st = staleness(root, r)
            if st != "fresh":
                warns.append(f"{rid}: {st}" + (f" (re-read the cited code, then `pb.py mem mark {rid} verified`)"
                                                 if st == "needs-verify" else
                                                 (" (cite a file, path#L10-40 or ARCHITECTURE.md#Section)"
                                                  if st == "uncited" else "")))
            if int(r.get("harmful", "0") or 0) >= 2 and int(r.get("harmful", "0") or 0) > int(r.get("helpful", "0") or 0):
                warns.append(f"{rid}: marked harmful more than helpful; archive or rewrite it")
            if not r.get("cite_hash") and cited_paths(r):
                warns.append(f"{rid}: no content hash yet; after checking it, run `pb.py mem mark {rid} verified`")
            if "<situation>" in r["_body"] or "<reason>" in r["_body"]:
                warns.append(f"{rid}: template placeholders still in the body")
    for r in recs:
        for rel in split_list(r.get("relates", "")) + split_list(r.get("supersedes", "")):
            if rel not in ids:
                warns.append(f"{r.get('id')}: refers to unknown record {rel}")
    active = [r for r in recs if r.get("status") == "active"]
    if len(active) > CAPS["active"]:
        warns.append(f"{len(active)} active records (cap {CAPS['active']}); archive stale or merge similar ones")
    by_type = {}
    for r in active:
        by_type.setdefault(r.get("type"), []).append(r)
    for rs in by_type.values():
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                if difflib.SequenceMatcher(None, a.get("title", "").lower(), b.get("title", "").lower()).ratio() >= 0.8:
                    warns.append(f"{a.get('id')} and {b.get('id')} look like duplicates; merge them")
    idx = mem_dir(root) / "INDEX.md"
    if idx.exists() and idx.read_text(encoding="utf-8").count("\n") > CAPS["index_lines"]:
        warns.append(f"INDEX.md over {CAPS['index_lines']} lines")
    for w in warns:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"mem check: {len(recs)} record(s), {len(active)} active, {len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if errors else 0


def mark(root: Path, rid: str, how: str, by: str | None = None) -> int:
    target = next((r for r in all_records(root) if r.get("id") == rid), None)
    if not target:
        die(f"unknown memory record {rid}", 1)
    path = target["_path"]
    text = path.read_text(encoding="utf-8")

    def setf(t, key, val):
        if re.search(rf"(?m)^{key}:.*$", t[: t.find('\n---', 3) + 1]):
            return re.sub(rf"(?m)^{key}:.*$", f"{key}: {val}", t, count=1)
        return t.replace("\n---", f"\n{key}: {val}\n---", 1)

    if how in ("helpful", "harmful"):
        text = setf(text, how, str(int(target.get(how, "0") or 0) + 1))
    elif how == "verified":
        text = setf(text, "verified_at", f"{today()}@{git_info(root)['sha'] or 'nogit'}")
        text = setf(text, "cite_hash", cite_hash(root, target))
    elif how in ("superseded", "archived", "invalidated"):
        text = setf(text, "status", how)
        if by:
            text = setf(text, "superseded_by", by)
    else:
        die(f"unknown mark {how!r}")
    path.write_text(text, encoding="utf-8")
    if how in ("superseded", "archived", "invalidated"):
        dest = mem_dir(root) / "archive" / path.name
        path.replace(dest)
    write_index(root)
    print(f"{rid}: {how}" + (f" by {by}" if by else ""))
    return 0


def write_index(root: Path) -> None:
    m = mem_dir(root)
    m.mkdir(parents=True, exist_ok=True)
    recs = [r for r in all_records(root, include_archive=False) if r.get("status") == "active"]
    order = {t: i for i, t in enumerate(MEM_TYPES)}
    recs.sort(key=lambda r: (order.get(r.get("type"), 9), r.get("id", "")))
    archived = len(all_records(root)) - len(all_records(root, include_archive=False))
    rows = recs[: CAPS["index_lines"] - 12]
    lines = ["# Memory index", "",
             "Generated by `pb.py mem index`; do not edit by hand. Records are hints until their cited code is re-checked.",
             f"Active: {len(recs)} | archived/superseded: {archived} | search: `python3 .prod-build/pb.py mem find --paths .. --terms ..`",
             "", "| ID | Type | Components | Title | File |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r.get('id')} | {r.get('type')} | {r.get('components', '')} | {r.get('title', '')} | "
                     f"{r['_path'].relative_to(m).as_posix()} |")
    if len(recs) > len(rows):
        lines.append(f"\n{len(recs) - len(rows)} more active records: use `mem find`.")
    (m / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- report

def cmd_report(args, root: Path) -> int:
    path = Path(args.file)
    if not path.is_absolute():
        path = (Path.cwd() / path) if (Path.cwd() / path).exists() else root / path
    if not path.exists():
        die(f"no report at {path}", 1)
    text = path.read_text(encoding="utf-8")
    ledger = {e.get("id"): e for e in read_ledger(root)}
    errors, warns = [], []
    sections, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^#{2,3}\s+(.*)$", line)
        if m:
            cur = m.group(1).strip().lower().replace("optimisation", "optimization")
            sections[cur] = []
        elif cur is not None:
            sections[cur].append(line)
    for req in REPORT_SECTIONS:
        if not any(k.startswith(req) or req in k for k in sections):
            errors.append(f"missing section '{req}'")
    for name, lines in sections.items():
        if not any(name.startswith(c) for c in CLAIM_SECTIONS):
            continue
        for line in lines:
            if not re.match(r"^\s*([-*]|\d+[.)])\s+\S", line):
                continue
            ids = EVIDENCE_ID.findall(line)
            if not ids and name.startswith("bugs") and re.search(r"(?i)\bnone\b", line):
                warns.append(f"[{name}] 'none' without evidence: cite the bug-hunt/review evidence id")
                continue
            if not ids and not NOT_RUN.search(line):
                errors.append(f"[{name}] claim without evidence id or 'not run': {line.strip()[:110]}")
            if name.startswith("deployment") and ids and not NOT_RUN.search(line):
                if not any((ledger.get(i) or {}).get("where") in ("preview", "prod") for i in ids):
                    errors.append(f"[{name}] deployment claim cites only local evidence: {line.strip()[:90]}")
            for i in ids:
                e = ledger.get(i)
                if e and e.get("expect") == "fail" and not re.search(r"(?i)\b(red|fail\w*|expected)\b", line):
                    errors.append(f"[{name}] cites {i}, a RED (expected-failure) run, as if it proved a pass")
                    continue
                if not e:
                    errors.append(f"[{name}] cites {i}, which is not in the ledger")
                elif not e.get("ok") and not re.search(r"(?i)\b(fail\w*|red|expected|broke\w*|regress\w*)\b", line):
                    errors.append(f"[{name}] cites {i}, which did not pass: {line.strip()[:90]}")
                elif e.get("method") == "reported":
                    warns.append(f"[{name}] {i} is an agent-reported result, not re-run by the orchestrator")
    mem_ids = re.findall(r"\b(ADR|F|L|A|U|P)-\d{4}\b", "\n".join(sections.get("memory", [])))
    known = {r.get("id") for r in all_records(root)}
    for prefix_num in re.findall(r"\b(?:ADR|F|L|A|U|P)-\d{4}\b", "\n".join(
            l for k, v in sections.items() if k.startswith("memory") for l in v)):
        if prefix_num not in known:
            warns.append(f"[memory] {prefix_num} not found in docs/memory")
    if not mem_ids and not any("none" in l.lower() for k, v in sections.items() if k.startswith("memory") for l in v):
        warns.append("[memory] no memory IDs listed (write 'none' if nothing was worth keeping)")
    chain = verify_chain(list(read_ledger(root)))
    errors += [f"ledger: {c}" for c in chain]
    for w in warns:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"report check: {len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if errors else 0


# ----------------------------------------------------------------------------- event clock, window, submission

EVENT_KEYS = ("name", "start", "end", "freeze", "freeze_set", "source", "provisional", "history", "no_window",
              "tz_min", "video_cap_s", "card_chars", "sponsors", "judge_minutes", "ai_disclosure", "set_at",
              "changes")
FREEZE_KINDS = ("fix", "bugfix", "hotfix", "verify", "qa", "review", "deploy", "docs", "submit", "record", "smoke",
                "test", "e2e", "lint", "typecheck", "security", "manual")
_NAME_DOC = r"\.(md|markdown|mdx|txt|pdf|docx?|rtf)"
_DIR_DOC = r"\.(md|markdown|mdx|txt|json|pdf|docx?|rtf|html?)"
INTERNAL_PATHS = re.compile(
    r"(?i)(^|/)(judging[-_ ]?(intel|notes?)[^/]*|idea[-_ ]?package[^/]*|judges?[-_ ]?(notes?|profiles?|intel)[^/]*)"
    + _NAME_DOC + r"$|(^|/)(notes/)?(hackathon|prod)[-_ ]?ideas?/([^/]+/)*[^/]+" + _DIR_DOC + r"$")
DRAFT_PATHS = re.compile(r"(?i)(^|/)(SHOOT|SCRIPT|VIDEO[-_ ]?SCRIPT|SUBMISSION|PITCH([-_ ]?NOTES?)?|DEMO[-_ ]?SCRIPT)"
                         r"\.(md|txt)$")
INTERNAL_PHRASES = re.compile(
    r"(?i)\bjudging[- ]intel(ligence)?\b|\bworth (one|a) message\b|\bwhat (the )?judges (want|reward|look for)\b|"
    r"\bjudge (psychology|profile)s?\b")
GITIGNORE_BASE = ["node_modules/", ".env", ".env.*", "!.env.example", ".prod-build/logs/", "dist/", ".next/",
                  "__pycache__/", ".venv/", "coverage/", ".claude/worktrees/"]
IGNORE_BLOCK = """# prod-build: planning notes stay out of a judged repository
hackathon-idea/
prod-idea/
notes/hackathon-idea/
judging-intel*.md
judging-notes*.md
judge-notes*.md
idea-package*.md
idea_package*.md
SUBMISSION.md
submission.md
SHOOT.md
SCRIPT.md
PITCH.md
PITCH-NOTES.md
DEMO-SCRIPT.md
VIDEO-SCRIPT.md
video-script.md
# browser session files hold live sign-in cookies
storage-state*.json
storage_state*.json
*.storage-state.json
"""
SESSION_FILES = re.compile(r"(?i)(^|/)(storage[-_]?state[^/]*|[^/]*[.\-_]storage[-_]?state)\.json$")
MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)|<img\b[^>]*\bsrc=", re.I)
MD_LINK = re.compile(r"\[([^\]]*)\]\(\s*([^)\s]*)[^)]*\)")
MD_LINK_FULL = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
BARE_URL = re.compile(r"https?://[^\s)>\]\"'`]+")
VIDEO_HOST = re.compile(r"(?i)(youtube\.com|youtu\.be|loom\.com|vimeo\.com|drive\.google\.com/file|\.mp4\b)")
CODE_HOST = re.compile(r"(?i)(github\.com|gitlab\.com|bitbucket\.org|shields\.io|badge|npmjs\.com|pypi\.org|"
                       r"localhost|127\.0\.0\.1|example\.(com|org|net)|creativecommons\.org|opensource\.org|"
                       r"wikipedia\.org|docs\.|/docs/|developer\.)")

# Unfinished text a judge would see. Every pattern runs on prose only: fenced blocks, indented code and
# `inline code` are removed first, because a curl sample or an env template is not a placeholder.
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")
_LABEL = r"(?:demo\s+video|video|demo|live(?:\s+(?:demo|link|site|app))?|link|screenshots?|deck|slides|site|url)s?"
PLACEHOLDER_TARGET = re.compile(
    r"(?i)^(add[_ -]?(link|url)|link|url|todo|tbd|tba|insert[_ -]?(link|url)|link[-_ ]?here|"
    r"your[-_ ]?[a-z_ -]*(link|url|video|id)|video[-_ ]?(id|url|link)|demo[-_ ]?(url|link))$")
PLACEHOLDER_URL = re.compile(
    r"(?i)^https?://\S*?(\bexample\.(com|org|net)\b|\.example(?=[/:?#]|$)|\byour[-_]?(app|project|site|video|link|domain|"
    r"username|name)\b|x{5,}|VIDEO[_-]?ID|<[^>]*>|\{[^}]*\})")
EXAMPLE_URL = re.compile(r"(?i)https?://(www\.)?example\.(com|org|net)\b")
LOCAL_URL = re.compile(r"(?i)^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)\b")
LINKISH_TEXT = re.compile(r"(?i)\b(video|demo|live|watch|try|deck|slides|site|app|link|here|launch|open|blog|post)\b")
LIVE_TEXT = re.compile(r"(?i)\b(live|demo|try|deployed|production|launch)\b")
RX_PROMISED = re.compile(
    r"(?i)^[\s>*_\-+#|]*" + _LABEL + r"[\W_]{0,12}(link\s+)?(to follow|coming soon|to be added|to come|tbd|tba|"
    r"goes here|will be (added|uploaded|linked|recorded)|not yet (uploaded|recorded))\b")
RX_PROMISED_ANY = re.compile(
    r"(?i)\b(?:" + _LABEL + r"|recording|walkthrough)\b[^.\n]{0,40}\b(to follow|coming soon|to be added|to come|"
    r"will be (added|uploaded|linked|recorded)|not yet (uploaded|recorded))\b"
    r"|\b(links?|videos?|urls?)\s+(to follow|coming soon|to be added)\b")
RX_TOKEN = re.compile(r"\b(ADD_LINK|ADD_URL|INSERT_[A-Z_]+|REPLACE_ME|YOUR_[A-Z_]*(LINK|URL|VIDEO)[A-Z_]*|VIDEO_ID|"
                      r"VIDEO_URL|DEMO_URL|DEMO_LINK)\b|(?i:lorem ipsum)")
RX_BRACKET = re.compile(r"(?i:\[(link|url|tbd|tba|todo|video link|demo link|add link|insert link)\](?!\())|"
                        r"(?i:<\s*(add|insert|your|paste)[^>\n]{0,30}(link|url)\s*>)|<[A-Z0-9_ ]*(LINK|URL|_ID)>")
RX_SELFNOTE = re.compile(
    r"(?i)\bnote to self\b|\b(replace|remove|delete) this (line|section|text|paragraph)\b|"
    r"\b(todo\s*:|fixme\b|tbd\b|tba\b)[^\n]{0,60}\b(link|video|demo|url|screenshot)s?\b|"
    r"\b(link|video|demo|url|screenshot)s?\b[^\n]{0,40}\b(todo\s*:|fixme\b|tbd\b|tba\b)")
RX_COMMENT = re.compile(r"(?s)<!--(.{0,400}?)-->")
RX_COMMENT_NOTE = re.compile(
    r"(?i:\b(replace this|fill (this )?in|before submitting|note to self|add (the |a )?(link|video|screenshot|url)|"
    r"remove (this|before))\b)|\bTODO\b|\bFIXME\b|(?i:\btodo\s*:)")
RX_LABELLED_URL = re.compile(r"(?i)^[\s>*_\-+#|]*" + _LABEL + r"[\W_]{0,12}(https?://\S+)")
HTML_TAIL = re.compile(r"(?i)(</?(br|p|div|span|a|li|td|th|b|i|strong|em|sub|sup|center)\b[^>]*>)+$")
SOFT_PLACEHOLDERS = re.compile(r"\b(TODO|TBD|FIXME|WIP|YOUR_[A-Z_]+)\b|(?i:\bcoming soon\b)")
AI_TOOLS = re.compile(r"(?i)\b(claude|copilot|cursor|chatgpt|codex|gemini|windsurf|aider|kiro|amazon q|"
                      r"ai (coding )?(tools?|assistants?|agents?)|co-authored-by)\b")
LIMITS_HEAD = re.compile(r"(?im)^\s*(#{1,6}\s+|\*\*)[^\n]{0,60}\b(limits?|limitations?|not built|not yet|honest|"
                         r"scope|simulated|mocked|what is real|what's real|known issues?|what it does(n'| no)t)\b")
WINDOW_LINE = re.compile(r"(?i)(first commit|built (during|inside|within|between)\b|built in (the )?(\d+|a |one |two |"
                         r"three )|build window|hackathon window|hacking began|before the (clock|start|window)\b|"
                         r"(after|when) the (clock|window|hackathon) (started|opened))")
README_NAME = re.compile(r"(?i)^readme(\.(md|markdown|mdown|mkd|mdx|rst|txt|org|adoc|asciidoc|textile))?$")


def instant_or_none(s: str):
    """ISO-8601 text -> datetime (aware when the text carried a zone), or None when it does not parse."""
    if not isinstance(s, str):
        return None
    txt = re.sub(r"[zZ]$", "+00:00", s.strip())
    txt = re.sub(r"^(\d{4}-\d{2}-\d{2})[ T]", r"\1T", txt)
    txt = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", txt)
    try:
        return _dt.datetime.fromisoformat(txt)
    except ValueError:
        return None


def parse_instant(s: str) -> _dt.datetime:
    """ISO-8601 instant with an explicit zone, returned in UTC. A zone-less time is refused: that is how a
    start gets read as local when the page meant something else."""
    d = instant_or_none(s)
    if d is None:
        die(f"not an ISO-8601 instant: {s!r} (write 2026-03-14T09:00:00Z or 2026-03-14T14:30:00+05:30)")
    if d.tzinfo is None:
        die(f"{s!r} has no time zone: add Z or an offset such as +05:30, copied from the event page")
    return d.astimezone(_dt.timezone.utc)


def git_instant(s: str):
    d = instant_or_none(s)
    return d.astimezone(_dt.timezone.utc) if d is not None and d.tzinfo is not None else None


def iso_z(d: _dt.datetime) -> str:
    return d.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def offset_minutes(s: str):
    """Offset of an instant string or of '+05:30' / '-0700' / 'Z', in minutes; None when there is none."""
    s = (s or "").strip()
    if re.search(r"[zZ]$", s):
        return 0
    m = re.search(r"([+-])(\d{2}):?(\d{2})$", s)
    if not m:
        return None
    return (1 if m.group(1) == "+" else -1) * (int(m.group(2)) * 60 + int(m.group(3)))


_FAKE_SAID = []


def fake_now():
    return os.environ.get("PB_FAKE_NOW") or None


def now_utc() -> _dt.datetime:
    fake = fake_now()
    if fake:
        if not _FAKE_SAID:
            _FAKE_SAID.append(1)
            print(f"TEST CLOCK: PB_FAKE_NOW={fake} (unset it for a real check)")
        return parse_instant(fake)
    return _dt.datetime.now(_dt.timezone.utc)


def span(td: _dt.timedelta) -> str:
    secs = int(round(abs(td.total_seconds()) / 60.0)) * 60
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h {m}m"
    return f"{h}h {m}m" if h else f"{m}m"


def local_label(d: _dt.datetime, tz_min) -> str:
    if tz_min is None:
        return ""
    tz = _dt.timezone(_dt.timedelta(minutes=tz_min))
    sign = "+" if tz_min >= 0 else "-"
    return d.astimezone(tz).strftime("%a %d %b %H:%M ") + f"{sign}{abs(tz_min) // 60:02d}:{abs(tz_min) % 60:02d}"


def event_path(root: Path) -> Path:
    return pb_dir(root) / "event.json"


def event_problems(ev: dict) -> dict:
    """Keys of a stored event record that cannot be used, each with the reason. {} when the record is sound."""
    bad = {}
    for k in ("start", "end", "freeze"):
        v = ev.get(k)
        if v in (None, ""):
            continue
        d = instant_or_none(v)
        if d is None or d.tzinfo is None:
            bad[k] = f"'{k}' is not an instant with a zone ({v!r})"
    if not bad:
        if bool(ev.get("start")) != bool(ev.get("end")):
            k = "start" if ev.get("start") else "end"
            bad[k] = "'start' and 'end' must both be set"
        elif ev.get("start") and parse_instant(ev["end"]) <= parse_instant(ev["start"]):
            bad["end"] = "'end' is not after 'start'"
        elif ev.get("start") and ev.get("freeze") and not (
                parse_instant(ev["start"]) < parse_instant(ev["freeze"]) <= parse_instant(ev["end"])):
            bad["freeze"] = f"'freeze' is outside the window ({ev['freeze']!r})"
    tz = ev.get("tz_min")
    if tz is not None and not (isinstance(tz, int) and not isinstance(tz, bool) and abs(tz) <= 14 * 60):
        bad["tz_min"] = f"'tz_min' must be whole minutes between -840 and 840 ({tz!r})"
    if ev.get("history") not in (None, "strict", "none"):
        bad["history"] = f"'history' must be \"strict\" or \"none\" ({ev.get('history')!r})"
    if "sponsors" in ev and not (isinstance(ev["sponsors"], list) and all(isinstance(s, str) for s in ev["sponsors"])):
        bad["sponsors"] = "'sponsors' must be a list of names"
    for k in ("card_chars", "video_cap_s", "judge_minutes"):
        v = ev.get(k)
        if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0):
            bad[k] = f"'{k}' must be a positive number ({v!r})"
    for k in ("no_window", "name", "source", "provisional"):
        if ev.get(k) is not None and not isinstance(ev[k], str):
            bad[k] = f"'{k}' must be text"
    return bad


def load_event(root: Path, repair: bool = False):
    """The stored event record, or None. A record edited by hand into something unusable stops every command
    with one line naming the key, because a clock that silently reads as 'no clock' gates nothing. With
    repair=True (only `clock set`) the unusable keys are dropped so new flags can replace them."""
    try:
        raw = event_path(root).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError as e:
        if repair:
            return None             # `clock set` writes a fresh record over a file that cannot be read
        die(f".prod-build/event.json is not valid JSON ({e}). Set the clock again: `pb.py clock set --start <instant> "
            "--end <instant> --source <event page>`. Nothing is gated until it is.", 1)
    if not isinstance(ev, dict):
        if repair:
            return None
        die(".prod-build/event.json is not a JSON object. Set the clock again: `pb.py clock set --start <instant> "
            "--end <instant> --source <event page>`", 1)
    bad = event_problems(ev)
    if bad and not repair:
        die(".prod-build/event.json cannot be used: " + "; ".join(bad.values()) + ". Set it again with `pb.py clock "
            "set --start <instant> --end <instant>`. Nothing is gated until it is.", 1)
    for k in bad:
        ev.pop(k, None)
    if bad and ("start" in bad or "end" in bad):
        for k in ("start", "end", "freeze", "freeze_set"):
            ev.pop(k, None)
    return ev


def has_clock(ev) -> bool:
    """True when the record settles the question: a window with both instants, or a quoted 'no window' rule."""
    return bool(ev and (ev.get("no_window") or (ev.get("start") and ev.get("end"))))


def default_freeze(start: _dt.datetime, end: _dt.datetime) -> _dt.datetime:
    """The last 12% of the window, at least two hours and at most a day, and never more than a quarter of the
    window: time for the take, the page and a signed-out check of every link, not for building."""
    window = end - start
    hold = max(_dt.timedelta(hours=2), min(_dt.timedelta(hours=24), window * 0.12))
    return end - min(hold, window / 4)


def build_event(args, old=None) -> dict:
    """Event record from CLI flags, layered over an existing record."""
    ev = dict(old or {})
    start_s, end_s = getattr(args, "start", None), getattr(args, "end", None)
    no_window = (getattr(args, "no_window", None) or "").strip()
    hist_flag = getattr(args, "history", None)
    if no_window and (start_s or end_s):
        die("--no-window and --start/--end contradict each other: the event has a build window or it does not")
    if no_window:
        if len(re.findall(r"\w+", no_window)) < 4:
            die("--no-window takes the rule that says so, quoted from the event's own page")
        ev.update({"no_window": no_window, "history": "none"})
        for k in ("start", "end", "freeze", "freeze_set", "provisional"):
            ev.pop(k, None)
    if start_s or end_s:
        if not (start_s or ev.get("start")) or not (end_s or ev.get("end")):
            die("the event clock needs both --start and --end")
        start = parse_instant(start_s or ev["start"])
        end = parse_instant(end_s or ev["end"])
        if end <= start:
            die(f"--end ({iso_z(end)}) is not after --start ({iso_z(start)})")
        was_none = ev.pop("no_window", None)
        ev["start"], ev["end"] = iso_z(start), iso_z(end)
        if not getattr(args, "freeze", None):
            kept = None
            if ev.get("freeze_set") and ev.get("freeze"):
                fr = parse_instant(ev["freeze"])
                kept = fr if start < fr <= end else None
            if kept is not None:
                print(f"freeze kept at {iso_z(kept)} (it was set by hand; pass --freeze to move it)")
            else:
                ev["freeze"] = iso_z(default_freeze(start, end))
                ev.pop("freeze_set", None)
        off = offset_minutes(start_s or "")
        if off is not None and ("tz_min" not in ev or off != 0):
            ev["tz_min"] = off  # show local times in the zone the start was written in
        if was_none and not hist_flag:
            ev["history"] = "strict"  # a window replaces "no window": its default rule is the strict one
        else:
            ev.setdefault("history", "strict")
    if getattr(args, "freeze", None):
        if not ev.get("start"):
            die("--freeze needs --start and --end")
        fr = parse_instant(args.freeze)
        if not (parse_instant(ev["start"]) < fr <= parse_instant(ev["end"])):
            die(f"--freeze ({iso_z(fr)}) must fall inside the window, after the start")
        ev["freeze"], ev["freeze_set"] = iso_z(fr), True
    if getattr(args, "tz", None) and ev.get("start"):
        tz = offset_minutes(args.tz)
        if tz is None or abs(tz) > 14 * 60:
            die("--tz takes an offset such as +05:30 or -07:00")
        ev["tz_min"] = tz
    for flag, key in (("event", "name"), ("source", "source"), ("history", "history"),
                      ("video_cap", "video_cap_s"), ("card_chars", "card_chars"),
                      ("judge_minutes", "judge_minutes")):
        v = getattr(args, flag, None)
        if v not in (None, ""):
            if key in ("video_cap_s", "card_chars", "judge_minutes") and not (
                    isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0):
                die(f"--{flag.replace('_', '-')} must be a positive number ({v!r})")
            ev[key] = v
    if (getattr(args, "provisional", None) or "").strip():
        ev["provisional"] = args.provisional.strip()
    elif getattr(args, "confirmed", False):
        ev.pop("provisional", None)
    if getattr(args, "sponsor", None):
        ev["sponsors"] = [s.strip() for s in args.sponsor if s.strip()]
    if getattr(args, "ai_disclosure", False):
        ev["ai_disclosure"] = True
    if old:
        was = {k: old[k] for k in ("start", "end", "freeze", "history", "no_window")
               if old.get(k) is not None and old.get(k) != ev.get(k)}
        if was:
            ev["changes"] = (list(old.get("changes") or []) + [{"at": iso_z(now_utc()), "was": was}])[-10:]
    ev["set_at"] = iso_z(now_utc())
    return {k: ev[k] for k in EVENT_KEYS if k in ev}


def event_flags_given(args) -> bool:
    return any(getattr(args, k, None) for k in ("start", "end", "freeze", "no_window", "event", "source", "history",
                                                "video_cap", "card_chars", "judge_minutes", "sponsor", "tz",
                                                "ai_disclosure", "provisional", "confirmed"))


def event_phase(ev, now: _dt.datetime = None) -> dict:
    """phase: none | before | building | freeze | closed, with the instants as datetimes."""
    if not ev or not ev.get("start") or not ev.get("end"):
        return {"phase": "none"}
    start, end = parse_instant(ev["start"]), parse_instant(ev["end"])
    freeze = parse_instant(ev["freeze"]) if ev.get("freeze") else default_freeze(start, end)
    now = now or now_utc()
    phase = "before" if now < start else "closed" if now > end else "freeze" if now >= freeze else "building"
    return {"phase": phase, "start": start, "end": end, "freeze": freeze, "now": now}


PREP_ALLOWED = ("Allowed before the start: reading the rules, docs and past winners; the idea package, UI spec, "
                "contract, plan and briefs, written but not committed; account, quota and deploy checks with one "
                "real call each; installing tools. Not before the start: project code, seed scripts or demo data "
                "that will ship, the repository (git init, a remote), a commit, a push, or anything you would have "
                "to hide. A prototype of the project is the project: practise on something else.")
FREEZE_MEANS = ("freeze: stop building. Fix only what the rehearsed judge path trips over, then record, write the "
                "page and open every link signed out.")
NO_CLOCK = ("no event clock (hackathon builds need one: `pb.py clock set --start <instant> --end <instant> "
            "--source <event page>`, or `--no-window '<the rule, quoted>'` where the event has none)")


def phase_text(ph: dict) -> str:
    now = ph["now"]
    return {"before": f"BEFORE: starts in {span(ph['start'] - now)}",
            "building": f"BUILDING: freeze in {span(ph['freeze'] - now)}, ends in {span(ph['end'] - now)}",
            "freeze": f"FREEZE: ends in {span(ph['end'] - now)}",
            "closed": f"CLOSED: ended {span(now - ph['end'])} ago"}[ph["phase"]]


def clock_summary(ev) -> str:
    """One line for the status board."""
    if not has_clock(ev):
        return NO_CLOCK
    if ev.get("no_window"):
        return f"event: {ev.get('name') or '(unnamed)'} | no build window: {ev['no_window']}"
    ph = event_phase(ev)
    return (f"clock: now {iso_z(ph['now'])}  {local_label(ph['now'], ev.get('tz_min'))}".rstrip()
            + f"  phase {phase_text(ph)}" + ("  (PROVISIONAL)" if ev.get("provisional") else ""))


def clock_lines(ev) -> list:
    if not ev:
        return [NO_CLOCK]
    if ev.get("no_window"):
        return [f"event: {ev.get('name') or '(unnamed)'} | no build window: {ev['no_window']}"]
    ph = event_phase(ev)
    if ph["phase"] == "none":
        return ["event clock is incomplete (no start or end): run `pb.py clock set --start <instant> --end <instant>`"]
    tz = ev.get("tz_min")
    out = [f"event: {ev.get('name') or '(unnamed)'} | source: {ev.get('source') or 'NOT RECORDED'}"]
    if ev.get("provisional"):
        out.append(f"  PROVISIONAL: {ev['provisional']}. Re-read the instants from the event's own page before "
                   "building, then `pb.py clock set --start .. --end .. --confirmed`.")
    for label, key in (("start ", "start"), ("freeze", "freeze"), ("end   ", "end")):
        d = ph[key]
        out.append(f"  {label} {iso_z(d)}  {local_label(d, tz)}".rstrip())
    out.append(f"  now    {iso_z(ph['now'])}  {local_label(ph['now'], tz)}".rstrip() + f"  phase {phase_text(ph)}")
    strict = ev.get("history", "strict") == "strict"
    out.append("  history rule: " + ("strict: the first commit is at or after the start, and no code written before "
                                     "it is copied in" if strict else
                                     "earlier work is allowed by this event's rules; still say what existed before"))
    if ph["phase"] == "before" and strict:
        out.append("  " + PREP_ALLOWED)
    if ph["phase"] == "freeze":
        out.append("  " + FREEZE_MEANS)
        out.append("  task kinds that may start now: " + ", ".join(FREEZE_KINDS))
    return out


def ensure_ignores(root: Path) -> list:
    """Planning notes and session files are ignored before the first `git add` can pick them up."""
    said = []
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("\n".join(GITIGNORE_BASE) + "\n" + IGNORE_BLOCK, encoding="utf-8")
        said.append("created .gitignore (dependencies, env files, and planning notes that stay out of a judged repository)")
    elif "prod-build: planning notes" not in gitignore.read_text(encoding="utf-8", errors="replace"):
        have = gitignore.read_text(encoding="utf-8", errors="replace")
        with gitignore.open("a", encoding="utf-8") as fh:
            fh.write(("" if have.endswith("\n") or not have else "\n") + IGNORE_BLOCK)
        said.append("added to .gitignore: idea packages, judging notes, filming scripts and browser session files "
                    "(they stay out of a judged repository)")
    return said


def cmd_clock(args, root: Path) -> int:
    if args.action == "set":
        if not event_flags_given(args):
            die("usage: pb.py clock set --start <instant> --end <instant> [--freeze <instant>] [--event NAME] "
                "[--source URL] [--history strict|none] [--provisional '<why>' | --confirmed] [--video-cap SECONDS] "
                "[--card-chars N] [--sponsor NAME ...] [--ai-disclosure] | --no-window '<the rule, quoted>'")
        ev = build_event(args, load_event(root, repair=True))
        if not has_clock(ev):
            die("the event clock needs --start and --end (or --no-window '<the rule, quoted>')")
        pb_dir(root).mkdir(parents=True, exist_ok=True)
        before = load_event(root, repair=True) or {}
        write_json(event_path(root), ev)
        for line in ensure_ignores(root):
            print(line)
        moved = {k: before.get(k) for k in ("start", "end", "freeze", "history", "no_window")
                 if before.get(k) is not None and before.get(k) != ev.get(k)}
        if moved and (pb_dir(root) / "plan.json").exists():
            was = ", ".join(f"{k} was {v}" for k, v in moved.items())
            print(f"WARN  the clock changed after the plan was made ({was}): this moves the start, freeze and "
                  "deadline gates. Change it only when the event's own page changed, quote the change in contract "
                  "§12, and list it with the overrides in the delivery report")
            rec = append_evidence(root, {
                "task": None, "kind": "review", "method": "reported", "cmd": ("pb.py " + ARGV_TEXT[0])[:400],
                "exit": 0, "expect": "pass", "ok": True, "git": git_info(root), "tree": tree_hash(root),
                "where": "local", "tail": [f"EVENT CLOCK CHANGED ({iso_z(now_utc())}): {was}"]})
            print(f"[{rec['id']}] recorded")
        if not ev.get("source") and not ev.get("no_window"):
            print("WARN  no --source: record the event page (and the quoted line) these instants came from")
        if ev.get("start"):
            hours = (parse_instant(ev["end"]) - parse_instant(ev["start"])).total_seconds() / 3600
            if hours < 2 or hours > 24 * 60:
                print(f"WARN  the window is {hours:.1f} hours long; check the instants against the event page")
    ev = load_event(root)
    for line in clock_lines(ev):
        print(line)
    return 0 if has_clock(ev) else 1


def commit_times(root: Path, *refs: str) -> list:
    """[(sha, earliest_of_author_and_committer, latest_of_both, subject)] oldest first; [] without history."""
    out = git(root, "log", "--format=%H%x09%aI%x09%cI%x09%s", *(refs or ("HEAD",)))
    if out.returncode != 0:
        return []
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 3:
            continue
        a, c = git_instant(parts[1]), git_instant(parts[2])
        if a is None or c is None:
            continue
        rows.append((parts[0], min(a, c), max(a, c), parts[3] if len(parts) > 3 else ""))
    rows.sort(key=lambda r: r[1])
    return rows


def repo_state(root: Path) -> dict:
    """Whether `root` is its own repository: own | nested (inside a larger one) | none."""
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        return {"kind": "none", "top": None}
    try:
        same = Path(top.stdout.strip()).resolve() == root.resolve()
    except OSError:
        same = True
    return {"kind": "own" if same else "nested", "top": top.stdout.strip()}


def window_findings(root: Path, start: _dt.datetime, end: _dt.datetime, tz_min=None, now: _dt.datetime = None,
                    provisional: bool = False) -> dict:
    """Compare the repository's history with the event window. FAIL = commits dated before the start, or a
    history that cannot be seen whole."""
    fails, warns, info = [], [], []
    repo = repo_state(root)
    if repo["kind"] == "nested":
        info.append(f"this folder is inside the repository at {repo['top']}, so it has no history of its own to "
                    "check. A judged entry needs its own repository: create it in this folder after the start")
        return {"fails": fails, "warns": warns, "info": info, "rows": [], "certified": False,
                "unchecked": "no repository of its own"}
    rows = commit_times(root)
    if not rows:
        info.append("no commits yet: create the repository and make the first commit after the start")
        return {"fails": fails, "warns": warns, "info": info, "rows": rows, "certified": False,
                "unchecked": "no commits yet"}
    first, last = rows[0], max(rows, key=lambda r: r[2])
    info.append(f"commits on HEAD: {len(rows)} | first {first[0][:7]} {iso_z(first[1])} | "
                f"last {last[0][:7]} {iso_z(last[2])}")
    shallow = git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    if shallow:
        fails.append("this is a shallow clone, so commits older than the ones fetched are not visible here; run "
                     "`git fetch --unshallow` and check again")
    early = [r for r in rows if r[1] < start]
    late = [r for r in rows if r[2] > end]
    if early:
        fails.append(f"{len(early)} of {len(rows)} commits are dated before the start; the earliest is "
                     f"{early[0][0][:7]} at {iso_z(early[0][1])}, {span(start - early[0][1])} early "
                     f"(\"{early[0][3][:60]}\")")
    if late:
        worst = max(late, key=lambda r: r[2])
        warns.append(f"{len(late)} commit(s) are dated after the end (latest {worst[0][:7]} at {iso_z(worst[2])}): "
                     "judges may discount them; after the deadline touch only a dead link, and say so")
    on_head = {r[0] for r in rows}
    pushed = [r for r in commit_times(root, "--remotes") if r[0] not in on_head and r[1] < start]
    local = [r for r in commit_times(root, "--branches", "--tags") if r[0] not in on_head and r[1] < start
             and r[0] not in {p[0] for p in pushed}]
    if pushed:
        fails.append(f"{len(pushed)} commit(s) on pushed branches other than this one are dated before the start "
                     f"(earliest {pushed[0][0][:7]} at {iso_z(pushed[0][1])}): a pushed branch is part of the public "
                     "history")
    if local:
        warns.append(f"{len(local)} commit(s) on local branches or tags are dated before the start (earliest "
                     f"{local[0][0][:7]}): do not push them to the judged repository")
    certified = not (early or shallow or pushed or local)
    if certified and provisional:
        info.append("no statement to print yet: the start is provisional. Re-read it from the event's page, then "
                    "`pb.py clock set --start .. --end .. --confirmed`, and run this again")
    elif certified:
        when = local_label(first[1], tz_min) or iso_z(first[1])
        began = local_label(start, tz_min) or iso_z(start)
        info.append("true statement for the README and the form: \"The first commit "
                    f"({first[0][:7]}) is timestamped {when} ({iso_z(first[1])}), after the build opened at {began}.\"")
    return {"fails": fails, "warns": warns, "info": info, "rows": rows, "certified": certified}


NOT_FIXABLE = ("This is not fixable by editing dates: pushes and the repository's creation are public records. "
               "Say exactly what existed before the start in the README and the form, ask the organisers in their "
               "public channel, or do not confirm that it was built during the event. The choice is the builder's.")


def record_check(root: Path, cmd: str, bad: bool, tail: list, task=None) -> None:
    """Event checks leave a ledger entry when a plan exists; without one they are still valid, just unrecorded."""
    if not (pb_dir(root) / "plan.json").exists():
        print("(not recorded: no .prod-build/plan.json here)")
        return
    rec = append_evidence(root, {
        "task": task, "kind": "review", "method": "ran", "cmd": cmd[:400],
        "exit": 1 if bad else 0, "expect": "pass", "ok": not bad, "git": git_info(root), "tree": tree_hash(root),
        "where": "local", "tail": tail[-20:]})
    print(f"[{rec['id']}] recorded")


def cmd_window(args, root: Path) -> int:
    ev = load_event(root) or {}
    if event_flags_given(args):
        ev = build_event(args, ev)
    if ev.get("no_window"):
        print(f"no build window recorded for this event ({ev['no_window']}); nothing to check")
        return 0
    if not ev.get("start"):
        die("no event clock: pass --start and --end, or run `pb.py clock set` first", 1)
    start, end = parse_instant(ev["start"]), parse_instant(ev["end"])
    print(f"window {iso_z(start)} -> {iso_z(end)} ({span(end - start)})"
          + (f" | {ev['name']}" if ev.get("name") else "") + (" | PROVISIONAL" if ev.get("provisional") else ""))
    res = window_findings(root, start, end, ev.get("tz_min"), provisional=bool(ev.get("provisional")))
    strict = ev.get("history", "strict") == "strict"
    lines = []
    for i in res["info"]:
        print(f"      {i}")
    for w in res["warns"]:
        lines.append(f"WARN  {w}")
    for f in res["fails"]:
        lines.append(f"{'FAIL ' if strict else 'WARN '} {f}")
    for line in lines:
        print(line)
    if res["fails"] and strict:
        print("      " + NOT_FIXABLE)
    bad = bool(res["fails"]) and strict
    if res.get("unchecked"):
        print(f"window check: NOTHING TO CHECK ({res['unchecked']})")
        return 0
    print(f"window check: {'FAIL' if bad else 'PASS'}"
          + (" (against a provisional start: confirm the clock before relying on this)" if ev.get("provisional") else ""))
    record_check(root, "pb.py " + ARGV_TEXT[0], bad, lines)
    return 1 if bad else 0


def tracked_files(root: Path) -> list:
    out = git(root, "-c", "core.quotepath=off", "ls-files", "-z")
    if out.returncode == 0:
        return [norm_path(f) for f in out.stdout.split("\0") if f.strip()]
    files = []
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not SKIP_DIRS.search(rel + "/") and not rel.startswith(".git/"):
            files.append(rel)
    return files


def find_readme(root: Path, explicit=None):
    """The README a repository page shows: .github/ first, then the root, then docs/ (the host's own order)."""
    if explicit:
        p = Path(explicit)
        p = p if p.is_absolute() else root / p
        return p if p.is_file() else None
    for folder in (root / ".github", root, root / "docs"):
        try:
            names = sorted(p.name for p in folder.iterdir() if p.is_file() and README_NAME.match(p.name))
        except OSError:
            continue
        if names:
            md = [n for n in names if n.lower().endswith((".md", ".markdown", ".mdx"))]
            return folder / (md or names)[0]
    return None


def first_statement(lines: list):
    """The first plain sentence in the opening lines: not a heading, badge, image, link list or table."""
    for line in lines:
        t = line.strip()
        if not t or t.startswith(("#", "|", "<", "```", "---", "===", "[!")) or MD_IMAGE.search(t):
            continue
        t = re.sub(r"^[>\-*+\s]+", "", t)
        plain = MD_LINK.sub(lambda m: m.group(1), t)
        plain = re.sub(r"[*_`~]", "", plain).strip()
        if re.match(r"(?i)^(video|live|demo|repo(sitory)?|docs?|links?|site|try it|watch)\b[^.]{0,30}:", plain):
            continue  # a label and its link, not a sentence
        n = len(re.findall(r"[^\W_][\w'’\-]*", BARE_URL.sub("", plain)))
        if 6 <= n <= 60 and not re.match(r"^\s*(\[[^\]]*\]\([^)]*\)\s*[|·•,]?\s*)+$", t):
            return plain
    return None


def prose_lines(text: str) -> list:
    """[(line number, line with inline code removed)] for lines that are not code."""
    out, fenced, prev_blank = [], False, True
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            prev_blank = False
            continue
        if fenced:
            continue
        indented = bool(re.match(r"^( {4,}|\t)", line)) and not re.match(r"^\s*([-*+]|\d+[.)])\s", line)
        if indented and prev_blank:
            continue  # an indented code block
        prev_blank = not line.strip() or (indented and prev_blank)
        out.append((n, INLINE_CODE.sub(" ", line)))
    return out


def placeholder_findings(text: str, where: str) -> tuple:
    """(fails, warns) for unfinished text in a README or a description."""
    fails, warns, hit = [], [], set()

    def fail(n, what, line):
        if n not in hit:
            hit.add(n)
            fails.append(f"{where}:{n} has {what}: {line.strip()[:90]}")

    lines = prose_lines(text)
    for n, line in lines:
        for m in MD_LINK_FULL.finditer(line):
            label, whole = m.group(1), m.group(2).strip().strip("<>").strip()
            target = whole.split()[0] if whole else ""
            if target in ("", "#"):
                # an empty target is a broken link whatever its text says; "#" is one only on a link a judge would press
                if not label.startswith("!") and (target == "" or LINKISH_TEXT.search(label)):
                    fail(n, "a link with no target", line)
            elif PLACEHOLDER_TARGET.match(whole) or PLACEHOLDER_TARGET.match(target):
                fail(n, "a link whose target is a placeholder", line)
            elif PLACEHOLDER_URL.match(target):
                fail(n, "a link that points at a placeholder address", line)
            elif LOCAL_URL.match(target) and LIVE_TEXT.search(label):
                fail(n, "a live link that only opens on your own machine", line)
        if RX_PROMISED.search(line) or RX_PROMISED_ANY.search(line):
            fail(n, "a link or asset that is promised but not there", line)
        if RX_TOKEN.search(line):
            fail(n, "placeholder text", line)
        if RX_BRACKET.search(line):
            fail(n, "a bracket where a link should be", line)
        if RX_SELFNOTE.search(line):
            fail(n, "a note-to-self line", line)
        m = RX_LABELLED_URL.search(line)
        bare = HTML_TAIL.sub("", m.group(1)) if m else ""
        if bare and PLACEHOLDER_URL.match(bare):
            fail(n, "a link that points at a placeholder address", line)
        elif bare and LOCAL_URL.match(bare) and LIVE_TEXT.search(line):
            fail(n, "a live link that only opens on your own machine", line)
        if n in hit:
            continue
        if EXAMPLE_URL.search(line):
            warns.append(f"{where}:{n} has an example.com address outside a code sample: {line.strip()[:90]}")
        elif SOFT_PLACEHOLDERS.search(line):
            warns.append(f"{where}:{n} reads as unfinished: {line.strip()[:90]}")
    kept = {n for n, _ in lines}
    numbered = text.splitlines()
    prose = "\n".join(l if i + 1 in kept else "" for i, l in enumerate(numbered))
    for m in RX_COMMENT.finditer(prose):
        if RX_COMMENT_NOTE.search(m.group(1)):
            n = prose[: m.start()].count("\n") + 1
            fail(n, "a note-to-self comment", m.group(0).replace("\n", " "))
    return fails, warns


def probe_video(path: Path):
    if not shutil.which("ffprobe"):
        return None
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                            str(path)], capture_output=True, text=True, timeout=120)
        data = json.loads(p.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        return None

    def rate(s):
        try:
            a, b = (s or "0/1").split("/")
            return float(a) / float(b) if float(b) else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0

    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    nominal, avg = rate(v.get("r_frame_rate")), rate(v.get("avg_frame_rate"))
    return {"duration": num((data.get("format") or {}).get("duration")) or num(v.get("duration")),
            "fps": nominal or avg, "avg_fps": avg or nominal,
            "width": int(num(v.get("width"))), "height": int(num(v.get("height"))),
            "audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def submit_findings(root: Path, args, ev: dict) -> dict:
    """Everything a judge opens, checked the way a judge meets it: card, README first screen, links, history,
    video file. FAIL = a judge would see something broken, false or embarrassing."""
    fails, warns, notes, oks, skipped = [], [], [], [], []
    card = int(getattr(args, "card_chars", None) or ev.get("card_chars") or 180)
    sponsors = [str(s) for s in (getattr(args, "sponsor", None) or ev.get("sponsors") or [])]
    if not sponsors:
        skipped.append("sponsor names on the page (none set: `pb.py clock set --sponsor <names>` or --sponsor here)")

    # --- README
    readme = find_readme(root, getattr(args, "readme", None))
    text = ""
    if not readme:
        fails.append("no README: the repository page is the first thing a judge reads after the video")
    else:
        text = readme.read_text(encoding="utf-8", errors="replace")
        try:
            where = readme.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            where = readme.name
        lines = text.splitlines()
        nonempty = [l for l in lines if l.strip()]
        head = nonempty[:40]
        head_text = "\n".join(head)
        stmt = first_statement(nonempty[:14])
        if stmt:
            oks.append(f"README opens with a statement: \"{stmt[:110]}\"")
        else:
            warns.append("README first screen has no plain one-sentence statement of what the product does "
                         "(one sentence a judge could repeat, before any badge wall or architecture)")
        if MD_IMAGE.search(head_text):
            oks.append("README has a screenshot or image in its first screen")
        else:
            warns.append("no screenshot in the README's first 40 lines (organisers ask for one at the top)")
        links = [m.group(2) for m in MD_LINK.finditer(text)] + BARE_URL.findall(text)
        head_links = [m.group(2) for m in MD_LINK.finditer(head_text)] + BARE_URL.findall(head_text)
        want_video, want_live = getattr(args, "video_url", None), getattr(args, "live", None)
        vids = [u for u in links if VIDEO_HOST.search(u)]
        if want_video and want_video.rstrip("/") not in text:
            warns.append(f"README does not link the submitted video ({want_video}); put it in the first screen")
        elif not vids:
            warns.append("README has no demo video link; put it in the first screen, with its length")
        elif not any(VIDEO_HOST.search(u) for u in head_links):
            warns.append("the demo video link is below the README's first screen; move it to the top")
        else:
            oks.append("demo video link is in the README's first screen (not opened from here)")
        live = [u for u in links if u.startswith("http") and not VIDEO_HOST.search(u) and not CODE_HOST.search(u)
                and not PLACEHOLDER_URL.match(u)]
        if want_live and want_live.rstrip("/") not in text:
            fails.append(f"README does not link the submitted live URL ({want_live})")
        elif want_live and want_live.rstrip("/") not in head_text:
            warns.append("the live link is below the README's first screen; move it to the top")
        elif not live:
            warns.append("README has no live link (at one event 78% of the shortlisted 50 had one against 51% of the "
                         "rest; at another 70% of all entries had one and it did not separate winners)")
        elif not any(u in head_links for u in live):
            warns.append("the live link is below the README's first screen; move it to the top")
        else:
            oks.append("live link is in the README's first screen (not opened from here)")
        pf, pw = placeholder_findings(text, where)
        fails += pf
        warns += pw
        words_n = len(re.findall(r"\S+", text))
        if words_n > 3000:
            notes.append(f"README is {words_n} words: a judge has minutes, so the statement, the picture and the "
                         "links have to come before everything else")
        if LIMITS_HEAD.search(text):
            oks.append("README says what is real, simulated or not built")
        else:
            notes.append("README has no section on limits (what is simulated, what is not built, what the data "
                         "cannot see)")
        if AI_TOOLS.search(text):
            oks.append("README names the AI tools used")
        else:
            (warns if ev.get("ai_disclosure") else notes).append(
                "README does not say which AI coding tools were used" +
                (" (this event's rules ask for the list; it also goes in the form)" if ev.get("ai_disclosure")
                 else " (many events ask for the list)"))
        for s in sponsors:
            n = len(re.findall(re.escape(s), text, flags=re.I))
            if n == 0:
                fails.append(f"README never names {s}, which this event requires or rewards")
            elif n < 3 or not re.search(re.escape(s), head_text, flags=re.I):
                warns.append(f"README names {s} {n} time(s)" + ("" if re.search(re.escape(s), head_text, flags=re.I)
                             else " and not in its first screen") + ": say which step it performs, where a judge "
                             "sees it. Counting the name is all this check does; whether it is really used is yours "
                             "to show")
            else:
                oks.append(f"README names {s} {n} times, starting in its first screen (the name, not the use, "
                           "is what was counted)")
        if ev.get("start") and ev.get("history", "strict") == "strict" and not WINDOW_LINE.search(text):
            notes.append("README does not say when it was built: when the history is clean, `pb.py window` prints a "
                         "first-commit line you can state truthfully")

    # --- files that should not be in a judged repository
    if repo_state(root)["kind"] == "none":
        files = []
        skipped.append("files in the repository (this folder is not a git repository yet)")
    else:
        files = tracked_files(root)
    internal = [f for f in files if INTERNAL_PATHS.search(f) and not f.startswith(".prod-build/")]
    drafts = [f for f in files if DRAFT_PATHS.search(f) and not f.startswith(".prod-build/")]
    sessions = [f for f in files if SESSION_FILES.search(f)]
    if sessions:
        fails.append("a browser session file is in the repository: " + ", ".join(sessions[:4]) + ". It holds live "
                     "sign-in cookies: remove it from the repository and from its history, and sign that session out")
    if internal:
        fails.append(f"{len(internal)} internal planning file(s) are in the repository: " + ", ".join(internal[:6])
                     + (" …" if len(internal) > 6 else "") + " (idea packages and judging notes are not for judges; "
                     "take them off the judged branch)")
    elif files:
        oks.append("no idea-package or judging-notes files in the repository")
    if drafts:
        warns.append("filming or submission drafts are in the repository: " + ", ".join(drafts[:6])
                     + " (a judge can open them; check that every sentence in them is true, or take them out)")
    talk = []
    for f in files:
        if not f.lower().endswith((".md", ".mdx", ".txt")) or INTERNAL_PATHS.search(f) or SKIP_DIRS.search(f):
            continue
        p = root / f
        try:
            if p.is_file() and p.stat().st_size < 400_000 and INTERNAL_PHRASES.search(
                    p.read_text(encoding="utf-8", errors="replace")):
                talk.append(f)
        except OSError:
            continue
        if len(talk) >= 5:
            break
    if talk:
        warns.append("these files talk about working the judges: " + ", ".join(talk) + " (read them as a judge would)")
    own = [f for f in files if f.startswith(".prod-build/") and f.lower().endswith((".md", ".json"))]
    if own:
        notes.append(f"the build's own records are in the repository ({len(own)} files under .prod-build/: contract, "
                     "plan, briefs, reports). They are public with it: read the contract's event section as a judge "
                     "would")

    # --- history against the window
    if ev.get("start") and not ev.get("no_window"):
        start, end = parse_instant(ev["start"]), parse_instant(ev["end"])
        res = window_findings(root, start, end, ev.get("tz_min"), provisional=bool(ev.get("provisional")))
        strict = ev.get("history", "strict") == "strict"
        for f in res["fails"]:
            (fails if strict else warns).append("history: " + f + (". " + NOT_FIXABLE if strict and "dated before"
                                                                    in f else ""))
        warns += ["history: " + w for w in res["warns"]]
        rows = res["rows"]
        if not rows:
            skipped.append("history vs build window (" + res["info"][0] + ")")
        elif not res["fails"]:
            oks.append("history: no commit on HEAD is dated before the start"
                       + (" (the start is provisional: confirm the clock first)" if ev.get("provisional") else ""))
        if rows:
            last = max(r[2] for r in rows)
            window = end - start
            late_span = max(_dt.timedelta(hours=2), window * 0.05)
            late = [r for r in rows if end - late_span <= r[2] <= end]
            if end >= last >= end - _dt.timedelta(minutes=30):
                warns.append(f"history: the last commit landed {span(end - last)} before the deadline; whatever a "
                             "judge opens is then the least-tested state (no judge has said this cost a mark)")
            elif late:
                warns.append(f"history: {len(late)} commit(s) in the final {span(late_span)}; check that none "
                             "changed what the video shows")
    elif ev.get("no_window"):
        skipped.append("history vs build window (this event has none: " + str(ev["no_window"])[:80] + ")")
    else:
        skipped.append("history vs build window (no event clock: `pb.py clock set` or --start/--end)")

    # --- the description as the judge queue shows it
    desc = getattr(args, "description", None)
    if desc:
        try:
            p = Path(desc)
            if len(desc) < 400 and "\n" not in desc and p.is_file():
                desc = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
        desc = desc.strip()
        flat = re.sub(r"\s+", " ", desc)
        shown = flat[:card] + ("…" if len(flat) > card else "")
        notes.append(f"judge card as the queue shows it ({card} characters; read it, nothing here judges it): | {shown}")
        first = re.split(r"(?<=[.!?])\s+", flat, maxsplit=1)[0]
        if len(first) > card:
            warns.append(f"description: the first sentence is {len(first)} characters and the card cuts it at {card}")
        if len(re.findall(r"\S+", first)) < 5:
            warns.append("description: the first sentence is a fragment; say what the product is and does in it")
        mx = getattr(args, "desc_max", None)
        if mx and len(desc) > mx:
            fails.append(f"description is {len(desc)} characters; the form allows {mx}")
        for s in sponsors:
            if not re.search(re.escape(s), flat[: max(card, 300)], flags=re.I):
                warns.append(f"description: {s} is not named in the opening lines")
        pf, pw = placeholder_findings(desc, "description")
        fails += pf
        warns += pw
    else:
        skipped.append("submission description (--description <text or file>): the queue card was not checked")

    # --- the video file
    video = getattr(args, "video", None)
    cap = getattr(args, "video_cap", None) or ev.get("video_cap_s")
    if video:
        vp = Path(video)
        info = probe_video(vp) if vp.exists() else None
        if not vp.exists():
            fails.append(f"video file not found: {video}")
        elif info is None:
            skipped.append("video (ffprobe is missing, or the file has no picture it could read)")
        else:
            bad_v = False
            desc_v = f"{info['duration']:.0f} s, {info['width']}x{info['height']}, {info['fps']:.0f} fps"
            if not cap:
                skipped.append("video length limit (none set: `pb.py clock set --video-cap <seconds>` or --video-cap "
                               "here)")
            elif info["duration"] > float(cap):
                fails.append(f"video is {info['duration']:.0f} s; the limit is {cap} s")
                bad_v = True
            elif info["duration"] > float(cap) - 2:
                fails.append(f"video is {info['duration']:.1f} s against a limit of {cap} s: the host can show it a "
                             "second longer than the file, so leave two seconds")
                bad_v = True
            elif float(cap) >= 90 and info["duration"] < 0.85 * float(cap):
                warns.append(f"video is {info['duration']:.0f} s of an allowed {cap} s: when the video is all a judge "
                             "sees, a short one leaves the flow, the sponsor's part or the failure case unshown")
            if info["fps"] and round(info["fps"]) < 24:
                fails.append(f"video is {info['fps']:.0f} fps; deliver 30 (screen motion stutters below 24)")
                bad_v = True
            elif info["fps"] and info["avg_fps"] and info["avg_fps"] < 0.8 * info["fps"]:
                warns.append(f"video has a variable frame rate (nominal {info['fps']:.0f}, average "
                             f"{info['avg_fps']:.0f} fps): re-encode at a constant 30 so motion is even")
            if info["height"] and info["height"] < 720:
                fails.append(f"video is {info['width']}x{info['height']}; deliver at least 1280x720")
                bad_v = True
            if not info["audio"]:
                warns.append("video has no audio track: a silent video makes the judge read every claim")
            if not bad_v:
                oks.append("video file: " + desc_v)
    else:
        skipped.append("video file (--video <file>): length, frame rate and size were not checked")

    # --- links, only when asked: this touches the network
    urls = [u for u in (getattr(args, "live", None), getattr(args, "video_url", None)) if u]
    if getattr(args, "online", False):
        if text:
            seen = [u for u in dict.fromkeys(BARE_URL.findall(text)) if not re.search(r"localhost|127\.0\.0\.1", u)]
            urls += [u.rstrip(".,;") for u in seen[:20] if u.rstrip(".,;") not in urls]
        must = {u for u in (getattr(args, "live", None), getattr(args, "video_url", None)) if u}
        for u in urls:
            st, _, secs, _ = fetch(u, 20)
            if st is None or st >= 400:
                (fails if u in must else warns).append(f"link {u} -> {st or 'no response'}")
            elif secs > 8 and u in must:
                warns.append(f"link {u} took {secs}s: warm it before judging opens")
        oks.append(f"{len(urls)} link(s) requested from here; still open the video, the live URL and the repository "
                   "in a signed-out browser")
    else:
        skipped.append("links (--online): none were opened; check the video, live URL and repository signed out")
    return {"fails": fails, "warns": warns, "notes": notes, "oks": oks, "skipped": skipped}


def cmd_submit(args, root: Path) -> int:
    ev = load_event(root) or {}
    if event_flags_given(args):
        ev = build_event(args, ev)
    res = submit_findings(root, args, ev)
    for o in res["oks"]:
        print(f"ok    {o}")
    for s in res["skipped"]:
        print(f"SKIP  {s}")
    for n in res["notes"]:
        print(f"NOTE  {n}")
    for w in res["warns"]:
        print(f"WARN  {w}")
    for f in res["fails"]:
        print(f"FAIL  {f}")
    bad = bool(res["fails"]) or (args.strict and bool(res["warns"]))
    print(f"submit check: {len(res['fails'])} fail, {len(res['warns'])} warn, {len(res['skipped'])} not checked"
          f" -> {'NOT READY' if bad else 'ready, with the unchecked items still yours'}")
    record_check(root, "pb.py " + ARGV_TEXT[0], bad,
                 [f"FAIL {f}" for f in res["fails"]] + [f"WARN {w}" for w in res["warns"]]
                 + [f"SKIP {s}" for s in res["skipped"]], getattr(args, "task", None))
    return 1 if bad else 0


def event_gate_for_start(root: Path, plan: dict, t: dict, force: bool, note: str) -> tuple:
    """Refuse to start build work outside the event window. Returns (line to print or None, prefix for the
    task's note when the gate was overridden or None)."""
    mode = plan.get("mode")
    ev = load_event(root)
    if not has_clock(ev):
        if mode != "hackathon":
            return None, None
        phase = "no clock"
        why = ("this is a hackathon plan with no event clock. Set it before any work starts: `pb.py clock set "
               "--start <instant> --end <instant> --source <event page>`, or `--no-window '<the rule, quoted>'` where "
               "the event has none. If the event is over and this is now a product, set \"mode\": \"product\" in "
               ".prod-build/plan.json and say so")
    elif ev.get("no_window"):
        return None, None
    else:
        ph = event_phase(ev)
        phase, why = ph["phase"], None
        kind = str(t.get("kind") or "").strip().lower()
        if phase == "before" and ev.get("history", "strict") == "strict":
            why = (f"the build window opens in {span(ph['start'] - ph['now'])} ({iso_z(ph['start'])}). Project work "
                   "starts when the clock does. " + PREP_ALLOWED)
        elif phase == "closed" and mode == "hackathon":
            why = (f"the build window closed {span(ph['now'] - ph['end'])} ago. Nothing built now is part of the "
                   "submission; after the deadline touch only a dead link, and say so. To keep building this as a "
                   "product, set \"mode\": \"product\" in .prod-build/plan.json and stay off the judged branch until "
                   "judging closes")
        elif phase == "closed":
            return (f"NOTE  the event's build window closed {span(ph['now'] - ph['end'])} ago: keep this work off the "
                    "judged branch until judging closes"), None
        elif phase == "freeze" and kind not in FREEZE_KINDS:
            why = (f"{span(ph['end'] - ph['now'])} to the deadline. " + FREEZE_MEANS +
                   f" This task's kind is {kind or 'unset'}; freeze work is one of: {', '.join(FREEZE_KINDS)}")
        if not why:
            if ev.get("provisional") and phase in ("before", "building"):
                return (f"WARN  the event clock is provisional ({ev['provisional']}): re-read the instants from "
                        f"{ev.get('source') or 'the event page'}, then `pb.py clock set --start .. --end .. "
                        "--confirmed`"), None
            return None, None
    if force and note:
        return (f"WARN  EVENT GATE OVERRIDDEN ({phase}): {why}",
                f"EVENT GATE OVERRIDE ({phase}, {iso_z(now_utc())}{', test clock' if fake_now() else ''}): ")
    die(f"{t.get('id')} not started: {why}. Override only with --force --note '<why this is within the rules>'.", 1)


# ----------------------------------------------------------------------------- status

def cmd_status(args, root: Path) -> int:
    plan = read_json(pb_dir(root) / "plan.json")
    if plan is None:
        print("no .prod-build/plan.json here; run `pb.py init --mode <mode>`")
        return 1
    tasks = task_map(plan)
    print(f"{plan.get('project')} | mode {plan.get('mode')} | channel {plan.get('channel')} | base {plan.get('base')}")
    ev = load_event(root)
    if ev or plan.get("mode") == "hackathon":
        print(clock_summary(ev))
    counts = {s: 0 for s in STATUSES}
    for t in tasks.values():
        counts[t.get("status", "todo")] = counts.get(t.get("status", "todo"), 0) + 1
    print("tasks: " + "  ".join(f"{k} {v}" for k, v in counts.items()))
    for t in tasks.values():
        if t.get("status") in ("doing", "blocked"):
            last = str(t.get("notes", "")).strip().splitlines()[-1:] or [""]
            print(f"  {t['id']} [{t['status']}] {t.get('title')}  {last[0][:80]}")
    if not find_cycle(tasks):
        ready = [k for k, t in tasks.items() if t.get("status", "todo") == "todo"
                 and all(tasks.get(d, {}).get("status") in ("done", "skip") for d in (t.get("deps") or []))]
        if ready:
            print("ready: " + ", ".join(sorted(ready)))
    ledger = read_ledger(root)
    fails = [e for e in ledger if not e.get("ok")]
    print(f"evidence: {len(ledger)} entries, {len(fails)} failing" + (f", last {ledger[-1].get('id')}" if ledger else ""))
    for e in ledger[-3:]:
        print(f"  {e.get('id')} {'ok' if e.get('ok') else 'FAIL'} {(e.get('cmd') or e.get('summary') or '')[:80]}")
    chain = verify_chain(ledger)
    if chain:
        print(f"LEDGER PROBLEM: {chain[0]}")
    recs = [r for r in all_records(root, include_archive=False) if r.get("status") == "active"]
    stale = [r.get("id") for r in recs if staleness(root, r) in ("needs-verify", "dead-citation")]
    by = {}
    for r in recs:
        by[r.get("type")] = by.get(r.get("type"), 0) + 1
    print("memory: " + (", ".join(f"{k} {v}" for k, v in by.items()) or "empty") +
          (f" | stale: {', '.join(stale[:8])}" if stale else ""))
    return 0


# ----------------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pb.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--root", help="project root (default: nearest folder with .prod-build/ or .git)")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    def event_flags(sp):
        sp.add_argument("--start", help="build window opens: ISO instant with a zone (2026-03-14T09:00:00Z)")
        sp.add_argument("--end", help="build window closes: ISO instant with a zone")
        sp.add_argument("--freeze", help="stop building at this instant (default: the last 12%% of the window, at "
                        "least 2 h, at most 24 h, never more than a quarter of it)")
        sp.add_argument("--event", help="event name")
        sp.add_argument("--source", help="the event page the instants and rules came from")
        sp.add_argument("--history", choices=("strict", "none"),
                        help="strict (default): nothing may predate the start; none: the rules allow earlier work")
        sp.add_argument("--provisional", help="the instants are not final yet: say why (the page says hours are "
                        "being finalised, or they were worked out from a relative time)")
        sp.add_argument("--confirmed", action="store_true", help="the instants were re-read from the event page; "
                        "clears --provisional")
        sp.add_argument("--no-window", dest="no_window", help="this event has no build window: quote the rule")
        sp.add_argument("--tz", help="offset for local times, e.g. +05:30 or -07:00 (default: the offset --start "
                        "was written in)")
        sp.add_argument("--video-cap", dest="video_cap", type=int, help="video limit in seconds")
        sp.add_argument("--card-chars", dest="card_chars", type=int,
                        help="characters of the description a judge sees before opening the entry (default 180)")
        sp.add_argument("--judge-minutes", dest="judge_minutes", type=float, help="minutes a judge has per project")
        sp.add_argument("--sponsor", nargs="+", help="names the event requires or rewards on the page and in the video")
        sp.add_argument("--ai-disclosure", dest="ai_disclosure", action="store_true",
                        help="the rules require listing the AI tools used")

    s = sub.add_parser("init")
    s.add_argument("--mode", choices=MODES)
    s.add_argument("--channel", choices=CHANNELS, default="direct")
    s.add_argument("--name")
    s.add_argument("--checklists", action="store_true",
                   help="relay: copy the security/verification/deploy/AI checklists into .prod-build/checklists/")
    event_flags(s)

    s = sub.add_parser("clock", help="the event's build window: phase, time left, what is allowed now")
    s.add_argument("action", nargs="?", choices=("show", "set"), default="show")
    event_flags(s)

    s = sub.add_parser("window", help="check the repository's history against the build window")
    event_flags(s)

    s = sub.add_parser("submit", help="check what a judge will open")
    s.add_argument("action", choices=("check",))
    event_flags(s)
    s.add_argument("--readme")
    s.add_argument("--description", help="the submission text, or a file holding it")
    s.add_argument("--desc-max", dest="desc_max", type=int, help="the form's character limit")
    s.add_argument("--video", help="the local video file to be uploaded")
    s.add_argument("--video-url", dest="video_url")
    s.add_argument("--live", help="the live URL being submitted")
    s.add_argument("--online", action="store_true", help="also request the links (uses the network)")
    s.add_argument("--strict", action="store_true", help="treat warnings as failures")
    s.add_argument("--task")

    sub.add_parser("status")

    s = sub.add_parser("plan")
    s.add_argument("action", choices=("check", "waves", "next"))
    s.add_argument("--strict", action="store_true", help="treat warnings as failures")

    s = sub.add_parser("task")
    s.add_argument("id")
    s.add_argument("action", choices=("start", "done", "skip", "block", "reopen"))
    s.add_argument("--note")
    s.add_argument("--evidence", help="comma-separated evidence ids")
    s.add_argument("--force", action="store_true")
    s.add_argument("--strict", action="store_true", help="require evidence for every verify command")

    s = sub.add_parser("run")
    s.add_argument("--task")
    s.add_argument("--kind", choices=KINDS)
    s.add_argument("--where", default="local", choices=("local", "ci", "preview", "prod"))
    s.add_argument("--url")
    s.add_argument("--expect-fail", action="store_true", help="RED proof: the command is expected to fail")
    s.add_argument("--timeout", type=int, default=1800)
    s.add_argument("--tail", type=int, default=30, help="lines of output to print (full log is saved)")
    s.add_argument("command", nargs=argparse.REMAINDER)

    s = sub.add_parser("verify", help="run every verify command of a task through the ledger")
    s.add_argument("id")
    s.add_argument("--where", default="local", choices=("local", "ci", "preview", "prod"))
    s.add_argument("--timeout", type=int, default=1800)
    s.add_argument("--tail", type=int, default=15)
    s.add_argument("--keep-going", action="store_true", help="run the remaining commands after a failure")

    s = sub.add_parser("smoke")
    s.add_argument("url")
    s.add_argument("--routes", nargs="+", default=["/"])
    s.add_argument("--task")
    s.add_argument("--where", default="prod", choices=("local", "ci", "preview", "prod"))
    s.add_argument("--timeout", type=float, default=30)
    s.add_argument("--slow", type=float, default=8.0, help="seconds after which a page counts as slow")
    s.add_argument("--external", action="store_true", help="also check third-party assets")

    s = sub.add_parser("evidence")
    s.add_argument("action", choices=("add", "list", "redact"))
    s.add_argument("--id", help="redact: the evidence id")
    s.add_argument("--pattern", help="redact: regex of the text to remove")
    s.add_argument("--reason", help="redact: why (recorded in an audit entry)")
    s.add_argument("--task")
    s.add_argument("--kind", choices=KINDS)
    s.add_argument("--summary")
    s.add_argument("--method", default="observed", choices=("observed", "reported"))
    s.add_argument("--where", default="local", choices=("local", "ci", "preview", "prod"))
    s.add_argument("--url")
    s.add_argument("--file", action="append", help="screenshot or artifact path (repeatable)")
    s.add_argument("--failed", action="store_true", help="add: the observation found a problem; list: only failures")
    s.add_argument("--limit", type=int, default=40)

    s = sub.add_parser("diffcheck")
    s.add_argument("--base")
    s.add_argument("--task", help="use this task's BASE (recorded by `task start`)")

    s = sub.add_parser("intake")
    s.add_argument("id")
    s.add_argument("--base")

    s = sub.add_parser("mem")
    s.add_argument("action", choices=("add", "find", "check", "index", "mark"))
    s.add_argument("type", nargs="?", help="add: decision|failure|lesson|assumption|feedback|pattern; mark: record id")
    s.add_argument("extra", nargs="?", help="mark: helpful|harmful|verified|superseded|archived|invalidated")
    s.add_argument("--title")
    s.add_argument("--components", default="")
    s.add_argument("--triggers", default="")
    s.add_argument("--evidence", default="", help="path#L1-9, commit:sha, test:cmd, url:..")
    s.add_argument("--relates", default="")
    s.add_argument("--supersedes")
    s.add_argument("--scope", default="project", choices=("project", "global-candidate", "global"))
    s.add_argument("--source")
    s.add_argument("--body", help="record body (use \\n for newlines); default: the type's template")
    s.add_argument("--force", action="store_true")
    s.add_argument("--paths", default="")
    s.add_argument("--terms", default="")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--min-score", type=int, default=2, help="find: drop weaker matches")
    s.add_argument("--id", help="mark: record id")
    s.add_argument("--how", choices=("helpful", "harmful", "verified", "superseded", "archived", "invalidated"))
    s.add_argument("--by", help="mark superseded: the replacing record id")

    s = sub.add_parser("report")
    s.add_argument("file")
    return p


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    for i in range(len(argv) - 1):  # argparse reads "--tz -07:00" as two flags
        if argv[i] == "--tz" and re.match(r"^[+-]\d{2}:?\d{2}$", argv[i + 1]):
            argv[i:i + 2] = [f"--tz={argv[i + 1]}"]
            break
    ARGV_TEXT[0] = " ".join(shlex.quote(a) for a in argv)
    args = build_parser().parse_args(argv)
    root = find_root(args.root)
    if args.cmd == "init":
        args.mode_given = args.mode is not None
        args.mode = args.mode or "hybrid"
    starts_event = (args.cmd == "clock" and args.action == "set") or (
        args.cmd == "init" and (args.mode == "hackathon" and args.mode_given or event_flags_given(args)))
    here = Path.cwd().resolve()
    nearer = None
    if not args.root and root != here:
        nearer = next((p for p in [here, *here.parents] if p != root and (p / ".git").exists()
                       and root in p.parents), None)
    if nearer and starts_event:
        die(f"{nearer} is a repository of its own, but the nearest .prod-build/ is in {root}. An event build is its "
            "own folder and its own repository: pass `--root .` to act on this one, or `--root` with the other path "
            "if that is the project.")
    if nearer:
        print(f"pb: note: acting on {root} (it holds .prod-build/). {nearer} is a repository of its own with no "
              ".prod-build/: pass `--root .` before the command to act on it.", file=sys.stderr)
    if starts_event and not args.root and root != here and not (root / ".prod-build").is_dir():
        die(f"this folder ({here}) has no .prod-build/ or .git of its own, and {root} does. An event build needs "
            "its own folder and its own repository. Pass `--root .` to make this folder the project, or run from "
            f"{root} if that is the project.")
    if args.cmd == "mem" and args.action == "find" and args.type and args.type not in MEM_TYPES:
        die(f"--type for find must be one of {', '.join(MEM_TYPES)}")
    if args.cmd == "mem" and args.action == "mark":
        args.id = args.id or args.type
        args.how = args.how or args.extra
        if not args.id or args.how not in ("helpful", "harmful", "verified", "superseded", "archived", "invalidated"):
            die("usage: pb.py mem mark <ID> helpful|harmful|verified|superseded|archived|invalidated [--by ID]")
    handlers = {"init": cmd_init, "status": cmd_status, "plan": cmd_plan, "task": cmd_task, "run": cmd_run,
                "verify": cmd_verify,
                "smoke": cmd_smoke, "evidence": cmd_evidence, "diffcheck": cmd_diffcheck, "intake": cmd_intake,
                "mem": cmd_mem, "report": cmd_report, "clock": cmd_clock, "window": cmd_window,
                "submit": cmd_submit}
    return handlers[args.cmd](args, root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # output piped into head/less that closed early
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
