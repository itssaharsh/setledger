import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from setledger.tags import (TagSuggestion, before_after_table, normalize_tags,
                            vocab_guard)

ROWS = [
    {"file": "a.wav", "title": "MIDNIGHT  circuit (EXTENDED mix) FINAL_v2",
     "artist": "dj sable feat. Nia Rao"},
    {"file": "b.wav", "title": "Glasshouse", "artist": "Mara V"},
]


class FakeClient:
    """Stands in for OllamaClient; scripted responses, no server."""

    def __init__(self, responses, up=True):
        self.responses = list(responses)
        self.up = up

    def available(self):
        return self.up

    def generate(self, prompt):
        return self.responses.pop(0)


GOOD = json.dumps({"artist": "DJ Sable", "title": "Midnight Circuit",
                   "mix": "Extended Mix", "featured": ["Nia Rao"], "confidence": 0.9})
UNCHANGED = json.dumps({"artist": "Mara V", "title": "Glasshouse",
                        "mix": None, "featured": [], "confidence": 0.95})
INVENTED = json.dumps({"artist": "DJ Sable", "title": "Midnight Circuit (Radio Edit)",
                       "mix": None, "featured": [], "confidence": 0.9})


def test_no_client_is_passthrough():
    out = normalize_tags(ROWS, client=None)
    assert [s.method for s in out] == ["passthrough", "passthrough"]
    assert out[0].title == ROWS[0]["title"] and not out[0].changed


def test_unreachable_client_is_passthrough():
    out = normalize_tags(ROWS, client=FakeClient([], up=False))
    assert all(s.method == "passthrough" for s in out)


def test_gemma_normalizes_messy_tag():
    out = normalize_tags(ROWS, client=FakeClient([GOOD, UNCHANGED]))
    s = out[0]
    assert (s.method, s.changed) == ("gemma", True)
    assert s.title == "Midnight Circuit" and s.artist == "DJ Sable"
    assert s.mix == "Extended Mix" and s.featured == ["Nia Rao"]
    assert s.confidence == 0.9
    assert out[1].method == "gemma" and not out[1].changed


def test_vocab_guard_blocks_invented_words():
    # "Radio Edit" never appeared in the raw tag -> that row degrades to passthrough
    out = normalize_tags(ROWS, client=FakeClient([INVENTED, UNCHANGED]))
    assert out[0].method == "passthrough"
    assert out[1].method == "gemma"
    assert not vocab_guard(ROWS[0]["artist"], ROWS[0]["title"], json.loads(INVENTED))


def test_malformed_output_is_passthrough():
    out = normalize_tags(ROWS[:1], client=FakeClient(["sorry, I cannot do JSON"]))
    assert out[0].method == "passthrough"


def test_before_after_table():
    out = normalize_tags(ROWS, client=FakeClient([GOOD, UNCHANGED]))
    md = before_after_table(out)
    assert "Midnight Circuit" in md and "feat. Nia Rao" in md
    assert "(unchanged)" in md
    assert md.count("|", 0, md.index("\n")) == 6  # 5 columns
