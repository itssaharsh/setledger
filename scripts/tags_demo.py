"""Tag-normalization demo: mess up the crate's tags the way real DJ exports are messy,
run Gemma normalization (or passthrough when no model), write the before/after artifact.

All tags are synthetic and labeled so; the mess transforms only ADD noise tokens, so the
vocabulary guard can always recover the canonical words from the raw tag.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np

from setledger.tags import OllamaClient, OpenAICompatClient, before_after_table, normalize_tags

ROOT = Path(__file__).parent.parent
CRATE = ROOT / "data" / "crate"
OUT = ROOT / "docs" / "media" / "tags-before-after.md"


def mess(title: str, artist: str, rng) -> tuple[str, str]:
    """Deterministic 'exported from a DJ library' mess: casing, noise, feat., mix tags."""
    style = int(rng.integers(0, 5))
    if style == 0:
        return title.upper().replace(" ", "_") + "_FINAL_v2", artist.lower()
    if style == 1:
        return f"{title} (extended MIX) [320]", artist.upper()
    if style == 2:
        first = artist.split()[0]
        rest = " ".join(artist.split()[1:]) or first
        return f"{title}   master", f"{first} feat. {rest}"
    if style == 3:
        return f"{artist} - {title.lower()}", "unknown"  # the "Artist - Title" filename convention
    return title, artist  # already clean


def crate_rows() -> list[dict]:
    from setledger.trackgen import ADJ, ARTISTS, NOUN
    rng = np.random.default_rng(42)
    rows = []
    for i, p in enumerate(sorted(CRATE.glob("*.wav"))):
        title = f"{ADJ[i % 10]} {NOUN[(i * 3 + i // 10) % 10]}"
        artist = ARTISTS[i % len(ARTISTS)]
        m_title, m_artist = mess(title, artist, rng)
        rows.append({"file": p.name, "title": m_title, "artist": m_artist})
    return rows


def pick_client():
    """DigitalOcean Serverless Inference (Gemma) when a model key is saved, else local ollama.
    The DO model id is picked from the endpoint's own /models list, never guessed."""
    keyf = Path.home() / ".config" / "setledger" / "do_model_key"
    if keyf.exists() and keyf.read_text().strip():
        import json, urllib.request
        key = keyf.read_text().strip()
        base = "https://inference.do-ai.run/v1"
        want = os.environ.get("SETLEDGER_DO_MODEL")
        if not want:
            req = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
            ids = sorted(m["id"] for m in json.loads(urllib.request.urlopen(req, timeout=20).read())["data"])
            gemmas = [i for i in ids if "gemma" in i.lower()]
            print("DO models with 'gemma':", gemmas or "none")
            want = gemmas[-1] if gemmas else None
        if want:
            c = OpenAICompatClient(base, want, key)
            if c.available():
                return c, f"{want} via DigitalOcean Serverless Inference"
    c = OllamaClient()
    return c, "gemma3:1b via local ollama"


def main() -> int:
    rows = crate_rows()
    client, label = pick_client()
    live = client.available()
    print(f"model: {label if live else 'UNAVAILABLE -> passthrough'}")
    sugg = normalize_tags(rows, client=client if live else None,
                          progress=lambda i, n, f: print(f"  {i}/{n} {f}"))
    changed = sum(s.changed for s in sugg)
    guarded = sum(s.method == "passthrough" for s in sugg) if live else 0
    md = ["# Tag normalization — before/after (synthetic demo tags)", "",
          f"Model: {label if live else 'unavailable — passthrough mode'}. "
          f"{changed}/{len(sugg)} tags normalized"
          + (f"; {guarded} rows degraded to passthrough by the vocabulary guard." if live else "."),
          "", before_after_table(sugg), "",
          "_Guard (two-sided): every word in a suggestion must already appear in the raw tag, "
          "and every non-noise word in the raw tag must survive into the suggestion — "
          "the model may reformat, never invent and never lose._"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(md))
    print(f"changed {changed}/{len(sugg)}  -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
