"""Set scanning: find which crate tracks play where in a set recording.

Exact layer: chromaprint raw fingerprints compared at every alignment lag between the
set and each crate track (XOR + popcount). A play is a contiguous run of low bit-error
alignment. Segments nothing matches become status='review' — the scanner never invents
a track, by construction: candidates can only come from the crate.

Embedding layer (ADR-0001): for each review segment, window embeddings NOMINATE a few
candidate tracks via the store's vector search; an atempo sweep plus the SAME exact
matcher must then CONFIRM before anything is claimed (method='embedding'). The recall
layer can therefore never create a claim the fingerprint evidence doesn't support.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .fingerprint import FP_RATE, popcount32, raw_fingerprint
from .store import TrackRow

MATCH_BER = 0.28          # max mean bit-error rate for an exact match
RUN_BER = 0.32            # per-position threshold when trimming the matched run
MIN_OVERLAP_S = 12.0      # shortest overlap considered
MIN_PLAY_S = 15.0         # shortest reported play
GAP_REVIEW_S = 10.0       # uncovered gaps at least this long become review segments
TEMPO_FACTORS = [round(0.90 + 0.02 * i, 2) for i in range(11)]  # confirmation sweep


@dataclass
class Play:
    track_id: int | None
    track_file: str | None
    t_start: float
    t_end: float
    confidence: float
    method: str           # exact | embedding | none
    status: str           # matched | review
    fragments: int = 1    # evidence runs joined into this one play


MERGE_GAP_S = 5.0         # same-track claims closer than this are one physical play


def merge_fragments(claims: list[Play]) -> list[Play]:
    """Join adjacent claims of the same track into one play.

    A tempo-shifted play is typically proven in pieces (an exact run plus recall-confirmed
    runs either side). Reporting the pieces separately would file one play as several, so
    they become one claim: method 'embedding' if any piece needed recall, confidence = the
    weakest piece's (a joined claim is only as strong as its weakest evidence).
    """
    out: list[Play] = []
    for c in sorted(claims, key=lambda p: p.t_start):
        last = out[-1] if out else None
        if last and last.track_file == c.track_file and c.t_start - last.t_end <= MERGE_GAP_S:
            out[-1] = Play(last.track_id, last.track_file, last.t_start, max(last.t_end, c.t_end),
                           min(last.confidence, c.confidence),
                           "embedding" if "embedding" in (last.method, c.method) else "exact",
                           "matched", last.fragments + c.fragments)
        else:
            out.append(c)
    return out


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
    return _with_review_gaps(accepted, set_dur)


def _with_review_gaps(accepted: list[Play], set_dur: float) -> list[Play]:
    """Merge fragments, then interleave uncovered gaps as review segments (never an
    invented track)."""
    plays: list[Play] = []
    cursor = 0.0
    for a in merge_fragments(accepted) + \
            [Play(None, None, set_dur, set_dur, 0.0, "none", "review")]:
        if a.t_start - cursor >= GAP_REVIEW_S:
            plays.append(Play(None, None, round(cursor, 2), round(a.t_start, 2), 0.0, "none", "review"))
        if a.track_file is not None:
            plays.append(a)
        cursor = max(cursor, a.t_end)
    return plays


def _slice_wav(set_path: Path, t0: float, t1: float, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t0:.2f}",
                    "-t", f"{t1 - t0:.2f}", "-i", str(set_path), str(out)], check=True)


def confirm_candidates(set_path: Path, t0: float, t1: float, candidates: list[TrackRow],
                       tmp_dir: Path, seg_path: Path | None = None) -> Play | None:
    """Tempo-sweep a review segment against nominated tracks with the exact matcher.

    The segment is un-shifted by 1/f for each factor; a claim requires a passing
    low-BER run, exactly like the exact layer. Returns the best confirmed Play or None.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    if seg_path is None:
        seg_path = tmp_dir / f"seg_{t0:.0f}_{t1:.0f}.wav"
        _slice_wav(set_path, t0, t1, seg_path)
    min_ov = int(min(MIN_OVERLAP_S, max(4.0, (t1 - t0) * 0.5)) * FP_RATE)
    # a short review gap is usually the unclaimed remainder of an adjacent play, so the
    # minimum claimable duration shrinks with the gap (never below 6 s of evidence)
    min_play = min(MIN_PLAY_S, max(6.0, 0.5 * (t1 - t0)))
    best: tuple[tuple[float, float], Play] | None = None
    for f in TEMPO_FACTORS:
        if f == 1.0:
            un = seg_path
        else:
            un = tmp_dir / f"{seg_path.stem}_f{f}.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(seg_path),
                            "-filter:a", f"atempo={1.0 / f:.6f}", str(un)], check=True)
        try:
            seg_fp, _ = raw_fingerprint(un)
        except ValueError:
            continue
        for tr in candidates:
            res = best_alignment(seg_fp, tr.fingerprint, min_ov)
            if res is None:
                continue
            lag, _, per = res
            run = _run_bounds(per, RUN_BER)
            if run is None:
                continue
            i0, i1 = run
            pad = 6
            i0, i1 = max(0, i0 - pad), min(len(per), i1 + pad)
            run_ber = float(per[i0:i1].mean())
            # positions in the un-shifted segment map back to set time at t0 + u/f
            u0 = (max(0, lag) + i0) / FP_RATE
            u1 = (max(0, lag) + i1) / FP_RATE
            s0, s1 = max(t0, t0 + u0 / f), min(t1, t0 + u1 / f)
            if run_ber > MATCH_BER or s1 - s0 < min_play:
                continue
            key = (s1 - s0, -run_ber)
            if best is None or key > best[0]:
                best = (key, Play(tr.id, tr.file, round(s0, 2), round(s1, 2),
                                  round(1.0 - run_ber, 3), "embedding", "matched"))
    return best[1] if best else None


