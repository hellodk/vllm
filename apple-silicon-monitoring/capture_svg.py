#!/usr/bin/env python3
"""
Capture an animated SVG as a GIF using Chrome headless + ffmpeg.
Steps:
  1. Serve the SVG locally (Chrome blocks file:// for some SVG features)
  2. Use Chrome headless to screenshot frames over the animation cycle
  3. Stitch frames into an animated GIF with ffmpeg
"""

import subprocess, os, time, threading, shutil
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

SVG_FILE   = "data-flow.svg"
OUT_GIF    = "data-flow.gif"
FRAMES_DIR = Path("/tmp/svg_frames")
PORT       = 18432

# Animation longest cycle: gateway→prom particles dur=1.4s, alert dur=3s
# Capture 3 full seconds at 12 fps → 36 frames; GIF loops perfectly
FPS        = 12
DURATION   = 3.0          # seconds to capture
DELAY_MS   = int(1000 / FPS)
WIDTH, HEIGHT = 1090, 670

# ── 1. Start a local HTTP server ───────────────────────────────────────────
class SilentHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

server = HTTPServer(("127.0.0.1", PORT), SilentHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
print(f"Serving on http://127.0.0.1:{PORT}")

# ── 2. Capture frames with Chrome headless ─────────────────────────────────
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
# Clear any old frames
for f in FRAMES_DIR.glob("frame_*.png"):
    f.unlink()

n_frames = int(FPS * DURATION)
url = f"http://127.0.0.1:{PORT}/{SVG_FILE}"

print(f"Capturing {n_frames} frames at {FPS} fps …")

# Warm-up: open the page once so animations have started before frame capture
warmup = subprocess.run([
    "google-chrome", "--headless=new", "--disable-gpu",
    "--no-sandbox", "--hide-scrollbars",
    f"--window-size={WIDTH},{HEIGHT}",
    f"--screenshot=/tmp/svg_warmup.png",
    url
], capture_output=True)

# Wait for animation to reach a mid-cycle point (avoids t=0 blank state)
time.sleep(0.4)

for i in range(n_frames):
    out = FRAMES_DIR / f"frame_{i:04d}.png"
    subprocess.run([
        "google-chrome", "--headless=new", "--disable-gpu",
        "--no-sandbox", "--hide-scrollbars",
        f"--window-size={WIDTH},{HEIGHT}",
        f"--screenshot={out}",
        url
    ], capture_output=True, check=True)
    # Advance time by one frame interval
    time.sleep(1.0 / FPS)
    if (i + 1) % 6 == 0:
        print(f"  {i+1}/{n_frames} frames captured")

server.shutdown()
print("Frames captured. Stitching GIF …")

# ── 3. Stitch frames → GIF with ffmpeg ────────────────────────────────────
# Scale to 50% (545×335) to keep GIF file size reasonable
subprocess.run([
    "ffmpeg", "-y",
    "-framerate", str(FPS),
    "-i", str(FRAMES_DIR / "frame_%04d.png"),
    "-vf", (
        "scale=545:-1:flags=lanczos,"
        "split[s0][s1];"
        "[s0]palettegen=max_colors=128[p];"
        "[s1][p]paletteuse=dither=bayer"
    ),
    "-loop", "0",
    OUT_GIF
], check=True)

size_kb = Path(OUT_GIF).stat().st_size // 1024
print(f"\nDone → {OUT_GIF}  ({size_kb} KB)")
