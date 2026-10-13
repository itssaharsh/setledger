"""Crate indexing: walk a folder, fingerprint every decodable track, upsert into the store."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .fingerprint import raw_fingerprint

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aiff"}


@dataclass
class IndexReport:
    indexed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (file, reason)


def parse_name(path: Path) -> tuple[str, str]:
    """Best-effort title/artist from filename; T08's normalizer improves on this."""
    stem = path.stem
    parts = stem.split("_", 1)
    title = parts[1].replace("_", " ") if len(parts) == 2 and parts[0].isdigit() else stem.replace("_", " ")
    return title, "Unknown"


def index_crate(crate_dir: Path, store, registered_files: set[str] | None = None) -> IndexReport:
    report = IndexReport()
    registered_files = registered_files or set()
    for path in sorted(crate_dir.iterdir()):
        if path.suffix.lower() not in AUDIO_EXTS or path.name.startswith("."):
            continue
        try:
            fp, duration = raw_fingerprint(path)
            if len(fp) == 0:
                raise ValueError("empty fingerprint")
        except Exception as e:  # undecodable audio must not kill the crate
            report.skipped.append((path.name, str(e)[:120]))
            continue
        title, artist = parse_name(path)
        store.upsert_track(path.name, title, artist, duration, fp,
                           registered=path.name in registered_files)
        report.indexed.append(path.name)
    return report
