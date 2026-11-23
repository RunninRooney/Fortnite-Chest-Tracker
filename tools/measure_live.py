"""How well live counting matches the replays. (A check for this PC; not shipped.)

    python tools/measure_live.py                 every match in tracker.log that has a replay
    python tools/measure_live.py --since 2026-11-19
    python tools/measure_live.py --misses        also list each missed / extra chest
    python tools/measure_live.py --frames        count again from the saved debug_frames reads
                                                 with today's chest_tracker.py (what a fix would do)

For each match: the chests the replay says you opened (with their times) against the
"chest +N -> game" lines in %APPDATA%\\ChestTracker\\tracker.log. A live count within -1.5..+3.5 s
of an opened chest is a match; an opened chest with none is a miss, a live count with no chest an
extra. Misses right after another chest (gap column) are chests merged into one pop-up.
Replays are read with reader\\ChestReplayReader.exe -v and cached in %TEMP%\\chest_measure.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
LOG = Path(os.environ["APPDATA"]) / "ChestTracker" / "tracker.log"
DEMOS = Path(os.environ.get("LOCALAPPDATA", "")) / "FortniteGame" / "Saved" / "Demos"
READER = APP / "reader" / "ChestReplayReader.exe"
CACHE = Path(tempfile.gettempdir()) / "chest_measure"
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) INFO chest \+(\d+) -> game")


def live_counts(path: Path) -> list[float]:
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(ln)
        if m:
            t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp() + int(m.group(2)) / 1000
            out += [t] * int(m.group(3))
    return out


def frame_counts(since_t: float) -> list[float]:
    """Live counts re-done with today's chest_tracker.py on the reads saved in debug_frames
    (only the pop-ups recorded there; "debug_frames": true in config.json)."""
    sys.path.insert(0, str(APP))
    import chest_tracker as ct

    root = LOG.parent / "debug_frames"
    recs = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        f = d / "frames.jsonl"
        if f.exists():
            recs += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    recs = sorted((r for r in recs if r["wall"] >= since_t), key=lambda r: r["t"])
    logic, last, out = ct.ChestLogic(), None, []
    for r in recs:
        if r["read"]:
            last = ct.read_xp_popup([(t, tuple(b)) for t, b in r["lines"]], ct.DEFAULT_CONFIG)
        elif r["changed"]:
            continue  # not read: no news
        out += [r["wall"]] * ct.sample_popup(logic, last, r["t"])
    return out


def read_replay(path: Path) -> dict | None:
    CACHE.mkdir(exist_ok=True)
    c = CACHE / (path.name + f".{int(path.stat().st_mtime)}.json")
    if c.exists():
        return json.loads(c.read_text())
    out = subprocess.run([str(READER), "-v", str(path)], capture_output=True, text=True, timeout=600).stdout
    j = next((json.loads(x) for x in reversed(out.splitlines()) if x.startswith("{")), None)
    if j and j.get("complete"):
        c.write_text(json.dumps(j))
    return j


def match(live, opened, before=1.5, after=3.5):
    used = [False] * len(opened)
    extras = []
    for lt in live:
        i = next((i for i, ot in enumerate(opened) if not used[i] and ot - before <= lt <= ot + after), None)
        if i is None:
            extras.append(lt)
        else:
            used[i] = True
    return [ot for i, ot in enumerate(opened) if not used[i]], extras


def main():
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2000-01-01"
    since_t = dt.datetime.fromisoformat(since).timestamp()
    if "--frames" in sys.argv:
        counts = frame_counts(since_t)
    else:
        counts = [t for t in live_counts(LOG) if t >= since_t]
    if not counts:
        print("no live counts in", LOG)
        return
    rows = []
    for p in sorted(DEMOS.glob("*.replay")):
        j = read_replay(p)
        if not j or not j.get("complete") or not j.get("start_utc"):
            continue
        start = dt.datetime.fromisoformat(j["start_utc"].replace("Z", "")[:26] + "+00:00").timestamp()
        end = start + j["length_ms"] / 1000
        if start < since_t - 60:
            continue
        opened = sorted(start + c["t"] for c in j["chests"] if not c["broken"])
        live = [t for t in counts if start - 5 <= t <= end + 5]
        if not live:
            continue  # Chest Tracker wasn't counting during this match
        misses, extras = match(live, opened)
        rows.append((p.name, start, opened, live, misses, extras))
    o = sum(len(r[2]) for r in rows)
    lv = sum(len(r[3]) for r in rows)
    mi = sum(len(r[4]) for r in rows)
    ex = sum(len(r[5]) for r in rows)
    print(f"{'replay':42} opened live miss extra")
    for name, start, opened, live, misses, extras in rows:
        print(f"{name:42} {len(opened):6} {len(live):4} {len(misses):4} {len(extras):5}")
        if "--misses" in sys.argv:
            for m in misses:
                prev = [x for x in opened if x < m]
                gap = f"{m - prev[-1]:.1f} s after the last chest" if prev else "first chest"
                print(f"    miss  {m - start:7.1f} s  ({gap})")
            for e in extras:
                print(f"    extra {e - start:7.1f} s")
    if rows:
        print(f"{len(rows)} matches: opened {o}, live {lv} ({lv / max(1, o):.1%}), "
              f"matched {o - mi} ({(o - mi) / max(1, o):.1%}), missed {mi}, extra {ex}")


if __name__ == "__main__":
    main()
