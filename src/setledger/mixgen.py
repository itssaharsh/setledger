"""Synthetic DJ-set builder with ground truth.

Takes a crate of WAV tracks, renders a continuous 'set' with crossfades, optional
tempo-shifted plays (ffmpeg atempo) and one foreign track not in the crate, and writes
a truth JSON with exact segment boundaries — the benchmark's ground truth.
"""
from __future__ import annotations

import json
import subprocess
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

SR = 44100


@dataclass
class TruthSegment:
    track_file: str | None  # None => foreign (not in crate)
    t_start: float
    t_end: float
    tempo_shift: float  # 1.0 = unshifted


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR, f"{path}: expected {SR} Hz"
        frames = w.readframes(w.getnframes())
        pcm = np.frombuffer(frames, dtype=np.int16).reshape(-1, w.getnchannels())
    return pcm.astype(np.float32) / 32767.0


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(audio.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def tempo_shift(path_in: Path, factor: float, tmp_dir: Path) -> np.ndarray:
    """Shift tempo (duration changes, pitch preserved) via ffmpeg atempo."""
    out = tmp_dir / f"shift_{factor}_{path_in.stem}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path_in),
         "-filter:a", f"atempo={factor}", "-ar", str(SR), str(out)],
        check=True,
    )
    return read_wav(out)


def build_set(
    crate_dir: Path,
    out_wav: Path,
    out_truth: Path,
    plays: list[dict],
    crossfade_s: float = 3.0,
    seed: int = 7,
) -> list[TruthSegment]:
    """plays: [{file: name|None(foreign), seg_s: float, shift: float}]; returns truth."""
    rng = np.random.default_rng(seed)
    tmp = out_wav.parent / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    xf = int(crossfade_s * SR)

    segments: list[np.ndarray] = []
    meta: list[tuple[str | None, float]] = []
    for p in plays:
        if p["file"] is None:
            from .trackgen import TrackSpec, render
            foreign = render(TrackSpec(seed=999999, title="Foreign", artist="X", bpm=126, key="A"), bars=16)
            audio = foreign
            name = None
        else:
            path = crate_dir / p["file"]
            audio = read_wav(path) if p["shift"] == 1.0 else tempo_shift(path, p["shift"], tmp)
            name = p["file"]
        seg_len = int(p["seg_s"] * SR)
        start_max = max(0, len(audio) - seg_len - 1)
        s0 = int(rng.integers(0, start_max + 1))
        segments.append(audio[s0 : s0 + seg_len])
        meta.append((name, p["shift"]))

    total = sum(len(s) for s in segments) - xf * (len(segments) - 1)
    mix = np.zeros((total, 2), dtype=np.float32)
    truth: list[TruthSegment] = []
    cursor = 0
    for i, seg in enumerate(segments):
        seg = seg.copy()
        if i > 0:
            seg[:xf] *= np.linspace(0, 1, xf)[:, None]
        if i < len(segments) - 1:
            seg[-xf:] *= np.linspace(1, 0, xf)[:, None]
        mix[cursor : cursor + len(seg)] += seg
        name, shift = meta[i]
        truth.append(TruthSegment(
            track_file=name,
            t_start=round(cursor / SR, 3),
            t_end=round((cursor + len(seg)) / SR, 3),
            tempo_shift=shift,
        ))
        cursor += len(seg) - xf

    write_wav(out_wav, mix)
    out_truth.write_text(json.dumps([asdict(t) for t in truth], indent=1))
    return truth


def standard_demo_plays(crate_files: list[str], rng_seed: int = 11) -> list[dict]:
    """8 plays: 5 clean, 2 tempo-shifted (+6%, -5%), 1 foreign."""
    rng = np.random.default_rng(rng_seed)
    picks = list(rng.choice(crate_files, size=7, replace=False))
    plays = [{"file": f, "seg_s": float(rng.integers(55, 80)), "shift": 1.0} for f in picks[:5]]
    plays.append({"file": picks[5], "seg_s": 65.0, "shift": 1.06})
    plays.append({"file": picks[6], "seg_s": 60.0, "shift": 0.95})
    plays.insert(int(rng.integers(1, 7)), {"file": None, "seg_s": 50.0, "shift": 1.0})
    return plays
