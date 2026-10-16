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
INVENTED = json.dumps({"artist": "DJ Sable", "title": "Midnight Circuit (Dub Cut)",
                       "mix": None, "featured": [], "confidence": 0.9})
DROPPED = json.dumps({"artist": "Mara", "title": "Glasshouse",   # lost the "V"
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


def test_vocab_guard_blocks_dropped_words():
    # the model lost the "V" in "Mara V" -> that row degrades to passthrough
    out = normalize_tags(ROWS[1:], client=FakeClient([DROPPED]))
    assert out[0].method == "passthrough"
    assert not vocab_guard(ROWS[1]["artist"], ROWS[1]["title"], json.loads(DROPPED))


def test_preclean():
    from setledger.tags import preclean
    assert preclean("MIDNIGHT_HARBOUR_FINAL_v2") == "MIDNIGHT HARBOUR FINAL v2"
    assert preclean("  a   b ") == "a b"


def test_placeholder_artist_forces_extraction():
    from setledger.tags import preclean_artist
    assert preclean_artist("unknown") == "" and preclean_artist("Mara V") == "Mara V"
    row = {"file": "c.wav", "title": "Delta Verma - hollow tide", "artist": "unknown"}
    good = json.dumps({"artist": "Delta Verma", "title": "Hollow Tide",
                       "mix": None, "featured": [], "confidence": 0.8})
    echo = json.dumps({"artist": "Unknown", "title": "Delta Verma - Hollow Tide",
                       "mix": None, "featured": [], "confidence": 0.7})
    assert normalize_tags([row], client=FakeClient([good]))[0].artist == "Delta Verma"
    # echoing 'Unknown' is now an invention (placeholder was blanked) -> passthrough
    assert normalize_tags([row], client=FakeClient([echo]))[0].method == "passthrough"


def test_junk_mix_is_dropped():
    from setledger.tags import _clean_mix
    assert _clean_mix("Final v2") is None and _clean_mix("320") is None
    assert _clean_mix("Extended Mix") == "Extended Mix" and _clean_mix(None) is None


def test_malformed_output_is_passthrough():
    out = normalize_tags(ROWS[:1], client=FakeClient(["sorry, I cannot do JSON"]))
    assert out[0].method == "passthrough"


def test_before_after_table():
    out = normalize_tags(ROWS, client=FakeClient([GOOD, UNCHANGED]))
    md = before_after_table(out)
    assert "Midnight Circuit" in md and "feat. Nia Rao" in md
    assert "(unchanged)" in md
    assert md.count("|", 0, md.index("\n")) == 6  # 5 columns


def test_openai_compat_client_against_fake_server():
    """Wire format for DO Serverless Inference: bearer auth, /models gate, chat completions."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from setledger.tags import OpenAICompatClient
    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen["auth"] = self.headers.get("Authorization")
            self._send({"data": [{"id": "gemma-test"}]})

        def do_POST(self):
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._send({"choices": [{"message": {"content": GOOD}}]})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        c = OpenAICompatClient(base, "gemma-test", "k-123")
        assert c.available() and seen["auth"] == "Bearer k-123"
        assert not OpenAICompatClient(base, "guessed-model-id", "k-123").available()
        out = normalize_tags(ROWS[:1], client=c)
        assert out[0].method == "gemma" and out[0].title == "Midnight Circuit"
        assert seen["body"]["model"] == "gemma-test" and seen["body"]["temperature"] == 0
    finally:
        srv.shutdown()