@dataclass
class ScanResult:
    plays: list[Play]
    degraded: bool                      # True -> exact-only (no embedding layer)
    nominated: dict[str, list[str]] = field(default_factory=dict)  # "t0-t1" -> files


def scan_full(set_path: Path, tracks: list[TrackRow], store, embedder=None,
              progress=None, tmp_dir: Path | None = None, on_claim=None) -> ScanResult:
    """Exact scan, then embedding nomination + exact confirmation on review segments.

    on_claim(play, phase): called as claims land ('exact' once per exact play, then
    'embedding' per confirmed recall claim) — the UI streams these onto the timeline.
    """
    from .embed import nominate

    plays = scan_exact(set_path, tracks, progress)
    if on_claim:
        for p in plays:
            if p.status == "matched":
                on_claim(p, "exact")
    degraded = embedder is None or not embedder.available()
    if degraded:
        return ScanResult(plays, True)
    tmp = Path(tmp_dir) if tmp_dir else Path(set_path).parent / ".tmp_scan"
    by_id = {t.id: t for t in tracks}
    matched = [p for p in plays if p.status == "matched"]
    set_dur = max((p.t_end for p in plays), default=0.0)
    nominated: dict[str, list[str]] = {}
    # worklist: a review segment can hold remainders of SEVERAL plays, so after each
    # confirmed claim the carved sub-gaps go back on the list (bounded per segment)
    work = [(p.t_start, p.t_end, 4) for p in plays if p.status == "review"]
    while work:
        t0, t1, budget = work.pop()
        if budget == 0 or t1 - t0 < GAP_REVIEW_S:
            continue
        seg = tmp / f"seg_{t0:.0f}_{t1:.0f}.wav"
        _slice_wav(set_path, t0, t1, seg)
        cand_ids = nominate(embedder.embed_path(seg), store)
        cands = [by_id[c] for c in cand_ids if c in by_id]
        if not cands:
            continue
        nominated[f"{t0:.0f}-{t1:.0f}"] = [c.file for c in cands]
        play = confirm_candidates(set_path, t0, t1, cands, tmp, seg_path=seg)
        if play is not None:
            matched.append(play)
            if on_claim:
                on_claim(play, "embedding")
            work.append((t0, play.t_start, budget - 1))
            work.append((play.t_end, t1, budget - 1))
    return ScanResult(_with_review_gaps(matched, set_dur), False, nominated)
