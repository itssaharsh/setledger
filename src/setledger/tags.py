"""Tag normalization: messy crate tags -> canonical PRS-ready suggestions via Gemma.

Honest by construction: the model may only REFORMAT, never invent and never lose.
Two-sided guard: every word in a suggestion must already appear in the raw tag, and
every non-noise word in the raw tag must survive into the suggestion. A row that fails
either side, or malformed model output, degrades to passthrough. With no model available
the whole pass is passthrough and callers can show the degraded banner.
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
- Use only words already present in the input. Never invent, guess or DROP name words.
- Clean casing to Title Case; fix spacing.
- A title like "Some Name - Some Words" follows the "Artist - Title" convention: the part
  before the dash is the artist; split it out when the artist field is missing.
- Move remix/edit/version suffixes like "(Extended Mix)" OUT of the title into "mix" (or null).
- Move feat./ft./featuring credits into "featured" (list of names).
- Drop only noise tokens: FINAL, v2, master, 320, unknown, [year], file junk.
- confidence: 0..1, how sure you are this is the canonical artist/title.
Examples:
RAW_ARTIST: dj sable feat. Nia Rao
RAW_TITLE: MIDNIGHT  CIRCUIT (EXTENDED mix) FINAL_v2
JSON: {{"artist": "DJ Sable", "title": "Midnight Circuit", "mix": "Extended Mix", "featured": ["Nia Rao"], "confidence": 0.9}}
RAW_ARTIST: (missing)
RAW_TITLE: Low Orbit Club - velvet signal
JSON: {{"artist": "Low Orbit Club", "title": "Velvet Signal", "mix": null, "featured": [], "confidence": 0.85}}
Now this one:
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

# Tokens the model may drop without losing identity information.
NOISE = {"final", "master", "mix", "extended", "remix", "edit", "version", "radio",
         "feat", "ft", "featuring", "unknown", "wav", "mp3", "flac", "aiff", "og",
         "original", "dirty", "clean", "untagged", "promo", "rip", "www", "com"}


def _tokens(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        out.update(w.lower() for w in _WORD.findall(t or ""))
    return out


def _is_noise(tok: str) -> bool:
    # pure digits (320, 2023) and vN markers are noise; so are known junk words
    return tok in NOISE or tok.isdigit() or re.fullmatch(r"v\d+", tok) is not None


def vocab_guard(raw_artist: str, raw_title: str, parsed: dict) -> bool:
    """Two-sided: no invented words, and no dropped content words.

    produced <= raw           -> the model cannot invent a name.
    raw - noise <= produced   -> the model cannot silently lose part of a name.
    """
    allowed = _tokens(raw_artist, raw_title)
    produced = _tokens(parsed.get("artist", ""), parsed.get("title", ""),
                       parsed.get("mix") or "", *parsed.get("featured", []))
    content = {t for t in allowed if not _is_noise(t)}
    return produced <= allowed and content <= produced


def preclean(text: str) -> str:
    """Deterministic cleanup the model shouldn't have to do: underscores, spacing."""
    return re.sub(r"\s+", " ", (text or "").replace("_", " ")).strip()


PLACEHOLDER_ARTISTS = {"unknown", "unknown artist", "n/a", "various", "various artists", "va"}


def preclean_artist(artist: str) -> str:
    """Placeholder artists become empty so the model must extract the real one
    from the title (or fail the guard) instead of echoing 'Unknown' into a claim."""
    a = preclean(artist)
    return "" if a.lower() in PLACEHOLDER_ARTISTS else a


# Junk that is never a real mix name; a mix made only of these is dropped.
_MIX_JUNK = {"final", "master", "og", "promo", "rip", "clean", "dirty", "untagged"}


def _clean_mix(mix) -> str | None:
    m = str(mix).strip() if mix else None
    if not m:
        return None
    toks = _tokens(m)
    return None if toks and all(t in _MIX_JUNK or t.isdigit()
                                or re.fullmatch(r"v\d+", t) for t in toks) else m


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
            p_artist, p_title = preclean_artist(artist), preclean(title)
            try:
                parsed = _parse_json(client.generate(
                    PROMPT.format(artist=p_artist or "(missing)", title=p_title)))
            except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError):
                parsed = None
            if parsed is not None and vocab_guard(p_artist, p_title, parsed):
                feat = [str(f) for f in parsed.get("featured", []) if str(f).strip()]
                mix = _clean_mix(parsed.get("mix"))
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
