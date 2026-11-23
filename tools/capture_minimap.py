"""Saves the top-right corner of Fortnite's window (where the minimap is) every 2 seconds, for
building and testing the map window. Not shipped; run it while you play a match or two:

    python tools/capture_minimap.py            (Ctrl+C or close the window to stop)

Pictures go to %APPDATA%\\ChestTracker\\minimap_debug\\<date-time>\\:
  corner_<n>.png   top-right corner of the Fortnite window, every 2 s while Fortnite is in front
  full_<n>.jpg     the whole Fortnite window, every 30 s (to see where the HUD sits)
  times.csv        n, epoch seconds, file, window size
Only the screen is read (like the chest pop-up reader); the game itself is never touched.
"""
import csv
import datetime as dt
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import chest_tracker as ct  # noqa: E402

CORNER = (0.70, 0.0, 1.0, 0.42)  # [left, top, right, bottom] fractions of the window
EVERY = 2.0
FULL_EVERY = 30.0
MAX_FILES = 2500


def main():
    import mss
    from PIL import Image

    out = ct.DATA_DIR / "minimap_debug" / dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out.mkdir(parents=True, exist_ok=True)
    print("saving to", out, flush=True)
    print("Play a match; this window can stay in the background. Ctrl+C to stop.", flush=True)
    n = 0
    last_full = 0.0
    with open(out / "times.csv", "w", newline="") as f, (getattr(mss, "MSS", None) or mss.mss)() as sct:
        w = csv.writer(f)
        w.writerow(["n", "epoch", "file", "win_w", "win_h"])
        while n < MAX_FILES:
            t0 = time.monotonic()
            rect = ct.fortnite_window_rect()
            if rect and ct.fortnite_in_front():
                l, t, r, b = CORNER
                box = {"left": rect["left"] + int(l * rect["width"]), "top": rect["top"] + int(t * rect["height"]),
                       "width": int((r - l) * rect["width"]), "height": int((b - t) * rect["height"])}
                now = time.time()
                shot = sct.grab(box)
                Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(out / f"corner_{n:05d}.png", compress_level=3)
                w.writerow([n, f"{now:.3f}", f"corner_{n:05d}.png", rect["width"], rect["height"]])
                if now - last_full > FULL_EVERY:
                    last_full = now
                    full = sct.grab(rect)
                    Image.frombytes("RGB", full.size, full.bgra, "raw", "BGRX").save(out / f"full_{n:05d}.jpg", quality=88)
                    w.writerow([n, f"{now:.3f}", f"full_{n:05d}.jpg", rect["width"], rect["height"]])
                f.flush()
                n += 1
                if n % 30 == 0:
                    print(f"{n} pictures saved", flush=True)
            time.sleep(max(0.05, EVERY - (time.monotonic() - t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("stopped")
