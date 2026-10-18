#!/usr/bin/env python3
"""Assemble the static replay demo in site/: the real UI files, the replay shim, and the
recording made by scripts/record_replay.py. Usage: python scripts/build_static.py [repo_url]"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SITE = ROOT / "site"
REPO = sys.argv[1] if len(sys.argv) > 1 else ""


def main() -> int:
    if not (SITE / "replay" / "run.json").exists():
        print("record first: scripts/record_replay.py")
        return 1
    static = SITE / "static"
    static.mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "app.js"):
        shutil.copy(ROOT / "src" / "setledger" / "static" / name, static / name)
    shutil.copy(ROOT / "deploy" / "static_replay" / "replay.js", static / "replay.js")
    with (static / "style.css").open("a") as f:
        f.write("\n#replay-chip{display:inline-block;margin-left:12px;padding:2px 8px;font-size:12px;"
                "border:1px solid var(--line-input);border-radius:var(--r-sm);color:var(--warning);"
                "text-decoration:none;font-family:var(--ff-mono)}\n"
                "@media (max-width:760px){#replay-chip{margin:6px 0 0}}\n")
    html = (ROOT / "src" / "setledger" / "static" / "index.html").read_text()
    tag = '<script src="/static/app.js"></script>'
    assert tag in html
    shim = (f"<script>window.SETLEDGER_REPO = {json.dumps(REPO)};</script>\n"
            '<script src="/static/replay.js"></script>\n')
    (SITE / "index.html").write_text(html.replace(tag, shim + tag))
    (SITE / "vercel.json").write_text(json.dumps({"cleanUrls": True, "trailingSlash": False}))
    n = sum(1 for p in SITE.rglob("*") if p.is_file())
    print(f"site/ ready: {n} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
