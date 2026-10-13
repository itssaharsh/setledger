"""Chromaprint raw fingerprints via fpcalc.

A raw chromaprint is a sequence of 32-bit ints, one per ~0.1238 s of audio.
Matching compares subsequences by Hamming distance (XOR + popcount), which is what
makes exact plays findable at any offset inside a long set recording.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

FP_RATE = 8.071  # chromaprint items per second (~0.1238 s per item)


class FpcalcMissing(RuntimeError):
    pass


def ensure_fpcalc() -> str:
    exe = shutil.which("fpcalc")
    if not exe:
        raise FpcalcMissing(
            "fpcalc not found. Install chromaprint (e.g. static binary from "
            "https://acoustid.org/chromaprint) and put it on PATH."
        )
    return exe


def raw_fingerprint(path: Path, max_seconds: int = 7200) -> tuple[np.ndarray, float]:
    """Return (uint32 fingerprint array, duration_seconds). Raises on undecodable audio."""
    exe = ensure_fpcalc()
    proc = subprocess.run(
        [exe, "-raw", "-json", "-length", str(max_seconds), str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ValueError(f"fpcalc failed on {path.name}: {proc.stderr.strip()[:200]}")
    data = json.loads(proc.stdout)
    fp = np.array(data["fingerprint"], dtype=np.uint64).astype(np.uint32)
    return fp, float(data["duration"])


_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def popcount32(x: np.ndarray) -> np.ndarray:
    """Vectorized popcount for uint32 arrays."""
    b = x.view(np.uint8).reshape(-1, 4) if x.flags.c_contiguous else x.copy().view(np.uint8).reshape(-1, 4)
    return _POPCOUNT[b].sum(axis=1).astype(np.uint16)


def bit_error_rate(a: np.ndarray, b: np.ndarray) -> float:
    """Mean fraction of differing bits between two equal-length uint32 sequences."""
    n = min(len(a), len(b))
    if n == 0:
        return 1.0
    xor = np.bitwise_xor(a[:n], b[:n])
    return float(popcount32(xor).sum()) / (32.0 * n)
