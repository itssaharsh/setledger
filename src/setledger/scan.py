"""Set scanning: find which crate tracks play where in a set recording.

Exact layer: chromaprint raw fingerprints compared at every alignment lag between the
set and each crate track (XOR + popcount). A play is a contiguous run of low bit-error
alignment. Segments nothing matches become status='review' — the scanner never invents
a track, by construction: candidates can only come from the crate.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .fingerprint import FP_RATE, popcount32, raw_fingerprint
from .store import TrackRow

MATCH_BER = 0.28          # max mean bit-error rate for an exact match
RUN_BER = 0.32            # per-position threshold when trimming the matched run
MIN_OVERLAP_S = 12.0      # shortest overlap considered
MIN_PLAY_S = 15.0         # shortest reported play
GAP_REVIEW_S = 10.0       # uncovered gaps at least this long become review segments


@dataclass
class Play:
    track_id: int | None
    track_file: str | None
    t_start: float
    t_end: float
    confidence: float
    method: str           # exact | embedding | none
    status: str           # matched | review


def _smooth(x: np.ndarray, w: int) -> np.ndarray:
    if len(x) < w:
        return x.astype(np.float32)
    k = np.ones(w, dtype=np.float32) / w
    return np.convolve(x.astype(np.float32), k, mode="same")


def best_alignment(set_fp: np.ndarray, track_fp: np.ndarray,
                   min_overlap: int, lag_stride: int = 2):
    """Scan alignment lags; return (lag, mean_ber, per_item_ber) of the best lag or None."""
    Ls, Lt = len(set_fp), len(track_fp)
    # Loop-based music self-matches at bar-multiple offsets, so the lowest-error lag can be
    # a short self-similar alignment, and the longest raw overlap dilutes error over
    # non-play audio. The right objective is the longest LOW-ERROR RUN: collect candidate
    # lags, then pick the one whose trimmed run (what we would actually claim) is longest.
    cands: list[tuple[float, int, int]] = []  # (ber, lag, n)
    best = None
    for lag in range(-(Lt - min_overlap), Ls - min_overlap, lag_stride):
        s0, t0 = max(0, lag), max(0, -lag)
        n = min(Ls - s0, Lt - t0)
        if n < min_overlap:
            continue
        xor = np.bitwise_xor(set_fp[s0:s0 + n], track_fp[t0:t0 + n])
        ber = float(popcount32(xor).sum()) / (32.0 * n)
        if best is None or ber < best[1]:
            best = (lag, ber)
        if ber <= MATCH_BER * 1.5:
            cands.append((ber, lag, n))
    if best is None:
        return None
    if cands:
        cands.sort()
        best_run = None  # (run_len, -run_ber, lag)
        for ber, lag, n in cands[:12]:
            s0, t0 = max(0, lag), max(0, -lag)
            m = min(Ls - s0, Lt - t0)
            per_c = popcount32(np.bitwise_xor(set_fp[s0:s0 + m], track_fp[t0:t0 + m])).astype(np.float32) / 32.0
            run = _run_bounds(per_c, RUN_BER)
            if run is None:
                continue
            run_len = run[1] - run[0]
            run_ber = float(per_c[run[0]:run[1]].mean())
            key = (run_len, -run_ber)
            if best_run is None or key > best_run[0]:
                best_run = (key, lag, ber)
        if best_run is not None:
            best = (best_run[1], best_run[2])
    # refine ±(stride-1) around the best lag
    lag0 = best[0]
    for lag in range(lag0 - lag_stride + 1, lag0 + lag_stride):
        s0, t0 = max(0, lag), max(0, -lag)
        n = min(Ls - s0, Lt - t0)
        if n < min_overlap:
            continue
        xor = np.bitwise_xor(set_fp[s0:s0 + n], track_fp[t0:t0 + n])
        ber = float(popcount32(xor).sum()) / (32.0 * n)
        if ber <= best[1]:
            best = (lag, ber)
    lag, _ = best
    s0, t0 = max(0, lag), max(0, -lag)
    n = min(Ls - s0, Lt - t0)
    per = popcount32(np.bitwise_xor(set_fp[s0:s0 + n], track_fp[t0:t0 + n])).astype(np.float32) / 32.0
    return lag, best[1], per


def _run_bounds(per_ber: np.ndarray, thr: float) -> tuple[int, int] | None:
    """Longest contiguous run where smoothed per-item BER stays below thr."""
    ok = _smooth(per_ber, 8) < thr
    best_len, best = 0, None
    i = 0
    while i < len(ok):
        if ok[i]:
            j = i
            while j < len(ok) and ok[j]:
                j += 1
            if j - i > best_len:
                best_len, best = j - i, (i, j)
            i = j
        else:
            i += 1
    return best


def scan_exact(set_path: Path, tracks: list[TrackRow], progress=None) -> list[Play]:
    set_fp, set_dur = raw_fingerprint(set_path)
    min_ov = int(MIN_OVERLAP_S * FP_RATE)
    candidates: list[Play] = []

    for idx, tr in enumerate(tracks):
        res = best_alignment(set_fp, tr.fingerprint, min_ov)
        if progress:
            progress(idx + 1, len(tracks), tr.file)
        if res is None:
            continue
        lag, ber, per = res
        if ber > MATCH_BER * 1.5:
            continue
        run = _run_bounds(per, RUN_BER)
        if run is None:
            continue
        i0, i1 = run
        pad = 6  # ~0.75 s: compensate smoothing erosion at run edges
        i0, i1 = max(0, i0 - pad), min(len(per), i1 + pad)
        run_ber = float(per[i0:i1].mean())
        if run_ber > MATCH_BER or (i1 - i0) / FP_RATE < MIN_PLAY_S:
            continue
        s0 = max(0, lag)
        t_start = (s0 + i0) / FP_RATE
        t_end = (s0 + i1) / FP_RATE
        candidates.append(Play(tr.id, tr.file, round(t_start, 2), round(t_end, 2),
                               round(1.0 - run_ber, 3), "exact", "matched"))

    # greedy non-overlapping assignment, best confidence first
    candidates.sort(key=lambda p: -p.confidence)
    accepted: list[Play] = []
    for c in candidates:
        overlap = sum(max(0.0, min(c.t_end, a.t_end) - max(c.t_start, a.t_start)) for a in accepted)
        if overlap < 0.5 * (c.t_end - c.t_start):
            accepted.append(c)
    accepted.sort(key=lambda p: p.t_start)

    # uncovered gaps -> review segments (never an invented track)
    plays: list[Play] = []
    cursor = 0.0
    for a in accepted + [Play(None, None, set_dur, set_dur, 0.0, "none", "review")]:
        if a.t_start - cursor >= GAP_REVIEW_S:
            plays.append(Play(None, None, round(cursor, 2), round(a.t_start, 2), 0.0, "none", "review"))
        if a.track_file is not None or a.t_end > a.t_start:
            if a.track_file is not None:
                plays.append(a)
        cursor = max(cursor, a.t_end)
    return plays
