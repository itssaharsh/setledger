"""Procedural track synthesis: original, builder-owned audio for the demo crate.

Each track is a seeded, deterministic render: tempo, key, drum pattern, bass line,
chord voicing and lead timbre all derive from the seed, so two seeds sound like two
different productions. Output: 44.1 kHz stereo WAV, ~72 s (32 bars at the track tempo).
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 44100

NOTE_FREQS = {n: 440.0 * 2 ** ((i - 9) / 12) for i, n in enumerate(
    ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}
MINOR = [0, 2, 3, 5, 7, 8, 10]


@dataclass
class TrackSpec:
    seed: int
    title: str
    artist: str
    bpm: float
    key: str


def _env(n: int, a: float, r: float) -> np.ndarray:
    e = np.ones(n)
    ai, ri = int(a * SR), int(r * SR)
    if ai > 0:
        e[:ai] = np.linspace(0, 1, ai)
    if ri > 0:
        e[-ri:] = np.linspace(1, 0, ri)
    return e


def _tone(freq: float, dur: float, kind: str, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    if kind == "saw":
        x = 2 * (t * freq % 1) - 1
    elif kind == "square":
        x = np.sign(np.sin(2 * np.pi * freq * t))
    elif kind == "fm":
        x = np.sin(2 * np.pi * freq * t + 3.0 * np.sin(2 * np.pi * freq * 2.01 * t))
    else:  # additive
        x = sum(np.sin(2 * np.pi * freq * k * t) / k for k in (1, 2, 3, 4))
    return x * _env(len(t), 0.005, min(0.05, dur / 4))


def _kick(dur: float = 0.25) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    f = 120 * np.exp(-t * 18) + 45
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def _hat(rng: np.random.Generator, dur: float = 0.06) -> np.ndarray:
    n = int(dur * SR)
    return rng.standard_normal(n) * np.exp(-np.arange(n) / (0.01 * SR)) * 0.4


def _snare(rng: np.random.Generator, dur: float = 0.15) -> np.ndarray:
    n = int(dur * SR)
    noise = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.03 * SR))
    body = np.sin(2 * np.pi * 180 * np.arange(n) / SR) * np.exp(-np.arange(n) / (0.02 * SR))
    return 0.6 * noise + 0.5 * body


def render(spec: TrackSpec, bars: int = 32) -> np.ndarray:
    rng = np.random.default_rng(spec.seed)
    beat = 60.0 / spec.bpm
    bar = 4 * beat
    total = int(bars * bar * SR)
    mix = np.zeros(total)

    root = NOTE_FREQS[spec.key] / 4  # bass register
    scale = [root * 2 ** (s / 12) for s in MINOR]
    prog = rng.choice(len(MINOR), size=4, replace=True)
    lead_kind = rng.choice(["saw", "square", "fm", "add"])
    hat_prob = rng.uniform(0.5, 0.95)
    snare_beats = [1, 3] if rng.random() < 0.8 else [2]
    bass_pattern = rng.integers(0, 2, size=8)
    melody_steps = rng.choice(len(MINOR), size=16)
    melody_gate = rng.random(16) < rng.uniform(0.4, 0.8)

    def add(sig: np.ndarray, at: float, gain: float) -> None:
        i = int(at * SR)
        j = min(total, i + len(sig))
        if i < total:
            mix[i:j] += sig[: j - i] * gain

    for b in range(bars):
        t0 = b * bar
        chord_root = scale[prog[b % 4]]
        for q in range(4):
            add(_kick(), t0 + q * beat, 0.9)
            if q in snare_beats:
                add(_snare(rng), t0 + q * beat, 0.5)
            for h in range(2):
                if rng.random() < hat_prob:
                    add(_hat(rng), t0 + q * beat + h * beat / 2, 0.35)
        for e in range(8):
            if bass_pattern[e]:
                add(_tone(chord_root, beat / 2 * 0.9, "saw", rng), t0 + e * beat / 2, 0.35)
        if b >= 4:  # melody enters after intro
            for s in range(16):
                if melody_gate[s]:
                    f = scale[melody_steps[s]] * 4
                    add(_tone(f, beat / 4 * 0.9, lead_kind, rng), t0 + s * beat / 4, 0.18)
        # pad
        add(_tone(chord_root * 2, bar, "add", rng), t0, 0.08)

    peak = np.max(np.abs(mix)) or 1.0
    mono = (mix / peak * 0.85).astype(np.float32)
    return np.stack([mono, mono], axis=1)


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


ADJ = ["Midnight", "Velvet", "Static", "Neon", "Hollow", "Amber", "Paper", "Glass", "Iron", "Wild"]
NOUN = ["Harbour", "Circuit", "Meridian", "Signal", "Tide", "Fracture", "Lantern", "Echo", "Motor", "Garden"]
ARTISTS = ["Kolkata Transit", "Low Orbit Club", "Analog Debt", "Night Shift Audio", "Delta Verma"]


def make_crate(out_dir: Path, n: int = 25, seed0: int = 1000) -> list[TrackSpec]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed0)
    specs = []
    for i in range(n):
        seed = seed0 + i
        spec = TrackSpec(
            seed=seed,
            title=f"{ADJ[i % 10]} {NOUN[(i * 3 + i // 10) % 10]}",
            artist=ARTISTS[i % len(ARTISTS)],
            bpm=float(rng.integers(118, 132)),
            key=list(NOTE_FREQS)[int(rng.integers(0, 12))],
        )
        path = out_dir / f"{spec.seed}_{spec.title.replace(' ', '_')}.wav"
        if not path.exists():
            write_wav(path, render(spec))
        specs.append(spec)
    return specs
