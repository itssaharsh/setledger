"""Ground-truth benchmark: scan synthetic sets, score ID rate and precision per tier.

Tiers: clean (unshifted crate plays), shifted (tempo-shifted plays), foreign (not in crate).
A truth segment counts as identified when a matched play names the right track and overlaps
it by >=50%. Precision counts matched plays that correspond to a correct truth segment.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .scan import Play


@dataclass
class TierResult:
    tier: str
    total: int
    identified: int

    @property
    def rate(self) -> float:
        return self.identified / self.total if self.total else 0.0


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def tier_of(seg: dict) -> str:
    if seg["track_file"] is None:
        return "foreign"
    return "clean" if seg["tempo_shift"] == 1.0 else "shifted"


MIN_CREDIT_OVERLAP_S = 5.0


def score(truth: list[dict], plays: list[Play]) -> dict:
    """Identified: right track covering >=50% of the truth segment. Partial: right track,
    real overlap, under 50% coverage (exact layer on shifted plays does this). False claim:
    a play whose best-overlapping truth segment is a different track or foreign audio."""
    matched = [p for p in plays if p.status == "matched"]
    tiers = {t: TierResult(t, 0, 0) for t in ("clean", "shifted", "foreign")}
    partials = {t: 0 for t in ("clean", "shifted", "foreign")}
    correct_play_idx: set[int] = set()

    for seg in truth:
        tier = tier_of(seg)
        tiers[tier].total += 1
        seg_len = seg["t_end"] - seg["t_start"]
        best_cov = 0.0
        hit = None
        for i, p in enumerate(matched):
            ov = overlap(seg["t_start"], seg["t_end"], p.t_start, p.t_end)
            if seg["track_file"] is not None and p.track_file == seg["track_file"] and ov >= MIN_CREDIT_OVERLAP_S:
                correct_play_idx.add(i)
                if ov > best_cov:
                    best_cov, hit = ov, i
        if hit is not None:
            if best_cov >= 0.5 * seg_len:
                tiers[tier].identified += 1
            else:
                partials[tier] += 1

    # a play is a false claim when its best-overlap truth segment is another track/foreign
    false_claims = 0
    for i, p in enumerate(matched):
        if i in correct_play_idx:
            continue
        false_claims += 1
    precision = (len(correct_play_idx)) / len(matched) if matched else 1.0
    foreign_claimed = 0
    for seg in truth:
        if seg["track_file"] is None:
            seg_len = seg["t_end"] - seg["t_start"]
            if any(overlap(seg["t_start"], seg["t_end"], p.t_start, p.t_end) >= 0.5 * seg_len for p in matched):
                foreign_claimed += 1
    return {
        "tiers": {t: {"total": r.total, "identified": r.identified, "rate": round(r.rate, 3),
                      "partial": partials[t]}
                  for t, r in tiers.items()},
        "precision": round(precision, 3),
        "false_claims": false_claims,
        "foreign_claimed": foreign_claimed,
        "matched_plays": len(matched),
    }


def table(results: dict) -> str:
    lines = [f"{'tier':<10}{'plays':>6}{'found':>7}{'partial':>9}{'rate':>8}"]
    for t, r in results["tiers"].items():
        lines.append(f"{t:<10}{r['total']:>6}{r['identified']:>7}{r.get('partial',0):>9}{r['rate']:>8.0%}")
    lines.append(f"precision {results['precision']:.0%}   false claims {results['false_claims']}   "
                 f"foreign wrongly claimed {results['foreign_claimed']}")
    return "\n".join(lines)


def run_bench(set_path: Path, truth_path: Path, tracks, scanner) -> dict:
    truth = json.loads(truth_path.read_text())
    plays = scanner(set_path, tracks)
    return score(truth, plays)
