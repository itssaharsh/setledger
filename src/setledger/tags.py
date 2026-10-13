"""Tag normalization: messy crate tags -> canonical PRS-ready suggestions via Gemma.

Honest by construction: the model may only REFORMAT, never invent. Every word in a
suggestion must already appear in the raw tag (vocabulary guard); a suggestion that
fails the guard, or malformed model output, degrades that row to passthrough. With no
model available the whole pass is passthrough and callers can show the degraded banner.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_MODEL = "gemma3:1b"
DEFAULT_URL = "http://localhost:11434"

PROMPT = """You normalize music track tags for royalty filing. Return STRICT JSON only:
{{"artist": "...", "title": "...", "mix": null, "featured": [], "confidence": 0.0}}
Rules:
- Use only words already present in the input. Never invent or guess names.
- Clean casing and spacing for artist and title.
- Move remix/edit/version suffixes like "(Extended Mix)" into "mix" (or null).
- Move feat./ft./featuring credits into "featured" (list of names).
- Drop noise tokens: FINAL, v2, master, 320, [year], file junk.
- confidence: 0..1, how sure you are this is the canonical artist/title.
RAW_ARTIST: {artist}
RAW_TITLE: {title}
JSON:"""


@dataclass
class TagSuggestion:
    file: str
    raw_title: str
    raw_artist: str
    title: str
    artist: str
    mix: str | None
    featured: list[str]
    confidence: float
    method: str          # gemma | passthrough
    changed: bool


class OllamaClient:
    """Minimal ollama HTTP client (stdlib only)."""

    def __init__(self, base_url: str = DEFAULT_URL, model: str = DEFAULT_MODEL,
                 timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/version", timeout=2.0):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def generate(self, prompt: str) -> str:
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False,
                           "options": {"temperature": 0}}).encode()
        req = urllib.request.Request(f"{self.base_url}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())["response"]


_WORD = re.compile(r"[A-Za-z0-9]+")


def _tokens(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        out.update(w.lower() for w in _WORD.findall(t or ""))
    return out


def vocab_guard(raw_artist: str, raw_title: str, parsed: dict) -> bool:
    """True iff every output word already appears in the raw tag (no inventions)."""
    allowed = _tokens(raw_artist, raw_title)
    produced = _tokens(parsed.get("artist", ""), parsed.get("title", ""),
                       parsed.get("mix") or "", *parsed.get("featured", []))
    return produced <= allowed


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("artist"), str) \
            or not isinstance(data.get("title"), str):
        return None
    if not data["artist"].strip() or not data["title"].strip():
        return None
    return data


def _passthrough(file: str, title: str, artist: str) -> TagSuggestion:
    return TagSuggestion(file, title, artist, title.strip(), artist.strip(),
                         None, [], 0.0, "passthrough", False)


def normalize_tags(rows, client: OllamaClient | None = None,
                   progress=None) -> list[TagSuggestion]:
    """rows: objects or dicts with file/title/artist. No client (or unreachable) -> passthrough."""
    live = client is not None and client.available()
    out: list[TagSuggestion] = []
    for i, r in enumerate(rows):
        g = (lambda k: r[k]) if isinstance(r, dict) else (lambda k: getattr(r, k))
        file, title, artist = g("file"), g("title"), g("artist")
        sug = _passthrough(file, title, artist)
        if live:
            try:
                parsed = _parse_json(client.generate(
                    PROMPT.format(artist=artist, title=title)))
            except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError):
                parsed = None
            if parsed is not None and vocab_guard(artist, title, parsed):
                feat = [str(f) for f in parsed.get("featured", []) if str(f).strip()]
                mix = parsed.get("mix")
                mix = str(mix).strip() if mix else None
                try:
                    conf = min(1.0, max(0.0, float(parsed.get("confidence", 0.5))))
                except (TypeError, ValueError):
                    conf = 0.5
                new_t, new_a = parsed["title"].strip(), parsed["artist"].strip()
                sug = TagSuggestion(file, title, artist, new_t, new_a, mix, feat, conf,
                                    "gemma", (new_t, new_a, mix, feat) != (title, artist, None, []))
        out.append(sug)
        if progress:
            progress(i + 1, len(rows), file)
    return out


def before_after_table(suggestions: list[TagSuggestion]) -> str:
    """Markdown before/after artifact for the post and the UI."""
    lines = ["| file | raw | suggestion | conf | method |",
             "|---|---|---|---|---|"]
    for s in suggestions:
        raw = f"{s.raw_artist} — {s.raw_title}"
        new = f"{s.artist} — {s.title}"
        if s.featured:
            new += f" (feat. {', '.join(s.featured)})"
        if s.mix:
            new += f" [{s.mix}]"
        mark = new if s.changed else "(unchanged)"
        lines.append(f"| {s.file} | {raw} | {mark} | {s.confidence:.2f} | {s.method} |")
    return "\n".join(lines)
