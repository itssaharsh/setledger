"""Tag-normalization demo: mess up the crate's tags the way real DJ exports are messy,
run Gemma normalization (or passthrough when no model), write the before/after artifact.

All tags are synthetic and labeled so; the mess transforms only ADD noise tokens, so the
vocabulary guard can always recover the canonical words from the raw tag.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np

from setledger.tags import OllamaClient, before_after_table, normalize_tags

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
        return f"{title.lower()} - {artist}", "unknown"
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


def main() -> int:
    rows = crate_rows()
    client = OllamaClient()
    live = client.available()
    print(f"model: {'gemma3:1b via ollama' if live else 'UNAVAILABLE -> passthrough'}")
    sugg = normalize_tags(rows, client=client if live else None,
                          progress=lambda i, n, f: print(f"  {i}/{n} {f}"))
    changed = sum(s.changed for s in sugg)
    guarded = sum(s.method == "passthrough" for s in sugg) if live else 0
    md = ["# Tag normalization — before/after (synthetic demo tags)", "",
          f"Model: {'Gemma 3 1B (ollama, local CPU)' if live else 'unavailable — passthrough mode'}. "
          f"{changed}/{len(sugg)} tags normalized"
          + (f"; {guarded} rows degraded to passthrough by the vocabulary guard." if live else "."),
          "", before_after_table(sugg), "",
          "_Guard: every word in a suggestion must already appear in the raw tag — "
          "the model may reformat, never invent._"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(md))
    print(f"changed {changed}/{len(sugg)}  -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
