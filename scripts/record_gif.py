#!/usr/bin/env python3
"""Record the demo GIF: ?record=1&autoscan=1 on a running server, video -> GIF via ffmpeg.

Usage: python scripts/record_gif.py [base_url] [out.gif]
Crops to the stage (waveform + claim lane + chips) so 12px chips survive downscaling.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else
           Path(__file__).parent.parent / "docs" / "media" / "demo.gif")
W, H = 1280, 760


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="slrec_"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": W, "height": H},
                                  record_video_dir=str(tmp),
                                  record_video_size={"width": W, "height": H})
        page = ctx.new_page()
        page.goto(f"{BASE}/?record=1&autoscan=1")
        # wait for the scan to finish (export button appears), then hold on the result
        page.wait_for_selector("#export-btn:not([hidden])", timeout=180_000)
        time.sleep(1.0)
        chip = page.locator("button.chip .glyph-embedding").first
        if chip.count():
            chip.click()                     # the differentiator: ≈ claim detail
            time.sleep(2.5)
        video = page.video.path()
        ctx.close()
        browser.close()
    stage = "crop=1260:500:10:72"            # stage + chips; the post carries the name
    palette = tmp / "pal.png"
    font = subprocess.run(["fc-match", "-f", "%{file}", "monospace"],
                          capture_output=True, text=True).stdout.strip()
    # 2x playback is labeled on-frame; the stage status line keeps the real scan time
    vf = (f"{stage},setpts=0.5*PTS,fps=8,scale=860:-1:flags=lanczos,"
          f"drawtext=fontfile={font}:text='2x speed':x=w-tw-14:y=12:fontsize=14:fontcolor=0xA9A293")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video,
                    "-vf", f"{vf},palettegen=max_colors=64:stats_mode=diff", str(palette)],
                   check=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", str(palette),
                    "-lavfi", f"{vf}[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle",
                    str(OUT)], check=True)
    mp4 = OUT.with_suffix(".mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-vf",
                    "crop=1260:500:10:72", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(mp4)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"gif {OUT} ({OUT.stat().st_size // 1024} KB) | mp4 {mp4} ({mp4.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
