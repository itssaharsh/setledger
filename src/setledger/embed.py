"""Audio window embeddings for the recall layer (ADR-0001: embeddings nominate,
fingerprints confirm).

Two drivers behind one interface:
- SpectralEmbedder: numpy-only log-band spectral envelope. Deterministic, fast, no
  downloads; atempo is pitch-preserving so the envelope barely moves under ±10% tempo.
- ClapEmbedder: laion/clap-htsat-unfused via transformers (optional torch dependency),
  for real-world recordings where a semantic embedding generalizes better.

Both only NOMINATE candidate tracks; a claim still requires exact confirmation in scan.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .mixgen import SR, read_wav

WIN_S = 10.0
HOP_S = 5.0


def _mono(audio: np.ndarray) -> np.ndarray:
    return audio.mean(axis=1) if audio.ndim == 2 else audio


def _windows(x: np.ndarray, win: int, hop: int) -> list[np.ndarray]:
    if len(x) < win:
        return [x] if len(x) >= win // 2 else []
    return [x[i:i + win] for i in range(0, len(x) - win + 1, hop)]


class SpectralEmbedder:
    """Log-spaced band energies of the average power spectrum, L2-normalized."""

    def __init__(self, dim: int = 64, f_lo: float = 50.0, f_hi: float = 8000.0) -> None:
        self.dim = dim
        self._edges = np.geomspace(f_lo, f_hi, dim + 1)

    def available(self) -> bool:
        return True

    def _features(self, w: np.ndarray) -> np.ndarray:
        # average power spectrum over 1s frames to be phase/offset tolerant
        frame = SR
        spec = np.zeros(frame // 2 + 1, dtype=np.float64)
        n = 0
        for i in range(0, len(w) - frame + 1, frame):
            spec += np.abs(np.fft.rfft(w[i:i + frame])) ** 2
            n += 1
        if n == 0:
            spec = np.abs(np.fft.rfft(w, n=frame)) ** 2
            n = 1
        spec /= n
        freqs = np.fft.rfftfreq(frame, 1.0 / SR)
        idx = np.searchsorted(self._edges, freqs)
        feat = np.zeros(self.dim, dtype=np.float64)
        for b in range(1, self.dim + 1):
            band = spec[idx == b]
            feat[b - 1] = band.mean() if len(band) else 0.0
        feat = np.log1p(feat * 1e3)
        norm = np.linalg.norm(feat)
        return (feat / norm if norm > 0 else feat).astype(np.float32)

    def embed_path(self, path: Path, win_s: float = WIN_S, hop_s: float = HOP_S) -> np.ndarray:
        x = _mono(read_wav(Path(path)))
        wins = _windows(x, int(win_s * SR), int(hop_s * SR))
        if not wins:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._features(w) for w in wins])


class ClapEmbedder:
    """CLAP audio tower (laion/clap-htsat-unfused). Heavy: torch + ~450MB weights."""

    def __init__(self, model_id: str = "laion/clap-htsat-unfused") -> None:
        self.model_id = model_id
        self.dim = 512
        self._model = None
        self._processor = None

    def available(self) -> bool:
        try:
            self._load()
            return True
        except Exception:
            return False

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch  # noqa: F401
        from transformers import ClapModel, ClapProcessor
        self._processor = ClapProcessor.from_pretrained(self.model_id)
        self._model = ClapModel.from_pretrained(self.model_id)
        self._model.eval()

    def embed_path(self, path: Path, win_s: float = WIN_S, hop_s: float = HOP_S) -> np.ndarray:
        import torch
        self._load()
        x = _mono(read_wav(Path(path)))
        # CLAP expects 48 kHz; linear resample is fine for feature extraction
        t48 = np.arange(int(len(x) * 48000 / SR)) * (SR / 48000)
        x48 = np.interp(t48, np.arange(len(x)), x).astype(np.float32)
        wins = _windows(x48, int(win_s * 48000), int(hop_s * 48000))
        if not wins:
            return np.zeros((0, self.dim), dtype=np.float32)
        out = []
        with torch.no_grad():
            for w in wins:
                inputs = self._processor(audio=w, sampling_rate=48000, return_tensors="pt")
                feats = self._model.get_audio_features(**inputs)
                # transformers >=5: BaseModelOutputWithPooling whose pooler_output is the
                # projected joint-space embedding; older versions return the tensor itself
                emb = (feats.pooler_output if hasattr(feats, "pooler_output") else feats)[0].numpy()
                n = np.linalg.norm(emb)
                out.append((emb / n if n > 0 else emb).astype(np.float32))
        return np.stack(out)


def default_embedder():
    """CLAP when its stack is importable and cached, else the spectral driver."""
    clap = ClapEmbedder()
    return clap if clap.available() else SpectralEmbedder()


def index_embeddings(crate_dir: Path, store, embedder, progress=None) -> int:
    """Embed every indexed track's windows into the store. Returns windows written."""
    total = 0
    tracks = store.all_tracks()
    for i, tr in enumerate(tracks):
        wins = embedder.embed_path(crate_dir / tr.file)
        store.set_embeddings(tr.id, wins)
        total += len(wins)
        if progress:
            progress(i + 1, len(tracks), tr.file)
    return total


def nominate(seg_windows: np.ndarray, store, k: int = 5, top_tracks: int = 2) -> list[int]:
    """Vote across segment windows via the store's nearest_windows; return track ids."""
    scores: dict[int, float] = {}
    for w in seg_windows:
        for track_id, _w_index, dist in store.nearest_windows(w, k=k):
            scores[track_id] = scores.get(track_id, 0.0) + max(0.0, 1.0 - float(dist))
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return [tid for tid, _ in ranked[:top_tracks]]
