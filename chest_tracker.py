"""
Chest Tracker - an automatic Fortnite chest counter with a tiny always-on-top overlay.

How it counts chests
--------------------
1. Live, while you play: when you open a chest, Fortnite shows a "Chest +600 XP" pop-up. This
   app reads that part of your screen a few times per second with on-device text recognition
   (nothing is uploaded) and counts one chest each time the pop-up appears.

2. Confirmed, after each match: Fortnite saves a replay of every match to
   %LOCALAPPDATA%\\FortniteGame\\Saved\\Demos. When the match ends, the bundled replay reader
   (reader\\ChestReplayReader.exe) lists every chest you opened AND every chest you broke. The
   game's number is then corrected: opened = the larger of (seen live, in the replay), plus the
   broken chests from the replay. Broken chests show no pop-up, so only the replay can see them.

3. Past matches: on first start, every replay already in the Demos folder from this season is
   read once and added to TOTAL (each recording counts only once, even if it is renamed).

4. Teammates: their own characters are in every replay all match long, with a flag that is on while
   they search something. A full-length search is (almost always) a chest, so a console teammate's
   chests are counted from that, even when they were far away.

When a match starts, when your squad is out (the placement screen) and when you're back in the
lobby is read from Fortnite's own log file (FortniteGame.log). During a match the overlay lets
clicks go through to the game; click Chest Tracker on the taskbar to move it.

It never touches Fortnite's memory or network traffic. It only reads files Fortnite writes to
disk (its log and its replays) and the screen, so it is as safe as running OBS or Discord.

Team: siblings who enter the same team code see each other's counts go up live. Counts are
shared over the internet through a public MQTT relay (only your name and chest counts are sent,
under a topic derived from the team code) and directly over the home network.

Overlay: drag it with the left mouse button. Right-click it for settings: background opacity,
size, your name and the team code.

Hotkeys (work while in game):
  F6  add a chest by hand (one the app missed)
  F7  take back one chest
  F8  calibrate now: press it while a "Chest +600 XP" pop-up is on screen
  F9  show/hide everyone's chests (works in game, when Fortnite holds the mouse)
  F11 open settings (also frees the mouse, so the overlay can be dragged)
  F10 start a new game count by hand (normally automatic)
"""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import app_model
import edition
import teamcrypt
import treasures

APP_VERSION = "1.31 (Nov 23, 2026)"  # shown in the settings window title and the log
IS_WIN = sys.platform == "win32"
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / "ChestTracker"
CONFIG_PATH = DATA_DIR / "config.json"
STATS_PATH = DATA_DIR / "stats.json"
REPLAYS_PATH = DATA_DIR / "replays.json"
TEAM_PATH = DATA_DIR / "team.json"
LOG_PATH = DATA_DIR / "tracker.log"
DEMOS_DIR = Path(os.environ.get("LOCALAPPDATA") or "") / "FortniteGame" / "Saved" / "Demos"
READER_EXE = APP_DIR / "reader" / "ChestReplayReader.exe"

DEFAULT_CONFIG = {
    "name": "",
    # Your in-game Fortnite name(s), e.g. ["RunninRobin"]. Only these players' chests count for you.
    # Found automatically from the replays on this PC; add console names too (e.g. a PlayStation name)
    # so chests a teammate's replays saw you get there get added. Change them in settings.
    "fortnite_names": [],
    # Fortnite names of teammates who play on a console without a tracker (e.g. ["jFalcon"]). Their
    # chests in this PC's replays are shown after each game and added to the team's numbers.
    "console_teammates": [],
    # Words that mean "this XP pop-up is for a chest". Lowercase. Add your language's word if needed.
    "chest_words": ["chest"],
    # Words that must be next to it (the XP amount).
    "xp_words": ["xp"],
    # Lines containing these words are never counted.
    "ignore_words": ["ammo", "supply", "llama", "drop"],
    # Part of the screen to read: [left, top, right, bottom] as fractions of the screen.
    # null = not found yet: the whole screen is read slowly until the first chest pop-up is seen.
    "scan_region": None,
    # Text reads (OCR, the costly part) per second at most while no chest pop-up is up. While one
    # is up, up to 5 a second in a smaller area, to catch chests opened back to back ("CHEST X2").
    "scans_per_second": 3,
    # Cheap looks at the pop-up area per second; the text is only read when the picture changed.
    "checks_per_second": 10,
    # Only read the screen while Fortnite is the window in front.
    "only_when_fortnite": True,
    # Only count while a match is being played (Fortnite is recording its replay), never in the lobby.
    "only_in_match": True,
    "monitor": 1,
    "overlay_x": None,
    "overlay_y": None,
    "overlay_scale": 1.0,
    # Background opacity of the overlay panel, 0 (invisible, numbers only) to 1 (solid).
    "bg_opacity": 0.85,
    # true = the overlay ignores the mouse entirely (not in the settings window; use F11 / the taskbar button).
    "click_through": False,
    # During a match the overlay can't be clicked or dragged (clicks go to the game). Clicking Chest
    # Tracker on the taskbar unlocks it until you click back into Fortnite.
    "lock_in_match": True,
    # Show everyone's chests for a while when your squad is out.
    "show_team_at_end": True,
    # Offline mode: nothing leaves this PC and nothing is downloaded. No squad sync (internet or home network),
    # no update checks, no problem reports, no map download (the map works only if it was downloaded before).
    "offline": False,
    # Problem reports from the launcher: None = this build's default (off in the public build), or true/false.
    "send_reports": None,
    # Siblings who enter the same code share their counts live.
    "team_code": "",
    "internet_sync": True,
    "mqtt_brokers": ["broker.hivemq.com:1883", "broker.emqx.io:1883", "test.mosquitto.org:1883"],
    "lan_sync": True,
    "lan_port": 47474,
    "auto_new_game": True,
    # Read Fortnite's replays after each match to confirm the count and add broken chests.
    "replays": True,
    "live_replay": True,   # read the replay mid-match too (every ~20 s), when Fortnite has written its key
    # Add chests from replays recorded while the app wasn't running (including all past ones).
    "import_past_replays": True,
    "record_matches": False,  # record every match with OBS (keeps the last 5, and every match with a real treasure find)
    # Replays from before this date (local time) are never imported. Start of the current season.
    "season_start": "2026-11-01",
    "debug": False,
    # Map window (map_window.py, right-click > Map window): your route and chests on the island, live, for
    # a second screen. Opens by itself at start when it was open last time.
    "map_window": False,
    # Share your position and chests with your team during matches, so everyone's map shows the squad
    # (over the team relay; a few hundred bytes every 3 seconds, only while in a match).
    "map_share": True,
    # Save the pop-up area around every chest pop-up (pictures + what was read) to
    # %APPDATA%\ChestTracker\debug_frames, to look into miscounts. Also on with "debug". Oldest
    # deleted past debug_frames_mb.
    "debug_frames": False,
    "debug_frames_mb": 500,
}

log = logging.getLogger("chest")


# --------------------------------------------------------------------------------------
# Config and saved stats
# --------------------------------------------------------------------------------------
def load_json(path: Path, default: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(default)
        merged.update(data)
        return merged
    except Exception:
        return dict(default)


def save_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:  # never crash the overlay over a save
        log.warning("could not save %s: %s", path, e)


def game_count(seen: int, manual: int, adjust: int, confirmed: dict | None, seen_at=None) -> int:
    """The number of chests a game is worth.

    Before the replay is read: what was seen live (pop-ups) plus chests added by hand.
    After: opened = the larger of (seen live, opened in the replay), so a chest only one of the
    two noticed still counts once; plus broken = the larger of (added by hand, broken in the
    replay), since F6 was the old stand-in for broken chests. `adjust` holds F6/F7 presses
    made after the replay was read.
    When the replay's chest times are known ("chest_times", "start", "upto") and so are the times of
    every live count (seen_at), opened is checked chest by chest instead (verified_opened): a live
    double count is dropped, a chest the screen missed is added.
    """
    if confirmed is None:
        n = seen + manual
    else:
        times = confirmed.get("chest_times")
        if times is not None and confirmed.get("start") and seen_at is not None and len(seen_at) == seen:
            opened = verified_opened(seen_at, [{"t": t} for t in times], float(confirmed["start"]), confirmed.get("upto"))
        else:
            opened = max(seen, int(confirmed["opened"]))
        n = opened + max(manual, int(confirmed["broken"]))
    return max(0, n + adjust)


def replay_start(r: dict, hdr: dict | None = None) -> float | None:
    """When a replay began (epoch s): the reader's start_utc (ms precision), else the header's (1 s)."""
    s = r.get("start_utc")
    if s:
        try:
            whole, _, frac = s.rstrip("Z").partition(".")
            return (dt.datetime.fromisoformat(whole).replace(tzinfo=dt.timezone.utc).timestamp()
                    + (float("0." + frac) if frac.isdigit() else 0.0))
        except ValueError:
            pass
    return (hdr or {}).get("started")


def _replay_check(r: dict, hdr: dict | None, live: bool) -> dict:
    """The keyword arguments Stats.confirm / confirm_partial take to check live counts chest by chest."""
    if "chests" not in r:
        return {}
    upto = r.get("read_upto")
    if upto is None and live:
        upto = (r.get("length_ms") or 0) / 1000 or None  # (a live replay's header says 0 until the end)
    return {"chests": r["chests"], "start": replay_start(r, hdr), "upto": upto}


def verified_opened(seen_at, chests, start: float, upto: float | None = None,
                    before: float = 1.5, after: float = 3.5, dup: float = 0.8) -> int:
    """Chests opened in a game: the live screen counts checked against the replay, chest by chest.

    seen_at: wall times (epoch s) of the live counts, one per chest. chests: the reader's "chests"
    list ({"t": s into the replay, "broken"}), read up to `upto` s into the replay (None: all of
    it); start: when the replay began, epoch s.
    - a live count from -before to +after s around an opened chest is that chest: 1
    - an opened chest no live count matched is one the screen missed: 1
    - a live count no chest matched: 1. After `upto` the replay just hasn't got there yet; before
      it, the replay can miss a chest whose pop-up clearly showed (2 of 50 on Nov 19, 2026).
      Unless it came within `dup` s after a live count that did match: the same pop-up counted
      twice (0). (Real double counts came 0.2-0.5 s apart; a real "CHEST X2" 1.2 s.)
    On Nov 19 this gave 50 for a match whose replay says 48 opened, with 2 more chest pop-ups on
    screen that the replay missed; the old max(seen, opened) gave 58 from that version's live counts.
    """
    opened = sorted(start + float(c["t"]) for c in chests or ()
                    if not c.get("broken") and (upto is None or float(c["t"]) <= upto))
    # The replay's start can be off (the header's is whole seconds): line the two clocks up so the
    # typical gap between a chest and its live count is the usual one (0.34 s on Nov 19, with the
    # reader's exact start_utc).
    gaps = sorted(d for d in (min((lt - ot for ot in opened), key=abs) for lt in seen_at) if abs(d) <= 5) if opened else []
    if len(gaps) >= 3:
        shift = gaps[len(gaps) // 2] - 0.35
        opened = [ot + shift for ot in opened]
    matched = _match_in_order(sorted(seen_at), opened, before, after)
    loose = [lt for lt in sorted(seen_at) if lt not in matched]
    kept = [lt for lt in loose if not any(0 <= lt - m <= dup for m in matched)]
    return len(opened) + len(kept)


def _match_in_order(live, opened, before, after) -> set:
    """The live counts paired with chests (both sorted, in order): as many pairs as possible, each as
    close to the typical gap as it can be. (Taking the first chest that fits pairs wrong in a burst
    of chests a second or two apart.)"""
    n, m = len(live), len(opened)
    # best[i][j]: (pairs, -total distance) using the first i live counts and the first j chests
    best = [[(0, 0.0)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            b = max(best[i - 1][j], best[i][j - 1])
            d = live[i - 1] - opened[j - 1]
            if -before <= d <= after:
                p, c = best[i - 1][j - 1]
                b = max(b, (p + 1, c - abs(d - 0.35)))
            best[i][j] = b
    out, i, j = set(), n, m
    while i and j:
        d = live[i - 1] - opened[j - 1]
        p, c = best[i - 1][j - 1]
        if -before <= d <= after and best[i][j] == (p + 1, c - abs(d - 0.35)):
            out.add(live[i - 1])
            i, j = i - 1, j - 1
        elif best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            j -= 1
    return out


def local_midnight(now: float | None = None) -> float:
    """When today started (local time)."""
    lt = time.localtime(time.time() if now is None else now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


class Stats:
    """This player's counts. Thread-safe; saved to disk on every change."""

    HISTORY_KEEP = 500
    RESET_DEBOUNCE = 90  # seconds: a second "new match" signal this soon after a reset is the same match
    # A replay that began recording from LINK_BEFORE s before to LINK_AFTER s after this game started
    # (Fortnite's log says "joined" ~15 s before the recording begins) is this game's recording.
    LINK_BEFORE, LINK_AFTER = 120, 300

    def __init__(self, path: Path = STATS_PATH):
        self.path = path
        d = load_json(path, {"game": 0, "total": 0, "game_started": time.time(), "history": [], "id": ""})
        self.lock = threading.Lock()
        self.game = int(d["game"])
        self.total = int(d["total"])
        self.game_started = float(d["game_started"])
        self.history = list(d["history"])[-self.HISTORY_KEEP:]
        self.id = d.get("id") or uuid.uuid4().hex[:12]
        # How this game's number is made up (see game_count).
        self.seen = int(d.get("seen", self.game))
        self.seen_at = [float(t) for t in d.get("seen_at") or []]  # when each live count happened (epoch s)
        self.manual = int(d.get("manual", 0))
        self.adjust = int(d.get("adjust", 0))
        self.replay = d.get("replay")          # file name of this game's replay, once known
        self.confirmed = d.get("confirmed")    # {"opened", "broken"} once the replay was read
        self.partial = d.get("partial")        # {"opened", "broken"} so far, from the replay read mid-match
        self.imported = int(d.get("imported", 0))  # chests added from replays of games not seen live
        self.credited: dict[str, str] = dict(d.get("credited") or {})  # match id -> who credited it
        self.finished = d.get("finished")      # when this game's match ended for me (placement screen / lobby)
        self.version = 0  # bumps on every change so the overlay knows to redraw
        self._last_reset = -1e9
        self._save()

    def _save(self):
        save_json(self.path, {"game": self.game, "total": self.total, "game_started": self.game_started,
                              "seen": self.seen, "manual": self.manual, "adjust": self.adjust,
                              "replay": self.replay, "confirmed": self.confirmed, "imported": self.imported,
                              "credited": self.credited, "finished": self.finished, "history": self.history,
                              "partial": self.partial, "seen_at": [round(t, 2) for t in self.seen_at],
                              "id": self.id})

    def _recount(self):
        """Recompute this game's number from its parts and move TOTAL by the same amount."""
        # mid-match, the replay read so far stands in for the final one (see confirm_partial)
        new = game_count(self.seen, self.manual, self.adjust,
                         self.confirmed if self.confirmed is not None else getattr(self, "partial", None),
                         self.seen_at)
        self.total = max(0, self.total + new - self.game)
        self.game = new
        self.version += 1
        self._save()

    def add(self, n: int = 1, manual: bool = False):
        """n chests seen live (manual=False) or added/taken back by hand with F6/F7 (manual=True)."""
        if n > 0 and not manual and (self.confirmed is not None or self.finished):
            # This game's match is over (its replay was read, or the placement screen showed): a chest
            # pop-up now means a new match started without being noticed. Start counting a new game.
            self.new_game("chest after the last game ended")
        with self.lock:
            if n < 0 and self.game <= 0:
                return
            if self.confirmed is not None and manual:
                self.adjust += n
            elif manual and (n > 0 or self.manual > 0):
                self.manual = max(0, self.manual + n)
            else:
                self.seen = max(0, self.seen + n)
                if n > 0:
                    self.seen_at += [time.time()] * n
                else:
                    self.seen_at = self.seen_at[:self.seen]
            self._recount()
        log.info("chest %+d%s -> game %d, total %d", n, " (by hand)" if manual else "", self.game, self.total)

    def _archive(self):
        if self.game > 0 or self.replay:
            self.history.append({"started": self.game_started, "ended": time.time(), "chests": self.game,
                                 "seen": self.seen, "manual": self.manual, "adjust": self.adjust,
                                 "replay": self.replay, "confirmed": self.confirmed,
                                 "finished": self.finished,  # (the match's end: playtime when there's no replay)
                                 "seen_at": [round(t, 2) for t in self.seen_at]})
            self.history = self.history[-self.HISTORY_KEEP:]

    def new_game(self, reason: str, replay: str | None = None, started: float | None = None,
                 debounce: float | None = None) -> bool:
        """Start counting a new game. Returns False if this was the same match as the last reset.
        debounce: how soon after a reset another signal is taken to be the same match (Fortnite's
        log says exactly when a match starts, so it passes a short one).
        started: when a replay began recording. A replay that began while this game was being
        counted is this game's recording, however late it's noticed (the replay watcher can be
        busy reading an old replay for minutes; that used to split a match in two)."""
        with self.lock:
            now = time.monotonic()
            window = self.RESET_DEBOUNCE if debounce is None else debounce
            same = now - self._last_reset < window and (replay is None or not self.replay)
            recording_of_this_game = (
                replay and started and not self.replay and not self.finished
                and self.game_started - self.LINK_BEFORE <= started <= self.game_started + self.LINK_AFTER)
            if same or recording_of_this_game:
                # Two signals for the same match (log line and replay file): keep the counts and
                # just remember the replay. A different replay file is always a new match.
                if replay:
                    self.replay = replay
                    self._save()
                if recording_of_this_game and not same:
                    log.info("replay %s is the current game's recording (noticed %.0f s after it began)",
                             replay, time.time() - started)
                return False
            self._archive()
            self.game = self.seen = self.manual = self.adjust = 0
            self.seen_at = []
            self.replay, self.confirmed, self.finished = replay, None, None
            self.partial = None
            self.game_started = started or time.time()
            self._last_reset = now
            self.version += 1
            self._save()
        log.info("new game (%s)", reason)
        return True

    def forget_screen_counts(self):
        """Drops every chest counted from the screen so far and keeps only replay and hand counts.
        Used once after upgrading from a version that could read the wrong screen.
        TOTAL is then rebuilt from the per-game list."""
        with self.lock:
            for h in self.history:
                if not h.get("imported") and h.get("seen"):
                    h["seen"] = 0
                    h["chests"] = game_count(0, int(h.get("manual", 0)), int(h.get("adjust", 0)), h.get("confirmed"))
            self.history = [h for h in self.history if h.get("chests") or h.get("replay")]
            self.seen, self.seen_at = 0, []
            self.game = game_count(0, self.manual, self.adjust, self.confirmed)
            before = self.total
            self.total = sum(int(h.get("chests", 0)) for h in self.history) + self.game
            self.version += 1
            self._save()
        log.info("dropped screen counts from the old version: total %d -> %d", before, self.total)

    def drop_double_counted(self):
        """Repair for versions that missed new matches: screen counts from later matches piled onto a
        game whose replay was already read, while those matches were also imported from their own
        replays. Keep the replay's count for that game, then start a fresh game."""
        with self.lock:
            if self.confirmed is None:
                return
            before = self.game
            self.seen = min(self.seen, int(self.confirmed.get("opened", 0)))
            self._recount()
            after = self.game
        log.info("removed %d double-counted chests from the current game", before - after)
        self._last_reset = -1e9
        self.new_game("repair after missed new matches")

    def finish(self) -> bool:
        """The match this game counts is over for me (placement screen, or I left it). Returns False
        if it was already marked."""
        with self.lock:
            if self.finished:
                return False
            self.finished = time.time()
            self.version += 1
            self._save()
        log.info("game over: %d chests (%d seen live)", self.game, self.seen)
        return True

    def link_replay(self, replay: str):
        """The app was started mid-match: this game is the one being recorded."""
        with self.lock:
            self.replay = replay
            self._save()

    def today(self, now: float | None = None) -> int:
        """Chests in games started since local midnight, this game included (shown to the squad)."""
        midnight = local_midnight(now)
        with self.lock:
            n = sum(int(h.get("chests") or 0) for h in self.history if float(h.get("started") or 0) >= midnight)
            return n + (self.game if self.game_started >= midnight else 0)

    def is_linked(self, replay: str) -> bool:
        with self.lock:
            return self.replay == replay or any(h.get("replay") == replay for h in self.history)

    @staticmethod
    def _checked(opened, broken, chests=None, start=None, upto=None) -> dict:
        """A replay result as stored: with its opened chests' times when known, so live counts
        are checked against them chest by chest (see game_count)."""
        conf = {"opened": int(opened), "broken": int(broken)}
        if chests is not None and start:
            conf.update(chest_times=[round(float(c["t"]), 2) for c in chests if not c.get("broken")],
                        start=float(start), upto=upto)
        return conf

    def confirm_partial(self, replay: str, opened: int, broken: int, chests=None, start=None,
                        upto=None) -> tuple[int, int] | None:
        """The replay of the current game read mid-match: what it shows so far. Unlike confirm(), the game
        stays open (a pop-up still counts in it). Returns (chests before, after), or None if not this game.
        chests/start/upto (the reader's chest list, the replay's start, how far it was read): the live
        counts are checked against it chest by chest; counts after `upto` count as they are."""
        with self.lock:
            if self.replay != replay or self.confirmed is not None:
                return None
            old = self.partial or {"opened": 0, "broken": 0}
            new = self._checked(opened, broken, chests, start, upto)
            # never go down: a later read can stop early on the half-written last chunk (for opened
            # chests checked by time that's covered: the read says how far it got)
            new["broken"] = max(new["broken"], int(old["broken"]))
            if "chest_times" not in new:
                new["opened"] = max(new["opened"], int(old["opened"]))
            self.partial = new
            before = self.game
            self._recount()
            return before, self.game

    def confirm(self, replay: str, opened: int, broken: int, drop_seen: bool = False, chests=None, start=None,
                upto=None):
        """Apply a replay's result to the game it belongs to.

        drop_seen: the replay shows someone else was playing on this PC, so the pop-ups the screen
        reader counted during that game weren't this player's chests.
        chests/start/upto: the reader's chest list, the replay's start and how far it was read (None: all
        of it): check the live counts against it.
        Returns ("current" | "past", chests before, chests after), or None if no game was linked to it.
        """
        conf = self._checked(opened, broken, None if drop_seen else chests, start, upto)
        with self.lock:
            if self.replay == replay:
                before = self.game
                self.confirmed = conf
                if drop_seen:
                    self.seen, self.seen_at = 0, []
                self._recount()
                return "current", before, self.game
            for h in reversed(self.history):
                if h.get("replay") == replay:
                    before = int(h.get("chests", 0))
                    h["confirmed"] = conf
                    if drop_seen:
                        h["seen"], h["seen_at"] = 0, []
                    after = game_count(int(h.get("seen", before)), int(h.get("manual", 0)), int(h.get("adjust", 0)), conf,
                                       h.get("seen_at"))
                    h["chests"] = after
                    self.total = max(0, self.total + after - before)
                    self.version += 1
                    self._save()
                    return "past", before, after
        return None

    def import_game(self, replay: str, started: float, length_s: float, opened: int, broken: int,
                    session: str | None = None):
        """A game the app didn't see live (it wasn't running): count it from its replay alone.
        If a teammate's replay already credited this match, this replay replaces that credit."""
        n = int(opened) + int(broken)
        with self.lock:
            h = next((h for h in self.history if session and h.get("match") == session), None)
            if h is not None:
                before = int(h.get("chests", 0))
                conf = {"opened": int(opened), "broken": int(broken)}
                after = game_count(int(h.get("seen", 0)), int(h.get("manual", 0)), int(h.get("adjust", 0)), conf,
                                   h.get("seen_at"))
                h.update(replay=replay, confirmed=conf, chests=after, credited_by=None)
                self.total = max(0, self.total + after - before)
                self.version += 1
                self._save()
                return
            self.history.append({"started": started, "ended": started + length_s, "chests": n, "seen": 0,
                                 "manual": 0, "adjust": 0, "replay": replay, "imported": True,
                                 "confirmed": {"opened": int(opened), "broken": int(broken)}})
            self.history.sort(key=lambda h: h.get("started", 0))
            self.history = self.history[-self.HISTORY_KEEP:]
            self.total += n
            self.imported += n
            self.version += 1
            self._save()

    CREDIT_MATCH_S = 600  # a credited match and a game counted live are the same if they started this close

    def credit_game(self, match: str, from_name: str, started: float, opened: int, broken: int) -> int | None:
        """Chests a teammate's replay saw this player open while playing on a console (where there's
        no tracker). Each match counts once; if the same teammate sends new numbers for a match
        (their reader got better), the match is recounted. Returns the change in chests, or None if
        nothing changed."""
        n = int(opened) + int(broken)
        conf = {"opened": int(opened), "broken": int(broken)}

        def worth(h):  # a game's chests with this credit standing in for its replay
            return game_count(int(h.get("seen", 0)), int(h.get("manual", 0)), int(h.get("adjust", 0)), conf,
                              h.get("seen_at"))

        with self.lock:
            if (started and self.replay and not self.confirmed
                    and abs(float(self.game_started) - float(started)) <= self.CREDIT_MATCH_S):
                # the game being played (or just ended) here, with its own recording: its replay counts it
                # (Nov 19, 2026: a credit got in 6 s before my replay of the match was read: counted twice)
                return None
            if match in self.credited:
                h = next((h for h in self.history if h.get("match") == match), None)
                if h is None or self.credited[match] != from_name or h.get("replay") and not h.get("credited_by"):
                    return None  # already counted (or credited by someone else first, or my own replay took over)
                after = worth(h)
                if after == int(h.get("chests", 0)) and h.get("confirmed") == conf:
                    return None
                delta = after - int(h.get("chests", 0))
                h["chests"], h["confirmed"] = after, conf
                self.total = max(0, self.total + delta)
                self.version += 1
                self._save()
                return delta
            # The game this tracker counted live (pop-ups only, no replay of its own) at the same time:
            # the credit is that game's replay check, so it's confirmed, not counted a second time.
            if started:
                live = [h for h in self.history if h.get("confirmed") is None and not h.get("replay")
                        and not h.get("match") and abs(float(h.get("started", 0)) - float(started)) <= self.CREDIT_MATCH_S]
                if live:
                    h = min(live, key=lambda h: abs(float(h.get("started", 0)) - float(started)))
                    self.credited[match] = from_name
                    before, after = int(h.get("chests", 0)), worth(h)
                    h.update(match=match, credited_by=from_name, confirmed=conf, chests=after)
                    self.total = max(0, self.total + after - before)
                    self.version += 1
                    self._save()
                    return after - before
            self.credited[match] = from_name
            self.history.append({"started": float(started or 0), "ended": float(started or 0), "chests": n,
                                 "seen": 0, "manual": 0, "adjust": 0, "replay": None, "credited_by": from_name,
                                 "match": match, "confirmed": {"opened": int(opened), "broken": int(broken)}})
            self.history.sort(key=lambda h: h.get("started", 0))
            self.history = self.history[-self.HISTORY_KEEP:]
            self.total += n
            self.version += 1
            self._save()
        return n

    def drop_credits_for(self, own: set[str]) -> int:
        """Removes games that only a teammate's credit counted, for matches this PC has its own replay of
        (that replay counted them already). Returns the chests taken off the total."""
        with self.lock:
            dup = [h for h in self.history if h.get("credited_by") and not h.get("replay") and h.get("match") in own
                   and not int(h.get("seen") or 0) and not int(h.get("manual") or 0) and not int(h.get("adjust") or 0)]
            if not dup:
                return 0
            gone = sum(int(h.get("chests") or 0) for h in dup)
            ids = {id(h) for h in dup}
            self.history = [h for h in self.history if id(h) not in ids]
            self.total = max(0, self.total - gone)
            self.version += 1
            self._save()
        log.info("removed %d chests counted twice (%d teammate credits for matches my own replays counted)",
                 gone, len(dup))
        return gone


# --------------------------------------------------------------------------------------
# Whose chests: a replay holds the recorder's chests and their teammates' (pure, unit-testable)
# --------------------------------------------------------------------------------------
PC_PLATFORMS = {"WIN", "MAC"}  # a teammate on these may have their own tracker reading their own replays


def norm_name(name) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def my_counts(r: dict, names: set[str]) -> tuple[int, int, str]:
    """(opened, broken, how) for the player called one of `names` in a replay reader result.

    how: "recorder" (they recorded it), "teammate" (they were in someone else's replay on this PC),
    "absent" (someone else's match). With no names known, the recorder is taken to be the player.
    Teammates' chests only count while they were near the recorder.
    """
    rec = norm_name(r.get("recorder_name"))
    if not names or not rec or rec in names:
        return int(r.get("opened", 0)), int(r.get("broken", 0)), "recorder"
    for t in r.get("teammates") or []:
        if norm_name(t.get("name")) in names:
            return int(t.get("opened", 0)), int(t.get("broken", 0)), "teammate"
    return 0, 0, "absent"


SAME_CHEST_M = 6.0  # a search and a credited chest this close (metres) are the same chest


def load_chest_spots(path: Path | None = None) -> dict[str, tuple[float, float]]:
    """Known chest positions by chest id, learned by the map from many matches (all seasons merged)."""
    path = path or DATA_DIR / "map" / "chest_spots.json"
    try:
        st = path.stat()
    except OSError:
        return {}
    cache = getattr(load_chest_spots, "_cache", None)
    if cache and cache[0] == (str(path), st.st_mtime_ns):
        return cache[1]
    out = {}
    for season in (load_json(path, {}) or {}).values():
        if isinstance(season, dict):
            for cid, v in season.items():
                if isinstance(v, dict) and "x" in v and "y" in v:
                    out[cid] = (float(v["x"]), float(v["y"]))
    load_chest_spots._cache = ((str(path), st.st_mtime_ns), out)
    return out


def squad_counts(r: dict, spots: dict[str, tuple[float, float]]) -> dict[str, int] | None:
    """Each teammate's opened chests, using the reader's map data ("--map"): every chest the replay
    credits to them, plus their searches that aren't one of those chests, the searches scaled by the share
    of the recorder's searches that were chests (the rest are ammo boxes and such).

    A chest is credited to its opener when its update reaches the recorder, which for a far-away teammate
    can be minutes after they opened it (it comes into view later). Their search for it was seen at the
    time, so the two are paired by WHERE they happened: the chest's real position (known chest spots, or
    its own spawn point) and where the teammate stood when they searched. Unpaired chests are opens the
    search flag never showed (Nov 18 20:10: 4 of Brave-Boots' 12). Returns {name: opened} or None."""
    m = r.get("map") or {}
    chests = m.get("chests")
    if not isinstance(chests, list) or not r.get("teammates"):
        return None
    searches_r, opened_r = int(r.get("recorder_searches") or 0), int(r.get("opened") or 0)
    share = min(1.0, max(0.8, opened_r / searches_r)) if searches_r >= 10 else 0.94
    out = {}
    for t in r["teammates"]:
        name = t.get("name")
        mine = [c for c in chests if c.get("who") == name]
        cred = sorted((c for c in mine if c.get("source") in ("opener", "actor") and not c.get("broken")),
                      key=lambda c: c.get("t", 0))
        free = [c for c in mine if c.get("source") == "search"]
        for c in cred:
            where = (c.get("x"), c.get("y")) if c.get("source") == "actor" else spots.get(c.get("id") or "")
            pick = None
            if where and where[0] is not None:
                near = [(((s["x"] - where[0]) ** 2 + (s["y"] - where[1]) ** 2) ** 0.5 / 100, i)
                        for i, s in enumerate(free) if s.get("t", 0) <= c.get("t", 0) + 1]
                near = [x for x in near if x[0] <= SAME_CHEST_M]
                pick = min(near)[1] if near else None
            else:  # position unknown: only a search just before it can be the same chest
                near = [i for i, s in enumerate(free) if -1 <= c.get("t", 0) - s.get("t", 0) <= 6]
                pick = near[-1] if near else None
            if pick is not None:
                free.pop(pick)
        out[name] = len(cred) + round(len(free) * share)
    return out


FAR_M = 300.0  # a teammate this far from the recorder (metres): their broken chests don't reach the replay


def far_share(m: dict, name: str) -> float | None:
    """The share of a teammate's searches made while the recorder was over FAR_M away (map data from the
    reader's --map). Chest updates reach a replay from up to ~300 m of the recorder (Nov 20, 2026: 90% of
    3,300 teammate chests); searches come from anywhere. None: no searches to tell from."""
    import bisect

    players = {p.get("name"): p for p in (m or {}).get("players") or []}
    rec = next((p for p in players.values() if p.get("recorder")), None)
    route = [p for p in (rec or {}).get("route") or [] if isinstance(p, (list, tuple)) and len(p) >= 3]
    if not route:
        return None
    ts = [float(p[0]) for p in route]

    def at(t):
        i = bisect.bisect_left(ts, t)
        if i <= 0:
            return route[0][1], route[0][2]
        if i >= len(route):
            return route[-1][1], route[-1][2]
        a, b = route[i - 1], route[i]
        f = (t - a[0]) / ((b[0] - a[0]) or 1)
        return a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f

    searches = [c for c in m.get("chests") or [] if c.get("who") == name and c.get("source") == "search"
                and c.get("x") is not None and c.get("y") is not None]
    if not searches:
        return None
    far = 0
    for c in searches:
        t = float(c.get("t") or 0)
        if all(((x - c["x"]) ** 2 + (y - c["y"]) ** 2) ** 0.5 / 100 > FAR_M for x, y in (at(t - 5), at(t), at(t + 5))):
            far += 1
    return round(far / len(searches), 2)


def mark_far(r_or_map: dict, teammates: list) -> bool:
    """Puts "far" (far_share) on each teammate. Returns True if any was set."""
    m = r_or_map.get("map") if "map" in r_or_map else r_or_map
    done = False
    for t in teammates or []:
        f = far_share(m, t.get("name"))
        if f is not None:
            t["far"] = f
            done = True
    return done


def build_credits(entries, names: set[str], console_only: bool = False) -> dict[str, dict[str, list]]:
    """{teammate name: {match id: [opened, broken, started]}}: what replays recorded here saw each
    teammate get, plus the recorder's own chests when someone else played on this PC. Their tracker
    uses these for matches it has no replay of: a console match, or a PC that doesn't save replays.
    (It skips matches it recorded itself, and matches a credit to the game it counted live.)
    console_only: the old kind (console teammates only), for trackers from before that matching."""
    out: dict[str, dict[str, list]] = {}
    for e in entries:
        match = e.get("session") or e.get("guid")
        if not match or e.get("status") not in ("imported", "confirmed"):
            continue
        who = [(t.get("name"), t.get("opened", 0), t.get("broken", 0), t.get("bot"),
                app_model.far_broken(t) if app_model.ESTIMATE_FAR_BROKEN else 0)
               for t in e.get("teammates") or []]
        raw = e.get("raw") or {}
        if console_only:
            who = [w for w, t in zip(who, e.get("teammates") or [])
                   if (t.get("platform") or "").upper() not in PC_PLATFORMS]
        elif e.get("recorder") and norm_name(e.get("recorder")) not in names:  # someone else played on this PC
            who.append((e.get("recorder"), raw.get("opened", 0), raw.get("broken", 0), False, 0))
        for name, o, b, bot, est in who:
            n = norm_name(name)
            if not n or bot or n in names or not (o or b):
                continue
            # 4th: broken chests the replay couldn't see, estimated (trackers before 1.24 read the first three)
            out.setdefault(n, {})[match] = [int(o or 0), int(b or 0), e.get("started", 0)] + ([est] if est else [])
    return out


def _by_match(v) -> dict | None:
    """A teammate's per-match chests for a console teammate, checked: {match id: [chests, started]}."""
    if not isinstance(v, dict):
        return None
    out = {}
    for k, x in list(v.items())[:300]:
        try:
            out[str(k)[:16]] = [max(0, min(int(x[0]), 500)), int(x[1])]
        except (TypeError, ValueError, IndexError):
            continue
    return out


def merge_guest(sources: list) -> dict:
    """One console teammate as seen by several trackers (mine and teammates'): [(guest dict, online)].
    Matches are combined: one that two trackers saw counts once, at the higher count, and matches only
    one of them saw are added. A tracker that sends no per-match list (older version) can only be
    compared by total, so the result is never lower than its total."""
    best = max(sources, key=lambda s: s[0].get("total", 0))[0]
    out = dict(best, online=any(on for _, on in sources))
    lists = [s[0].get("by_match") for s in sources if s[0].get("by_match")]
    if lists:
        union: dict = {}
        for bm in lists:
            for m, (c, started) in bm.items():
                if m not in union or c > union[m][0]:
                    union[m] = [c, started]
        midnight = local_midnight()
        totals = [s[0].get("total", 0) for s in sources if not s[0].get("by_match")]
        out["total"] = max([sum(c for c, _ in union.values())] + totals)
        out["matches"] = max(len(union), out.get("matches", 0))
        out["today"] = max([sum(c for c, st in union.values() if st >= midnight)]
                           + [s[0].get("today", 0) for s in sources])
    out["game"] = max((s[0].get("game", 0) for s in sources if s[1]), default=0)
    out.pop("by_match", None)
    return out


def guest_stats(entries: dict, guests: dict[str, str], current_replay: str | None) -> dict[str, dict]:
    """Chests of console teammates who don't run the tracker, from this PC's replays.

    guests: {normalized name: name as typed}. Returns {normalized name: {"name", "platform", "game",
    "total", "matches"}}; "game" is their chests in the current game's replay (once it's been read).
    Each match counts once. Only chests they got near the recorder are in the replay.
    """
    out = {n: {"name": shown, "platform": "", "game": 0, "total": 0, "matches": 0, "today": 0, "by_match": {}}
           for n, shown in guests.items()}
    counted = set()
    midnight = local_midnight()
    for rname, e in list(entries.items()):
        if e.get("status") not in ("imported", "confirmed"):
            continue
        match = e.get("session") or e.get("guid") or rname
        for t in e.get("teammates") or []:
            n = norm_name(t.get("name"))
            if n not in out or (n, match) in counted:
                continue
            counted.add((n, match))
            g = out[n]
            chests = app_model.teammate_chests(t)  # (plus the broken chests a far-away teammate's replay misses)
            g["name"] = t.get("name") or g["name"]
            g["platform"] = t.get("platform") or g["platform"]
            g["total"] += chests
            g["matches"] += 1
            # per match (short id), so teammates' trackers that list them too can be combined without doubles
            g["by_match"][str(match)[:16]] = [chests, int(float(e.get("started") or 0))]
            if rname == current_replay or float(e.get("started") or 0) >= midnight:
                g["today"] += chests  # (the game being played counts for today too)
            if rname == current_replay:
                g["game"] = chests
    return out


# --------------------------------------------------------------------------------------
# Chest logic (pure, no I/O, unit-testable)
# --------------------------------------------------------------------------------------
class ChestLogic:
    """Counts chests from Fortnite's XP pop-up.

    The pop-up is one XP number with what it was for stacked under it, e.g.
        +2,700 XP          (counts up to the new total when something is added)
        SPRITE CHEST
        RARE CHEST X2
    Anything earned while it's up is added to it rather than making a new pop-up: a second chest
    turns "CHEST" into "CHEST X2", a chest of another kind adds its own line. So each read gives
    the number of chests the pop-up shows (`chests`: the X's of its chest lines added up), and
    every confirmed rise of that number counts. Measured over 53 matches (Nov 15-19, 2026), the
    old rule (one chest per pop-up, a new one only after 2.5 s without it) missed nearly every
    chest opened within 4 s of the one before: about 13% of all chests.

    A new pop-up starts only after reads that *saw* it gone (a read can take over a second while
    the game is busy; that alone is not the pop-up going away):
      - gone for GONE_FOR seconds and seen gone, or for SHORT_GONE and seen gone twice, or
      - seen gone, and back with fewer chests or with none of its lines (a new pop-up that came
        up quickly, e.g. "SPRITE CHEST" right after "RARE CHEST" went).
    A rise is believed when two reads agree, or when the X number and the XP agree in one read;
    a jump of more than MAX_STEP chests at once is a misread. An X number read only once (the
    pop-up went before a second read) counts once the pop-up is seen gone twice.
    """

    GONE_FOR = 1.0
    SHORT_GONE = 0.6
    DROP_GAP = 0.3   # seen gone this long, then back showing fewer chests / other lines: a new pop-up
    COMBO_GAP = 3.0  # a pop-up back this soon after the last one may carry on its X count
    LONG_GONE = 5.0  # not seen for this long: a new pop-up even without reads in between
    MAX_STEP = 4

    def __init__(self):
        self.last_seen = -1e9
        self.gone_reads = 0   # reads since last_seen that saw no pop-up
        self.shown = 0        # chests the pop-up on screen stands for, all counted
        self.carry = 0        # the previous pop-up's count, if it was up moments ago
        self.fresh = False    # the pop-up's X hasn't been read yet (it counted 1 on sight)
        self.base = None      # XP of one chest in this pop-up
        self.xp = None        # the pop-up's XP when `shown` was last raised
        self.pending = None   # a rise read once, waiting for a second read: (target, xp)
        self.labels: set[str] = set()  # the pop-up's lines, as letters only

    def _dropped(self, xp, mult, labels) -> bool:
        """Back showing fewer chests than before, or none of the same lines: a new pop-up."""
        if labels and self.labels and not any(_same_label(a, b) for a in labels for b in self.labels):
            return True
        if self.shown < 2:
            return False
        if mult and 2 <= mult < self.shown:
            return True
        return bool(not mult and xp and self.base and abs(xp - self.base) <= 0.15 * self.base)

    def _target(self, xp, mult):
        """(chests the pop-up shows, sure) for one read."""
        t_mult = mult if mult and 1 <= mult <= 30 else None
        t_xp = None
        if xp and self.base:
            t_xp = max(1, int(xp / self.base + 0.25))
            if self.xp and xp >= self.xp + 0.5 * self.base:
                t_xp = max(t_xp, self.shown + 1)  # the XP went up: at least one more chest
            elif self.xp and xp <= self.xp:
                t_xp = None  # no news (or a misread low)
        if t_mult and t_xp:
            return t_mult, t_mult == t_xp
        return (t_mult or t_xp or 1), False

    def sample(self, visible: bool, t: float, xp: int | None = None, mult: int | None = None,
               labels=None) -> int:
        """One read of the pop-up area. xp: the pop-up's XP, only when chests are all it shows;
        mult: how many chests it shows (None = 1). Returns how many new chests to count."""
        if not visible:
            if self.last_seen > -1e9:
                self.gone_reads += 1
                if self.gone_reads == 2 and self.pending and self.pending[1] is None:
                    # an X read once just before the pop-up went: believe it
                    step = min(self.MAX_STEP, self.pending[0] - self.shown)
                    self.shown, self.pending = self.pending[0], None
                    return max(0, step)
            return 0
        labels = set(labels or ())
        gap = t - self.last_seen
        seen_gone = self.gone_reads
        self.last_seen, self.gone_reads = t, 0
        added = 0
        new = (gap >= self.LONG_GONE
               or (gap >= self.GONE_FOR and seen_gone >= 1)
               or (gap >= self.SHORT_GONE and seen_gone >= 2)
               or (gap >= self.DROP_GAP and seen_gone >= 1 and self._dropped(xp, mult, labels)))
        if new and self.pending and self.pending[1] is None and seen_gone < 2:
            added += max(0, min(self.MAX_STEP, self.pending[0] - self.shown))  # see above
        if new:
            # a new pop-up: at least one chest
            self.carry = self.shown if self.GONE_FOR <= gap < self.COMBO_GAP else 0
            self.shown, self.fresh, self.base, self.xp, self.pending = 1, True, None, None, None
            self.labels = set()
            added += 1
        self.labels |= labels
        if xp and self.base is None:
            self.base = xp / (mult if mult and 1 <= mult <= 30 else 1)
            self.xp = self.base  # the XP of the one chest counted so far
        target, sure = self._target(xp, mult)
        if self.fresh and target == 1:
            self.fresh, self.carry = False, 0  # a plain "CHEST": a new count, whatever came before
        if target <= self.shown:
            return added  # no news (a read that missed the X keeps a pending rise waiting)
        if self.fresh and self.carry and target > self.carry:
            step = target - self.carry - 1  # the last pop-up's X carried on into this one
        else:
            step = target - self.shown
        if step > self.MAX_STEP:
            self.pending = None
            return added
        # the XP counts up for a moment when something is added: agree on the target, and on
        # the XP too when the XP is all there is to go on
        key = (target, xp if not mult else None)
        if not (sure or self.pending == key):
            self.pending = key
            return added
        added += step
        self.shown, self.fresh, self.pending = target, False, None
        if xp:
            self.xp = xp
            if mult and sure:
                self.base = xp / mult
        return added


def _has_word(text: str, words) -> bool:
    low = text.lower()
    if any(w in low for w in words):
        return True
    # tolerate small misreads like "Chcst"
    for token in re.findall(r"[a-z]+", low):
        for w in words:
            if len(w) >= 4 and len(token) >= len(w) - 1 and difflib.SequenceMatcher(None, token, w).ratio() >= 0.8:
                return True
    return False


_MULT_RE = re.compile("[x×*]\\s*(\\d{1,2})\\b", re.I)
_MULT_TOKEN_RE = re.compile("^\\s*[x×*]\\s*(\\d{1,2})\\s*$", re.I)


def _label_key(text: str) -> str:
    """A pop-up line as letters only: "RARE CHEST X2" / "RARECHESTX2" -> "rarechest"."""
    return re.sub(r"[^a-z]", "", _MULT_RE.sub(" ", text.lower()))


def _same_label(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def parse_mult(text: str) -> int | None:
    """The 2 of "CHEST X2" (chests merged into one pop-up line), or None."""
    low = text.lower()
    m = _MULT_RE.search(low, max(0, low.find("ch")))
    if m and 2 <= int(m.group(1)) <= 30:
        return int(m.group(1))
    return None


def parse_xp(text: str) -> int | None:
    """The 1200 of "+ 1,200XP" / "+600xP" / "Chest +600 XP" / "+380.", or None."""
    low = _MULT_RE.sub(" ", text.lower())
    m = re.search(r"\+?\s*(\d[\d,.o ]*\d|\d)\s*(?:x\s*p|$)", low) or re.search(r"\+\s*(\d[\d,.o]*)", low)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1).replace("o", "0"))
    if not 2 <= len(digits) <= 6:
        return None
    return int(digits) or None


def _is_chest_line(text: str, cfg) -> bool:
    low = text.lower()
    if any(w in low for w in cfg["ignore_words"]) or not _has_word(text, cfg["chest_words"]):
        return False
    if re.search(r"chests|\d+\s*/\s*\d+", low):
        return False  # quest text ("Search 5 chests", "3/10"); the pop-up says "CHEST"
    if len(re.findall(r"[a-z]{2,}", re.sub(r"[+\d,.]+\s*x\s*p", " ", low))) > 3:
        return False  # a sentence ("Opening Chest grants increased pickaxe speed..."), not the pop-up
    return True


def read_xp_popup(lines, cfg) -> dict | None:
    """lines: [(text, (x1, y1, x2, y2))]. The XP pop-up, if it shows a chest:
    {"box", "xp", "chests" (its chest lines' X's added up), "pure" (chests are all it shows),
     "labels" (its lines), "text"}. Real example (2576x1048 window):
        "+2,700xP"       (165, 154, 310, 185)
        "SPRITE CHEST"   (214, 185, 329, 209)
        "RARE CHEST X2"  (215, 215, 342, 238)"""
    best = None
    for text, box in lines:
        if not _MULT_TOKEN_RE.match(text) and _is_chest_line(text, cfg) and parse_xp(text) and (
                _has_word(_MULT_RE.sub(" ", text), cfg["xp_words"]) or "+" in text):
            # "Chest +600 XP" read as one line
            found = {"box": box, "xp": parse_xp(text), "chests": parse_mult(text) or 1, "pure": True,
                     "labels": [_label_key(re.sub(r"[+\d,.]+\s*x\s*p", " ", text, flags=re.I))], "text": text}
        else:
            found = _popup_under(text, box, lines, cfg)
        if found and (best is None or found["chests"] > best["chests"]):
            best = found
    return best


def _popup_under(xp_text, xp_box, lines, cfg) -> dict | None:
    """The pop-up whose XP line is (xp_text, xp_box): its lines are stacked right under it."""
    xp = parse_xp(xp_text)
    if not xp or not ("+" in xp_text or _has_word(xp_text, cfg["xp_words"])) or _MULT_TOKEN_RE.match(xp_text):
        return None
    if re.search(r"[a-z]{3,}", re.sub(r"x\s*p", " ", xp_text.lower())):
        return None  # words on the XP line: not the pop-up's number
    x1, y1, x2, y2 = xp_box
    w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
    bottom = y2
    stack = []
    rest = sorted((b[1], t, b) for t, b in lines if b != xp_box)
    for _, t, b in rest:
        bx1, by1, bx2, by2 = b
        if by1 < (y1 + y2) / 2 or by1 - bottom > 0.6 * h:
            continue  # above the XP, or too far below the line before it
        if not (x1 - 0.3 * w <= bx1 <= x2 and bx2 <= x2 + max(0.6 * w, 2.5 * h)):
            continue  # not under the XP
        if _MULT_TOKEN_RE.match(t):
            continue  # "X2" on its own: joined to the line it ends, below
        stack.append([t, b])
        bottom = by2
    if not stack:
        return None
    for t, (bx1, by1, bx2, by2) in lines:  # "X2" read as a word of its own, just right of its line
        m = _MULT_TOKEN_RE.match(t)
        if m:
            for s in stack:
                sx1, sy1, sx2, sy2 = s[1]
                lh = max(1.0, sy2 - sy1)
                if abs((by1 + by2) / 2 - (sy1 + sy2) / 2) <= 0.8 * lh and sx2 - lh <= bx1 <= sx2 + 4 * lh:
                    s[0] = f"{s[0]} X{m.group(1)}"
                    s[1] = (sx1, min(sy1, by1), max(sx2, bx2), max(sy2, by2))
    chest_lines = [t for t, _ in stack if _is_chest_line(t, cfg)]
    if not chest_lines:
        return None
    boxes = [xp_box] + [b for _, b in stack]
    return {"box": (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)),
            "xp": xp, "chests": sum(parse_mult(t) or 1 for t in chest_lines),
            "pure": len(stack) == len(chest_lines) == 1, "labels": [_label_key(t) for t, _ in stack],
            "text": " | ".join([xp_text] + [t for t, _ in stack])}


def find_chest_popup(lines, cfg):
    """lines: [(text, (x1, y1, x2, y2))]. Returns the box of a chest XP pop-up or None."""
    found = read_xp_popup(lines, cfg)
    return found["box"] if found else None


def sample_popup(logic: ChestLogic, popup: dict | None, t: float) -> int:
    """Feed one read (a read_xp_popup() result, None = no pop-up) to the counting."""
    if not popup:
        return logic.sample(False, t)
    return logic.sample(True, t, popup["xp"] if popup["pure"] else None,
                        popup["chests"] if popup["chests"] > 1 else None, popup["labels"])


# --------------------------------------------------------------------------------------
# Screen reading
# --------------------------------------------------------------------------------------
class Reader:
    """Wraps the OCR engine. Loaded lazily because it takes a couple of seconds."""

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self.ocr = RapidOCR(
            use_cls=False,
            intra_op_num_threads=2,
            inter_op_num_threads=1,
            det_limit_type="max",
            det_limit_side_len=2000,
        )

    def lines(self, rgb_array):
        res, _ = self.ocr(rgb_array)
        out = []
        for box, text, score in res or []:
            try:
                score = float(score)
            except Exception:
                score = 0.0
            if score >= 0.5:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                out.append((text, (min(xs), min(ys), max(xs), max(ys))))
        return out


def region_around(box, mon_w, mon_h):
    """Screen area to keep reading once the pop-up has been found (room for it to move a bit)."""
    x1, y1, x2, y2 = box
    h = max(12.0, y2 - y1)
    pad_x, pad_y = max(150.0, 3 * h), max(110.0, 3 * h)
    return [round(max(0.0, (x1 - pad_x) / mon_w), 4), round(max(0.0, (y1 - pad_y) / mon_h), 4),
            round(min(1.0, (x2 + pad_x) / mon_w), 4), round(min(1.0, (y2 + pad_y) / mon_h), 4)]


def front_window() -> tuple[str, str]:
    """(class name, title) of the window in front. Never touches the game process."""
    if not IS_WIN:
        return "", ""
    from ctypes import wintypes

    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = wintypes.HWND
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return "", ""
    cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(128)
    u.GetClassNameW(wintypes.HWND(hwnd), cls, 64)
    u.GetWindowTextW(wintypes.HWND(hwnd), title, 128)
    return cls.value, title.value


def fortnite_in_front() -> bool:
    """True if the window in front is Fortnite: an Unreal Engine window titled "Fortnite".
    (Both are needed: a browser tab about Fortnite has "fortnite" in its title too.)"""
    if not IS_WIN:
        return True
    try:
        cls, title = front_window()
        return cls == "UnrealWindow" and title.strip().lower().startswith("fortnite")
    except Exception:
        return True


def fortnite_window_rect() -> dict | None:
    """Screen rectangle of Fortnite's window ({left, top, width, height}), or None if it isn't open.
    Found by window class and title only; the game process is never touched."""
    if not IS_WIN:
        return None
    from ctypes import wintypes

    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(128)
        u.GetClassNameW(h, cls, 64)
        if cls.value == "UnrealWindow" and u.IsWindowVisible(h) and not u.IsIconic(h):
            u.GetWindowTextW(h, title, 128)
            if title.value.strip().lower().startswith("fortnite"):
                r = wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                if r.right - r.left > 200 and r.bottom - r.top > 200:
                    found.append({"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top})
                    return False
        return True

    u.EnumWindows(visit, 0)
    return found[0] if found else None


# Where the "+600 XP / CHEST" pop-up appears, as fractions of the Fortnite window. Measured in a real
# match at 2576x1048 (pop-up at x 0.61-0.66, y 0.29-0.34) and in screenshots at 2000x843 (the same),
# with room around it for other screen shapes. Reading only this area is fast (a fraction of a
# second) and can't be fooled by text elsewhere. The exact spot is learned from the first pop-up.
DEFAULT_POPUP_AREA = [0.47, 0.14, 0.83, 0.52]


def frame_changed(a, b, level: int = 24, share: float = 0.003) -> bool:
    """True if two small brightness pictures (see Detector.thumb) differ: more than `share` of
    their pixels changed by more than `level` grey levels. Far cheaper than reading the text."""
    import numpy as np

    if a is None or b is None or a.shape != b.shape:
        return True
    return float((np.abs(a.astype(np.int16) - b.astype(np.int16)) > level).mean()) > share


class FrameRecorder:
    """Debug aid, on with "debug_frames": true (or "debug": true) in config.json. Saves the
    pop-up area as JPEG pictures from PRE seconds before each chest pop-up until POST seconds
    after it, with what was read in each one (frames.jsonl), in %APPDATA%\\ChestTracker\\debug_frames.
    Only the small area that's read anyway, only on this PC; the oldest are deleted past max_mb."""

    PRE, POST = 1.5, 2.0

    def __init__(self, root: Path, max_mb: int = 500):
        import collections
        import queue

        self.root, self.max_bytes = root, max_mb * 1024 * 1024
        self.buf = collections.deque()
        self.dir: Path | None = None
        self.until = -1e9
        self.n = 0
        self.q = queue.Queue(maxsize=300)
        threading.Thread(target=self._work, daemon=True).start()

    def add(self, t: float, img, rec: dict, keep: bool):
        if self.dir is None:
            self.buf.append((t, img, rec))
            while self.buf and self.buf[0][0] < t - self.PRE:
                self.buf.popleft()
            if not keep:
                return
            self.dir = self.root / dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
            self.n = 0
            self._trim()
            while self.buf:
                self._put(self.buf.popleft())
        else:
            self._put((t, img, rec))
        if keep:
            self.until = t + self.POST
        elif t > self.until:
            self.dir = None

    def _put(self, item):
        self.n += 1
        try:
            self.q.put_nowait((self.dir, self.n) + tuple(item))
        except Exception:
            pass  # disk too slow: drop the picture rather than slow down counting

    def _work(self):
        from PIL import Image

        while True:
            d, n, t, img, rec = self.q.get()
            try:
                d.mkdir(parents=True, exist_ok=True)
                name = f"{n:04d}.jpg"
                Image.fromarray(img).save(d / name, quality=85)
                with open(d / "frames.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(rec, file=name)) + "\n")
            except Exception as e:
                log.warning("debug frame not saved: %s", e)

    def _trim(self):
        import shutil

        try:
            dirs = sorted(p for p in self.root.iterdir() if p.is_dir())
            sizes = [sum(f.stat().st_size for f in p.iterdir()) for p in dirs]
            total = sum(sizes)
            for p, s in zip(dirs, sizes):
                if total <= self.max_bytes:
                    break
                shutil.rmtree(p, ignore_errors=True)
                total -= s
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("could not trim debug frames: %s", e)


def _jsonable_lines(lines):
    return [[t, [round(float(v), 1) for v in b]] for t, b in lines]


def _jsonable_popup(popup):
    return dict(popup, box=[round(float(v), 1) for v in popup["box"]]) if popup else None


class Detector(threading.Thread):
    """Looks at the pop-up area checks_per_second times a second, cheaply: the text is read (OCR, the
    costly part) only when the picture changed since the last read, and mostly only in the small
    spot where the pop-up shows (its first sightings were all within a few pixels of each other
    in a real match). Reading that spot takes ~35 ms instead of ~95 ms for the whole area (a
    third of the CPU), so it can be read up to FAST_READS times a second and still cost less:
    a chest counts sooner (Nov 19 match frames: same pop-ups found in 834 of 837 frames, 3 more).
    The whole area is still read every FULL_EVERY seconds while no pop-up is up, in case it shows
    somewhere else (the spot then moves there), and at most scans_per_second times a second
    before the spot is known."""

    FULL_SCREEN_INTERVAL = 1.2  # seconds between whole-screen reads (F8 calibration only)
    FAST_READS = 5
    FULL_EVERY = 3.0
    # room around the pop-up's usual box, in multiples of a one-line pop-up's height (~57 px at 1048
    # high): left, top, right ("X2" makes it wider), bottom
    SPOT_PAD = (0.9, 0.8, 1.4, 0.8)
    PERF_EVERY = 600  # seconds between "detector:" lines in tracker.log

    def __init__(self, app: "App"):
        super().__init__(daemon=True)
        self.app = app
        self.counter = ChestLogic()
        self.reader: Reader | None = None
        self.calibrate_requested = threading.Event()
        self.ready = False
        self.error = ""
        self.frames: FrameRecorder | None = None
        self._seen: dict = {}     # (crop offset, crop size) -> (small picture of the last read, what it found)
        self._last_read = -1e9
        self._last_full = -1e9
        self.focus = None         # the last pop-up's box, in scan-area pixels
        self.home = None          # where pop-ups first show (the last new one's box), in scan-area pixels
        self._unit = None         # height of a one-line pop-up there, px
        self._perf = {"checks": 0, "reads": 0, "read_s": 0.0, "since": time.monotonic()}

    def _area(self, sct) -> dict:
        """What to read: Fortnite's own window (on whichever screen it is), else the main screen.
        (sct.monitors[1] is not always the main screen: with two screens it can be the other one.)"""
        rect = fortnite_window_rect()
        if rect:
            return rect
        mons = sct.monitors[1:] or sct.monitors
        return next((m for m in mons if m.get("is_primary")), mons[0])

    def _grab(self, sct, region_frac=None):
        import numpy as np

        mon = self._area(sct)
        if region_frac is None:
            box = {"left": mon["left"], "top": mon["top"], "width": mon["width"], "height": mon["height"]}
        else:
            l, t, r, b = region_frac
            box = {
                "left": mon["left"] + int(l * mon["width"]),
                "top": mon["top"] + int(t * mon["height"]),
                "width": max(32, int((r - l) * mon["width"])),
                "height": max(32, int((b - t) * mon["height"])),
            }
        shot = sct.grab(box)
        img = np.asarray(shot)[:, :, :3][:, :, ::-1]  # BGRA -> RGB
        return np.ascontiguousarray(img), mon, box

    def _remember_region(self, popup_box, mon, grab_box):
        # popup_box is relative to the grabbed image; convert to monitor coordinates
        ox, oy = grab_box["left"] - mon["left"], grab_box["top"] - mon["top"]
        x1, y1, x2, y2 = popup_box
        self.app.cfg["scan_region"] = region_around((x1 + ox, y1 + oy, x2 + ox, y2 + oy), mon["width"], mon["height"])
        save_json(CONFIG_PATH, self.app.cfg)
        log.info("chest pop-up area set to %s", self.app.cfg["scan_region"])

    @staticmethod
    def has_white_text(img, level: int = 215, share: float = 0.002) -> bool:
        """True if enough pixels are near-white (all of R, G, B above `level`), like the pop-up's
        "+600 XP". Every other pixel, ~0.3 ms."""
        small = img[::2, ::2]
        return int((small.min(axis=2) > level).sum()) >= max(10, share * small.shape[0] * small.shape[1])

    @staticmethod
    def thumb(img):
        """Every other pixel's brightness: enough to see text change, ~0.3 ms."""
        return img[::2, ::2].max(axis=2)

    def _home(self, img, cfg):
        """Where pop-ups show, in scan-area pixels, or None if not known."""
        if self.home:
            return self.home
        if not cfg.get("scan_region"):
            return None
        # region_around() put the pop-up it was learned from in the middle, 150 / 110 px in
        H, W = img.shape[:2]
        box = (150, 110, W - 150, H - 110)
        return box if box[2] - box[0] >= 40 and box[3] - box[1] >= 20 else None

    def _crop_around(self, img, box):
        """(the part of img around box with SPOT_PAD room, its offset)."""
        x1, y1, x2, y2 = box
        u = self._unit or max(30.0, min(120.0, y2 - y1))
        l, t, r, b = (k * u for k in self.SPOT_PAD)
        H, W = img.shape[:2]
        cx1, cy1 = max(0, int(x1 - l)), max(0, int(y1 - t))
        cx2, cy2 = min(W, int(x2 + r)), min(H, int(y2 + b))
        if cx2 - cx1 < 32 or cy2 - cy1 < 32:
            return img, (0, 0)
        return img[cy1:cy2, cx1:cx2], (cx1, cy1)

    def _pick_crop(self, img, now: float, cfg, active: bool):
        """(what to look at, its offset, how soon it may be read again)."""
        fast = 1.0 / self.FAST_READS
        if active and self.focus:
            return (*self._crop_around(img, self.focus), fast)  # around the pop-up that's up
        home = self._home(img, cfg)
        if home and now - self._last_full < self.FULL_EVERY:
            return (*self._crop_around(img, home), fast)  # the spot where pop-ups show
        return img, (0, 0), (fast if home else 1.0 / max(1, cfg.get("scans_per_second", 3)))

    def check(self, img, now: float, cfg) -> tuple[dict | None, int, dict]:
        """One look at the scan area. Returns (the chest pop-up seen, or None; chests to add;
        a record for the debug frames)."""
        active = now - self.counter.last_seen < ChestLogic.COMBO_GAP
        crop, (ox, oy), min_gap = self._pick_crop(img, now, cfg, active)
        full = crop is img
        thumb = self.thumb(crop)
        key = ((ox, oy), crop.shape)
        before = self._seen.get(key)
        changed = before is None or frame_changed(thumb, before[0])
        rec = {"wall": round(time.time(), 3), "t": round(now, 3), "crop": [ox, oy, crop.shape[1], crop.shape[0]],
               "changed": changed, "read": False}
        if changed and not active and not full and not self.has_white_text(crop):
            # Waiting for a pop-up, and the spot has no bright white: no pop-up there (a new pop-up's
            # XP is white: 754+ near-white pixels at every first sighting in the Nov 19 frames; only
            # one fading out is dimmer). No need to read it: most looks end here.
            if len(self._seen) > 8:
                self._seen.clear()
            self._seen[key] = (thumb, None)
            rec["white"] = False
            return None, sample_popup(self.counter, None, now), rec
        if changed and now - self._last_read >= min_gap - 0.02:
            t0 = time.perf_counter()
            lines = [(t, (x1 + ox, y1 + oy, x2 + ox, y2 + oy)) for t, (x1, y1, x2, y2) in self.reader.lines(crop)]
            self._perf["reads"] += 1
            self._perf["read_s"] += time.perf_counter() - t0
            popup = read_xp_popup(lines, cfg)
            if len(self._seen) > 8:
                self._seen.clear()
            self._seen[key] = (thumb, popup)
            self._last_read = now
            if full:
                self._last_full = now
            rec.update(read=True, lines=_jsonable_lines(lines))
            if cfg.get("debug") and lines:
                log.debug("saw %s", [t for t, _ in lines])
            added = sample_popup(self.counter, popup, now)
        elif not changed:
            popup = before[1]  # the same picture as the last read of this spot
            added = sample_popup(self.counter, popup, now)
        else:
            popup, added = None, 0  # changed, but read recently: no news until the next read
        if popup:
            if not active:  # a new pop-up: this is where they show
                self.home = popup["box"]
                self._unit = max(30.0, min(120.0, popup["box"][3] - popup["box"][1]))
            self.focus = popup["box"]
        rec.update(popup=_jsonable_popup(popup), added=added)
        return popup, added, rec

    def _log_perf(self, now: float):
        p = self._perf
        if now - p["since"] >= self.PERF_EVERY:
            if p["checks"]:
                log.info("detector: %d checks, %d reads (%.0f ms each) in %.0f s", p["checks"], p["reads"],
                         1000 * p["read_s"] / max(1, p["reads"]), now - p["since"])
            self._perf = {"checks": 0, "reads": 0, "read_s": 0.0, "since": now}

    def run(self):
        try:
            import mss

            self.reader = Reader()
        except Exception as e:
            self.error = f"Screen reader failed to start: {e}"
            log.exception("detector start failed")
            self.app.toast("Chest detection could not start. See tracker.log.")
            return
        self.ready = True
        log.info("detector ready")
        self.app.toast("Watching for chests", 6)
        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            while True:
                t0 = time.monotonic()
                self._log_perf(t0)
                cfg = self.app.cfg
                manual_cal = self.calibrate_requested.is_set()
                if not manual_cal and cfg.get("only_when_fortnite", True) and not fortnite_in_front():
                    time.sleep(0.5)  # don't read other windows (or this very text) as chest pop-ups
                    continue
                if not manual_cal and cfg.get("only_in_match", True) and not self.app.match_in_progress():
                    # lobby, placement screen, spectating: quest text like "Search chests +XP" (or the
                    # player you're watching) must not count
                    time.sleep(0.5)
                    continue
                self.calibrate_requested.clear()
                full = manual_cal  # whole screen only when asked with F8
                learning = full or not cfg.get("scan_region")
                area = None if full else (cfg.get("scan_region") or DEFAULT_POPUP_AREA)
                want_frames = bool(cfg.get("debug_frames") or cfg.get("debug"))
                if want_frames and self.frames is None:
                    self.frames = FrameRecorder(DATA_DIR / "debug_frames", int(cfg.get("debug_frames_mb", 500)))
                elif not want_frames:
                    self.frames = None
                try:
                    img, mon, grab_box = self._grab(sct, area)
                    now = time.monotonic()
                    self._perf["checks"] += 1
                    if full:
                        popup = read_xp_popup(self.reader.lines(img), cfg)
                        added = sample_popup(self.counter, popup, now)
                        rec = None
                    else:
                        popup, added, rec = self.check(img, now, cfg)
                    if popup and learning:
                        self._remember_region(popup["box"], mon, grab_box)
                        self.focus = self.home = self._unit = None  # boxes were in the old area's pixels
                        self._seen.clear()
                        if manual_cal:
                            self.app.toast("Calibrated. Chests count automatically now.")
                    elif manual_cal:
                        self.app.toast("No chest XP pop-up on screen. Press F8 while one shows.")
                    if added:
                        log.info("chest pop-up: +%d (%s)", added, popup["text"] if popup else "")
                        self.app.stats.add(added)
                        self.app.flash()
                    if self.frames is not None and rec is not None:
                        self.frames.add(now, img, rec, keep=bool(popup) or added > 0)
                except Exception:
                    log.exception("scan failed")
                    time.sleep(1)
                if full:
                    interval = self.FULL_SCREEN_INTERVAL
                else:
                    interval = 1.0 / max(1, cfg.get("checks_per_second", 10))
                wait = interval - (time.monotonic() - t0)
                if wait > 0:
                    time.sleep(wait)


# --------------------------------------------------------------------------------------
# Hotkeys (polled, no keyboard hooks)
# --------------------------------------------------------------------------------------
class Hotkeys(threading.Thread):
    """F6/F7/F8/F10 are polled (they still reach other apps). F9 and F11 are registered with Windows:
    a registered hotkey lets this app bring its settings window to the front while Fortnite is in
    front and holding the mouse, which a polled key can't do."""

    def __init__(self, app: "App"):
        super().__init__(daemon=True)
        self.app = app
        self.prev: dict[int, bool] = {}

    def run(self):
        if not IS_WIN:
            return
        from ctypes import wintypes

        polled = {
            0x75: self.app.manual_add,                          # F6
            0x76: self.app.undo,                                # F7
            0x77: self.app.request_calibrate,                   # F8
            0x79: lambda: self.app.stats.new_game("F10"),       # F10
        }
        registered = {1: (0x78, self.app.toggle_expanded),     # F9: show/hide everyone's chests
                      2: (0x7A, self.app.open_settings)}       # F11: settings
        u = ctypes.WinDLL("user32", use_last_error=True)
        active = {}
        for hid, (vk, fn) in registered.items():
            if u.RegisterHotKey(None, hid, 0x4000, vk):  # MOD_NOREPEAT
                active[hid] = fn
            else:
                log.warning("hotkey %#x is taken by another app; polling it instead", vk)
                polled[vk] = fn
        threading.Thread(target=self._poll, args=(polled,), daemon=True).start()
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in active:  # WM_HOTKEY
                self.app.call_soon(active[msg.wParam])

    def _poll(self, keys):
        user32 = ctypes.windll.user32
        while True:
            for vk, fn in keys.items():
                down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                if down and not self.prev.get(vk):
                    self.app.call_soon(fn)
                self.prev[vk] = down
            time.sleep(1 / 30)


# --------------------------------------------------------------------------------------
# Match start / squad out / back in the lobby, from Fortnite's own log file
# --------------------------------------------------------------------------------------
FORTNITE_LOG = Path(os.environ.get("LOCALAPPDATA") or "") / "FortniteGame" / "Saved" / "Logs" / "FortniteGame.log"

# Lines Fortnite writes (checked in its logs, Nov 2026):
LOG_JOIN = re.compile(r"LogLoad: LoadMap: \d{1,3}(?:\.\d{1,3}){3}:\d+/")  # loading into a match server's map
LOG_LOBBY = re.compile(r"LogLoad: LoadMap: /Game/Maps/Frontend")          # back to the lobby
LOG_PHASE = re.compile(r"EAthenaGamePhase::(\w+)\. PhaseStep")            # Warmup, Aircraft (bus), SafeZones...
# the placement screen ("#4"), shown when your whole squad is out or the match is won
LOG_RESULTS = re.compile(r"AddWidgetHost: host=PlacementOverlay\S* tag=DynamicUI\.HUD\.Fort\.Athena\.PostMatch\."
                         r"|GameState in EndGame phase")
LOG_CLOSED = re.compile(r"(?:^|\])Log file closed")                       # Fortnite quit
# where the camera is (seen in a real match, Nov 19, 2026):
LOG_BUS_IN = re.compile(r"LogFort: EnterAircraft:")                          # boarded the battle bus
LOG_BUS_OUT = re.compile(r"LogFort: ExitAircraft:")                          # jumped
LOG_SKYDIVE = re.compile(r"TSkydivingCVarSwitch: Adjusting s\.AsyncLoadingPrecachePriority to (\d+)")  # 2 = falling, 0 = landed
LOG_VIEW = re.compile(r"LogFortViewTarget: \[\w+\] trying to set view target from (\S+) to (\S+)")  # Pawn:Name / the bus
LOG_DEAD = re.compile(r"LogSpectateAfterDeathViewTarget: \[\w+\] \[([^\]]+)\]")  # I'm out (spectating comes next)


class GameLog(threading.Thread):
    """Follows FortniteGame.log, which Fortnite writes as it goes, to know exactly when a match
    starts, when this player's squad is out (the placement screen) and when they're back in the lobby.

    state: "unknown" (no log read yet), "lobby", "match" (from loading in until the placement
    screen), "results" (placement screen / spectating after the squad is out), "spectating" (watching
    a party member's match from the lobby), "closed" (Fortnite quit).
    """

    TAIL = 16 * 2 ** 20   # on start, read this much of the end of the log to know where we are
    QUIET = 300           # seconds: a log that stopped growing mid-match (Fortnite crashed) proves nothing

    def __init__(self, app, path: Path = FORTNITE_LOG):
        super().__init__(daemon=True)
        self.app = app
        self.path = path
        self.state = "unknown"
        self.phase = ""
        self.last_growth = -1e9

    def feed(self, line: str) -> str | None:
        """Moves the state on by one log line. Returns what happened: "join", "spectate", "results"
        (squad out), "left" (back to the lobby or Fortnite closed before the placement screen),
        "lobby", "closed", or None."""
        if LOG_JOIN.search(line):
            self.view, self.view_name = "", ""
            if "SpectatorOnly=1" in line:
                self.state = "spectating"
                return "spectate"
            self.state, self.phase = "match", ""
            return "join"
        if self.state == "match":
            self._camera(line)
        if LOG_LOBBY.search(line):
            event = "left" if self.state == "match" else "lobby"
            self.state = "lobby"
            return event
        if LOG_CLOSED.search(line):
            event = "left" if self.state == "match" else "closed"
            self.state = "closed"
            return event
        if self.state == "match":
            if LOG_RESULTS.search(line):
                self.state = "results"
                return "results"
            m = LOG_PHASE.search(line)
            if m:
                self.phase = m.group(1)
        return None

    view = ""        # "bus", "skydive", "self" (my character, on the ground), "dead", "spectating" or "" (before the bus)
    view_name = ""   # who I'm spectating
    me_in_log = ""   # my character's name as the log writes it

    def _camera(self, line: str):
        """Follows where the camera is, so the live map only draws my own route on the ground."""
        if LOG_BUS_IN.search(line):
            self.view = "bus"
            return
        if LOG_BUS_OUT.search(line):
            self.view = "skydive"
            return
        m = LOG_SKYDIVE.search(line)
        if m:
            if m.group(1) != "0":
                self.view = "skydive"             # falling (also after a reboot or a launch)
            elif self.view in ("skydive", "bus"):
                self.view = "self"                # landed
            return
        m = LOG_DEAD.search(line)
        if m:
            self.me_in_log = self.me_in_log or m.group(1)
            if self.view != "spectating":
                self.view = "dead"
            return
        m = LOG_VIEW.search(line)
        if m:
            src, dst = m.group(1), m.group(2)
            if src.startswith("Pawn:") and src == dst and self.view not in ("dead", "spectating"):
                self.me_in_log = self.me_in_log or src[5:]  # (it re-targets my own character now and then)
            if not dst.startswith("Pawn:"):
                return                            # the bus and such
            who = dst[5:]
            mine = {norm_name(n) for n in (self.app.cfg.get("fortnite_names") if hasattr(self.app, "cfg") else None) or []}
            if self.me_in_log:
                mine.add(norm_name(self.me_in_log))
            if mine and norm_name(who) not in mine:
                self.view, self.view_name = "spectating", who
            elif self.view in ("spectating", "dead"):
                self.view, self.view_name = "self", ""  # back on my own character (rebooted)

    def tracking_ok(self) -> bool:
        """True while the screen shows my own character on the ground in a match: the live map records
        my route only then (not on the bus, skydiving, dead, or spectating a teammate)."""
        return self.match_state() == "match" and self.view == "self"

    def match_state(self) -> str | None:
        """The current state, or None when the log can't tell (not read yet, or it went quiet mid-match)."""
        if self.state == "unknown":
            return None
        if self.state == "match" and time.monotonic() - self.last_growth > self.QUIET:
            return None
        return self.state

    def _read(self, f, start: int, live: bool) -> int:
        f.seek(start)
        data = f.read()
        end = start + len(data)
        cut = data.rfind(b"\n")
        if cut < 0:
            return start  # no complete line yet
        for raw in data[:cut].split(b"\n"):
            event = self.feed(raw.decode("utf-8", "ignore"))
            if event and live:
                self._dispatch(event)
        if end > start:
            self.last_growth = time.monotonic()
        return start + cut + 1

    def _dispatch(self, event: str):
        log.info("Fortnite log: %s", event)
        try:
            if event == "join":
                self.app.on_match_start()
            elif event in ("results", "left"):
                self.app.on_match_over(squad_out=event == "results")
        except Exception:
            log.exception("match event %s failed", event)

    def run(self):
        pos = None
        while True:
            try:
                with open(self.path, "rb") as f:
                    size = f.seek(0, 2)  # (Windows may not update the file's listed size while it's written)
                    if pos is None or size < pos:
                        # first look, or Fortnite started a new log: catch up without acting on old lines
                        first = pos is None
                        pos = self._read(f, max(0, size - self.TAIL) if first else 0, live=not first)
                        if self.state == "unknown":
                            self.state = "lobby"
                        if first and time.time() - os.path.getmtime(self.path) > self.QUIET:
                            self.last_growth = -1e9  # an old log (Fortnite isn't running): wait for it to grow
                        log.info("Fortnite log: %s", self.state + (f" ({self.phase})" if self.state == "match" else ""))
                    elif size > pos:
                        pos = self._read(f, pos, live=True)
            except FileNotFoundError:
                pass
            except Exception:
                log.exception("log watch failed")
            time.sleep(1)


# --------------------------------------------------------------------------------------
# Replays: confirm each match, find broken chests, import past matches
# --------------------------------------------------------------------------------------
REPLAY_MAGIC = 0x1CA2E27F


def read_replay_header(path: Path) -> dict | None:
    """Reads the small unencrypted file header of a .replay (UE FLocalFileReplayInfo).

    Returns {"live", "length_ms", "started"} (started = epoch seconds), or None if it isn't a replay.
    """
    try:
        with open(path, "rb") as f:
            buf = f.read(4096)
        off = 0

        def take(fmt):
            nonlocal off
            vals = struct.unpack_from(fmt, buf, off)
            off += struct.calcsize(fmt)
            return vals

        magic, file_version = take("<II")
        if magic != REPLAY_MAGIC:
            return None
        if file_version >= 6:  # custom version list: count, then (guid, int32) pairs
            (n,) = take("<i")
            off += n * 20
        length_ms, _net_version, _changelist = take("<III")
        (name_len,) = take("<i")
        off += -name_len * 2 if name_len < 0 else name_len
        (live,) = take("<I")
        started = None
        if file_version >= 3:
            (ticks,) = take("<q")
            if ticks > 0:
                # .NET ticks of local time (it matches the "UnsavedReplay-yyyy.MM.dd-HH.mm.ss" file name)
                started = (dt.datetime(1, 1, 1) + dt.timedelta(microseconds=ticks // 10)).timestamp()
        return {"live": live != 0, "length_ms": length_ms, "started": started}
    except (OSError, struct.error, OverflowError, ValueError):
        return None


def season_start_epoch(text: str) -> float:
    try:
        return dt.datetime.fromisoformat(str(text)).timestamp()
    except ValueError:
        return 0.0


def run_reader(exe: Path, replay: Path, timeout: float = 600, with_map: bool = False, live: bool = False,
               squad: bool = False, background: bool = False) -> dict:
    """Runs the replay reader on one file and returns its JSON result. with_map: also routes and chest
    positions for the map window (read in the same pass instead of reading the file twice). squad: also
    the routes of the recorder's own squad only (for the live map; nothing about other teams).
    background: a match is being played: lowest priority, Windows background mode (disk and memory too)
    and one CPU core (the reader sets those itself on CT_BACKGROUND), so the game keeps its frames."""
    flags = 0
    env = None
    if IS_WIN:
        # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS (IDLE_PRIORITY_CLASS in the background): never slow the game
        flags = 0x08000000 | (0x00000040 if background else 0x00004000)
    if background:
        env = dict(os.environ, CT_BACKGROUND="1")
    args = [str(exe), "--map", str(replay)] if with_map else [str(exe), str(replay)]
    if live:
        args.insert(1, "--live")  # a replay Fortnite is still writing (see ReplayWatcher.live_check)
    if squad:
        args.insert(1, "--squad")
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env,
                          creationflags=flags, encoding="utf-8", errors="replace")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    if not lines:
        raise RuntimeError(f"reader gave no result (exit {proc.returncode}): {proc.stderr.strip()[-300:]}")
    return json.loads(lines[-1])


class ReplayDB:
    """Which replay files were already read, so each recording is only ever counted once."""

    def __init__(self, path: Path = REPLAYS_PATH):
        self.path = path
        d = load_json(path, {"files": {}, "guids": []})
        self.files: dict[str, dict] = dict(d["files"])
        self.guids: set[str] = set(d["guids"])
        self._save_lock = threading.Lock()

    def save(self):
        with self._save_lock:
            save_json(self.path, {"files": dict(self.files), "guids": sorted(self.guids)})

    def set_own_counts(self, names: set[str], games: dict[str, int]) -> int:
        """A teammate's own count for matches (session id -> chests, from their tracker) goes on their entry in
        this PC's replays of those matches as "own": what this replay saw of them is only what happened near
        the recorder. names: the names they play as (normalized). Returns how many entries changed."""
        changed = 0
        for e in list(self.files.values()):
            n = games.get(e.get("session") or "")
            if n is None:
                continue
            for t in e.get("teammates") or []:
                if norm_name(t.get("name")) in names and t.get("own") != n:
                    t["own"] = n
                    changed += 1
        if changed:
            self.save()
        return changed

    def break_rate(self) -> tuple[float, str]:
        """Broken chests per far-away opened chest that replays miss (see app_model.far_broken), learned from
        this PC's replays: teammates' own counts (their trackers) against what the replay saw of them; else
        the recorder's own broken per opened chest. Returns (rate, where it came from)."""
        missed = far_opens = 0.0
        matches = rec_o = rec_b = 0
        for e in list(self.files.values()):
            if e.get("how") == "recorder" and e.get("status") in ("imported", "confirmed"):
                rec_o += int(e.get("opened") or 0)
                rec_b += int(e.get("broken") or 0)
            for t in e.get("teammates") or []:
                own, far = t.get("own"), t.get("far")
                if isinstance(own, (int, float)) and isinstance(far, (int, float)) and far > 0:
                    o = int(t.get("opened") or 0)
                    missed += own - o - int(t.get("broken") or 0)
                    far_opens += o * far
                    matches += 1
        if matches >= 5 and far_opens >= 150:
            return max(0.1, min(0.6, missed / far_opens)), f"teammates' own counts, {matches} matches"
        if rec_o >= 150:
            return max(0.1, min(0.6, rec_b / rec_o)), "my own broken chests"
        return 0.3, "default"

    def backfill_far(self, maps_dir: Path) -> int:
        """Replays read before 1.24 have no "far" on their teammates: take it from the map data saved for
        them (no need to read the replays again). Returns how many entries got it."""
        done = changed = 0
        for name, e in list(self.files.items()):
            if e.get("far_v") or not e.get("teammates"):
                continue
            e["far_v"] = 1
            changed += 1
            f = maps_dir / f"{Path(name).stem}.map.json"
            try:
                m = json.loads(f.read_text(encoding="utf8")).get("map") if f.exists() else None
            except (OSError, ValueError):
                m = None
            if m and mark_far(m, e["teammates"]):
                done += 1
        if changed:
            self.version = getattr(self, "version", 0) + 1
            self.save()
        return done

    def finished(self, name: str, reader_stamp: float) -> bool:
        e = self.files.get(name)
        if not e:
            return False
        if e["status"] == "failed":
            # retry failures when the reader is updated, otherwise give up after 3 tries
            return e.get("reader") == reader_stamp and e.get("tries", 0) >= 3
        return True

    # Replays read by an older reader get read once more: 2 = names and teammates recorded,
    # 3 = teammates' chests counted from their searches (all over the map, not just near you).
    SCAN_VERSION = 8

    def mark(self, name: str, **info):
        e = self.files.setdefault(name, {})
        if info.get("status") == "failed":
            info["tries"] = e.get("tries", 0) + 1
        e.update(info)
        self.version = getattr(self, "version", 0) + 1
        self.save()

    def needs_rescan(self, name: str) -> bool:
        e = self.files.get(name) or {}
        return e.get("status") in ("imported", "confirmed") and e.get("scan", 1) < self.SCAN_VERSION

    def sessions(self) -> set[str]:
        return {e["session"] for e in self.files.values() if e.get("session")}


class ReplayWatcher(threading.Thread):
    """Watches Fortnite's replay folder.

    - A new replay file that is still being recorded = a new match started.
    - A replay that finished recording is read once: if it belongs to a game the app counted live,
      that game is confirmed and corrected; otherwise (the app wasn't running) it is imported.
    """

    POLL = 3          # seconds between folder checks
    SETTLE = 8        # a finished replay must be unchanged this long before it is read
    NEW_MATCH_AGE = 600  # a recording file younger than this (s) that we haven't seen = a match just started

    def __init__(self, app: "App", demos: Path = DEMOS_DIR, exe: Path = READER_EXE, db: ReplayDB | None = None):
        super().__init__(daemon=True)
        self.app = app
        self.demos = demos
        self.exe = exe
        self.db = db or ReplayDB()
        self.known: set[str] = set()
        self.handles_new_games = False
        self.warned_no_reader = False
        self.import_progress: tuple[int, int, int] | None = None  # (done, of, chests)
        self._mip, self._mip_checked, self._mip_known = True, -1e9, False

    MATCH_MAX_AGE = 45 * 60  # a "recording" replay older than this was left behind by a crash

    def match_in_progress(self, strict: bool = False) -> bool:
        """True while Fortnite is recording a match (its newest replay is still live).

        If replays aren't recorded at all there's no way to tell, so this returns True (False if
        strict) and counting only relies on Fortnite being the window in front.
        Note: Windows doesn't update a replay's size or modified time while Fortnite is writing it,
        so the header's "live" flag and the creation time are used instead.
        """
        now = time.monotonic()
        if now - self._mip_checked < 2:
            return self._mip and (self._mip_known or not strict)
        self._mip_checked = now
        try:
            files = [(p.stat().st_ctime, p) for p in self.demos.glob("*.replay")]
        except OSError:
            files = []
        self._mip_known = bool(files) and self.app.cfg.get("replays", True)
        if not self._mip_known:
            self._mip = True
        else:
            recent = [p for created, p in files if time.time() - created < self.MATCH_MAX_AGE]
            self._mip = any((read_replay_header(p) or {}).get("live") for p in recent)
        return self._mip and (self._mip_known or not strict)

    # -- helpers -----------------------------------------------------------------------------
    def _list(self):
        out = {}
        try:
            for p in self.demos.glob("*.replay"):
                try:
                    st = p.stat()
                    out[p.name] = (p, st.st_size, st.st_mtime)
                except OSError:
                    pass
        except OSError:
            pass
        return out

    def _reader_stamp(self) -> float:
        try:
            return self.exe.stat().st_mtime
        except OSError:
            return 0.0

    def startup(self, files):
        """Remember what's already there. If a match is being recorded right now, link it."""
        self.known = set(files)
        self.handles_new_games = bool(files) and self.app.cfg.get("auto_new_game", True)
        live = [(m, n) for n, (p, s, m) in files.items() if (read_replay_header(p) or {}).get("live")]
        if live:
            _, name = max(live)
            hdr = read_replay_header(files[name][0]) or {}
            st = self.app.stats
            if st.replay != name:
                started = hdr.get("started") or files[name][2]
                if st.replay is None and st.game > 0 and st.game_started >= started - 120:
                    st.link_replay(name)  # this match was already being counted (log watcher)
                else:
                    st.new_game("replay already recording", replay=name, started=started)
            log.info("match in progress: %s", name)

    def check_new(self, files, now: float):
        for name, (p, size, mtime) in files.items():
            if name in self.known:
                continue
            hdr = read_replay_header(p)
            if hdr is None and now - mtime < self.NEW_MATCH_AGE:
                # Fortnite creates the file a moment before it writes anything into it: look again
                # next time instead of deciding now (this used to miss new matches)
                continue
            self.known.add(name)
            if hdr and hdr["live"] and now - mtime < self.NEW_MATCH_AGE:
                self.handles_new_games = self.app.cfg.get("auto_new_game", True)
                started = hdr.get("started") or mtime
                if self.handles_new_games and self.app.stats.new_game("replay started", replay=name, started=started):
                    self.app.toast("New game. Good luck!")
                try:
                    log.info("window in front at match start: %r (counting %s)", front_window(),
                             "on" if fortnite_in_front() else "PAUSED - not recognised as Fortnite")
                except Exception:
                    pass

    def next_job(self, files, now: float):
        """The replay to read next: games counted live first (newest first), then past ones, then
        replays read by an older version that need reading once more (names and teammates)."""
        stamp = self._reader_stamp()
        linked, other, rescan = [], [], []
        for name, (p, size, mtime) in files.items():
            if now - mtime < self.SETTLE:
                continue
            if self.db.finished(name, stamp):
                # (an older reader can't report names: retry only once the reader has been rebuilt)
                if self.db.needs_rescan(name) and self.db.files[name].get("rescan_tried") != stamp:
                    rescan.append((mtime, name, {"started": self.db.files[name].get("started")}))
                continue
            hdr = read_replay_header(p)
            if not hdr or hdr["live"]:
                continue
            (linked if self.app.stats.is_linked(name) else other).append((mtime, name, hdr))
        if linked:
            return max(linked)
        # Games played without the tracker (and old reads to redo): read during matches too, in the background
        # (see playing() and run_reader's background); newest first, so the latest games show up first.
        if other and self.app.cfg.get("import_past_replays", True):
            return max(other)
        if rescan:
            return max(rescan)
        return None

    PLAYING_GAP = 12  # s between past replays read during a match (about 8 s each in the background)

    def playing(self) -> bool:
        """A match is being played on this PC right now (Fortnite's log when there is one, else the replays)."""
        gl = getattr(self.app, "gamelog", None)
        state = gl.match_state() if gl is not None else None
        if state is not None:
            return state == "match"
        self._mip_checked = -1e9
        return bool(self.match_in_progress(strict=True))

    # -- whose chests ------------------------------------------------------------------------
    def my_names(self) -> set[str]:
        return {norm_name(n) for n in self.app.cfg.get("fortnite_names") or [] if norm_name(n)}

    def detect_names(self):
        """No Fortnite name set yet: take the name that recorded most of this PC's replays.
        Only called once every replay on the PC has been read (see run), so one match played by
        someone else can't decide it."""
        if self.app.cfg.get("fortnite_names"):
            return
        recorders = [e.get("recorder") for e in self.db.files.values() if e.get("recorder")]
        if not recorders:
            return
        name = max(set(recorders), key=recorders.count)
        self.app.cfg["fortnite_names"] = [name]
        save_json(CONFIG_PATH, self.app.cfg)
        log.info("Fortnite name detected: %s (%d of %d replays)", name, recorders.count(name), len(recorders))
        self.app.toast(f"Counting chests for {name} (change it in settings)", 8)
        self.reattribute()

    def reattribute(self):
        """Recount every replay already read for the current Fortnite name(s), without reading again."""
        names = self.my_names()
        changed = 0
        for rname, e in list(self.db.files.items()):
            if e.get("status") not in ("imported", "confirmed") or e.get("scan", 1) < 2:  # (1 = no names yet)
                continue
            o, b, how = my_counts(e.get("raw", {}), names)
            if (o, b, how) != (e.get("opened"), e.get("broken"), e.get("how")):
                res = self.app.stats.confirm(rname, o, b, drop_seen=how != "recorder")
                self.db.files[rname].update(opened=o, broken=b, how=how)
                changed += 1 if res else 0
        if changed:
            self.db.save()
            log.info("recounted %d replays for %s", changed, sorted(names))

    def process(self, name: str, path: Path, hdr: dict):
        stats, cfg = self.app.stats, self.app.cfg
        if not stats.is_linked(name) and not self.db.needs_rescan(name) and name not in self.db.files:
            # The current game has no replay yet but started around when this one did: it's this
            # match (its start was missed). Confirm it instead of importing it, or it counts twice.
            started = hdr.get("started") or 0
            # (the game may only have started at its first chest, up to a whole match after the replay)
            if stats.replay is None and started and stats.game_started - 1500 <= started <= stats.game_started + 300:
                stats.link_replay(name)
                log.info("replay %s belongs to the current game (its start was missed)", name)
        linked = stats.is_linked(name)
        rescan = self.db.needs_rescan(name)
        started = hdr.get("started") or path.stat().st_mtime
        if not linked and not rescan and started < season_start_epoch(cfg.get("season_start", "")):
            self.db.mark(name, status="skipped", reason="before season_start", started=started)
            return
        map_on = getattr(self.app, "map", None) is not None
        try:
            bg = bool(getattr(self, "background", False))
            r = run_reader(self.exe, path, with_map=map_on, background=bg)
            if map_on:
                # the map window needn't read it again (a finished recording: next_job never gives a live one)
                self.app.map.take_reader_output(path, r, finished=not (hdr or {}).get("live", False))
        except Exception as e:
            log.warning("replay %s could not be read: %s", name, e)
            if rescan:
                self.db.mark(name, scan=self.db.SCAN_VERSION)  # keep its old count, don't retry forever
            else:
                self.db.mark(name, status="failed", error=str(e)[:300], reader=self._reader_stamp())
            return
        guid = r.get("guid")
        if not r.get("recorder_found"):
            log.warning("replay %s: could not tell which player is you (%s)", name, r.get("error"))
            if rescan:
                self.db.mark(name, scan=self.db.SCAN_VERSION)
            else:
                self.db.mark(name, status="failed", error="recorder not found: %s" % r.get("error"), guid=guid,
                             reader=self._reader_stamp())
            return
        if not rescan and guid and guid in self.db.guids:
            self.db.mark(name, status="duplicate", guid=guid)  # the same recording under another name
            return
        # teammates' chests with real chest locations (map data): chests credited to them that their
        # search flag never showed are added (see squad_counts)
        try:
            counts = squad_counts(r, load_chest_spots()) if r.get("map") else None
        except Exception:
            log.exception("squad counts for %s", name)
            counts = None
        if counts is not None:
            for t in r.get("teammates") or []:
                # never below the reader's own count (a teammate with no map marks, rounding)
                n = max(int(t.get("opened") or 0), counts.get(t.get("name"), 0))
                if n != t.get("opened"):
                    t["reader_opened"], t["opened"] = t.get("opened"), n
            r["squad_v"] = 1
        if r.get("map"):
            try:
                mark_far(r, r.get("teammates") or [])
            except Exception:
                log.exception("far-away teammates in %s", name)
        # what this replay holds: the recorder's chests and each teammate's
        raw = {k: r.get(k) for k in ("opened", "broken", "recorder_name", "recorder_platform", "teammates")}
        # an older reader doesn't report everything: then read it again once the new one is built
        # 2: teammates, 3: teammates' searches, 4: chests broken by placing a build
        # 5: treasure hunt prizes, 6: teammates' chests matched by location (needs the map data)
        map_on = getattr(self.app, "map", None) is not None
        # 7: sprite gizmo prizes (the reader's treasure_v 2); 8: gizmos credited to the chest's opener (3)
        level6 = bool(r.get("squad_v") or ("treasures" in r and not map_on))
        tv = r.get("treasure_v", 0)
        scan = (8 if tv >= 3 else 7 if tv >= 2 else 6) if level6 else 5 if "treasures" in r else 4 if "build_breaks" in r else 3 if "recorder_searches" in r else 2 if "teammates" in r else 1
        new_reader = scan >= self.db.SCAN_VERSION
        details = dict(scan=scan, raw=raw, recorder=r.get("recorder_name"), far_v=1 if r.get("map") else None,
                       session=r.get("session_id"), teammates=r.get("teammates") or [])
        opened, broken, how = my_counts(raw, self.my_names())
        if "treasures" in r and hasattr(self.app, "on_replay_treasures"):
            try:
                self.app.on_replay_treasures(name, r, started, how)
            except Exception:
                log.exception("treasures in %s", name)
        if rescan and not new_reader:
            self.db.mark(name, rescan_tried=self._reader_stamp())  # old reader: keep the count, try later
            return
        if rescan:
            stats.confirm(name, opened, broken, drop_seen=how != "recorder")
            self.db.mark(name, opened=opened, broken=broken, how=how, **details)
            log.info("replay %s re-read: recorder %s, counted %d + %d (%s)", name, r.get("recorder_name"),
                     opened, broken, how)
            return
        if not r.get("complete"):
            log.info("replay %s only partly read (%s); using what was read", name, r.get("error"))
        res = stats.confirm(name, opened, broken, drop_seen=how != "recorder",
                            **(_replay_check(r, hdr, live=False) if how == "recorder" else {}))
        if res:
            where, before, after = res
            log.info("replay %s confirms %s game: %d opened + %d broken (was %d, now %d)",
                     name, where, opened, broken, before, after)
            if where == "current":
                msg = f"Replay check: {opened} opened" + (f" + {broken} broken" if broken else "")
                msg = msg if after == before else f"{msg}. Now {after}"
                # console teammates' tally for this game
                guests = self.app.guest_names() if hasattr(self.app, "guest_names") else {}
                for t in r.get("teammates") or []:
                    if norm_name(t.get("name")) in guests:
                        msg += f". {t.get('name')}: {int(t.get('opened', 0)) + int(t.get('broken', 0))} chests"
                self.app.toast(msg, 10)
                if after != before:
                    self.app.flash()
            status = "confirmed"
        else:
            stats.import_game(name, started, (r.get("length_ms") or hdr.get("length_ms") or 0) / 1000, opened, broken,
                              session=r.get("session_id"))
            log.info("replay %s imported: %d opened + %d broken (%s)", name, opened, broken, how)
            status = "imported"
        if guid:
            self.db.guids.add(guid)
        self.db.mark(name, status=status, guid=guid, opened=opened, broken=broken, how=how, started=started,
                     partial=not r.get("complete"), **details)

    # -- thread --------------------------------------------------------------------------------
    def _drop_double_credits(self):
        try:
            self.app.stats.drop_credits_for(self.db.sessions())
        except Exception:
            log.exception("checking credits against my replays")
        self.learn_break_rate()

    def learn_break_rate(self):
        """How many broken chests far-away teammates' counts get added (app_model.far_broken)."""
        try:
            rate, src = self.db.break_rate()
            if abs(rate - app_model.BREAK_RATE) > 0.005 or not getattr(self, "_rate_said", False):
                self._rate_said = True
                log.info("far-away teammates: %.2f broken chests added per opened chest (from %s)", rate, src)
            app_model.BREAK_RATE = rate
        except Exception:
            log.exception("learning the break rate")

    def run(self):
        files = self._list()
        self.startup(files)
        try:
            n = self.db.backfill_far(DATA_DIR / "maps")
            if n:
                log.info("far-away teammates marked on %d past replays from their map data", n)
        except Exception:
            log.exception("marking far-away teammates")
        self._drop_double_credits()
        while True:
            try:
                now = time.time()
                files = self._list()
                self.check_new(files, now)
                try:
                    self.live_check(files)
                except Exception:
                    log.exception("mid-match replay check failed")
                if not self.exe.exists():
                    if not self.warned_no_reader:
                        self.warned_no_reader = True
                        log.warning("replay reader missing at %s: broken chests can't be counted", self.exe)
                        self.app.toast("Broken chests are off: double-click setup_reader.bat to fix", 15)
                    time.sleep(self.POLL)
                    continue
                job = self.next_job(files, now)
                if job:
                    _, name, hdr = job
                    linked = self.app.stats.is_linked(name)
                    self.background = not linked and self.playing()
                    if self.db.needs_rescan(name):
                        left = sum(1 for n in files if self.db.needs_rescan(n))
                        self.rescanning = True
                        if not self.background:  # (no progress messages over the game)
                            self.app.toast(f"Rechecking past replays for more chests ({left} left)", 30)
                    elif not linked:
                        self._import_tick(files)
                    try:
                        self.process(name, files[name][0], hdr)
                    finally:
                        self.background = False
                    self._drop_double_credits()
                    if not linked and self.playing():
                        time.sleep(self.PLAYING_GAP)  # spread the work out while the game runs
                    continue  # look for more work right away
                self.detect_names()  # every replay has been read
                # (the "done" messages wait for the lobby instead of popping up over the game)
                held = (self.import_progress or getattr(self, "rescanning", False)) and self.playing()
                if self.import_progress and not held:
                    done, of, chests = self.import_progress
                    self.app.toast(f"Added {self.app.stats.imported - chests} chests from {done} past matches", 8)
                    self.import_progress = None
                if getattr(self, "rescanning", False) and not held:
                    self.rescanning = False
                    names = ", ".join(self.app.cfg.get("fortnite_names") or []) or "you"
                    self.app.toast(f"Replays rechecked: counting {names}. Total {self.app.stats.total}", 10)
            except Exception:
                log.exception("replay watch failed")
            time.sleep(self.POLL)

    # -- mid-match ------------------------------------------------------------------------------
    LIVE_EVERY = 20  # s: Fortnite adds a chunk to the replay every 20 s of match time

    def live_check(self, files):
        """During a match, read the replay Fortnite is still writing, every LIVE_EVERY seconds, at low
        priority: broken chests, console teammates and treasures show up mid-match instead of after it.
        Needs the decryption key in the replay's header; if Fortnite only writes it at the end of the
        match, the reader says "no key yet" and this match waits for the end as before."""
        app, stats = self.app, self.app.stats
        gl = getattr(app, "gamelog", None)
        in_match = gl.match_state() == "match" if gl is not None and gl.match_state() is not None else app.match_in_progress()
        name = stats.replay
        if (not app.cfg.get("live_replay", True) or not in_match or not name or name not in files
                or not self.exe.exists()):
            return
        st = getattr(self, "_live", None)
        if st is None or st["name"] != name:
            st = self._live = {"name": name, "at": -1e9, "size": 0, "every": self.LIVE_EVERY, "nokey": False, "ok": 0}
        now = time.monotonic()
        if st["nokey"] or now - st["at"] < st["every"]:
            return
        path = files[name][0]
        hdr = read_replay_header(path)
        if not hdr or not hdr.get("live"):
            return  # finished: process() reads it the normal way
        try:
            with open(path, "rb") as f:  # (the folder listing's size isn't updated while Fortnite writes)
                size = f.seek(0, 2)
        except OSError:
            return
        st["at"] = now
        if size < st["size"] + 4096:
            return  # nothing new written since the last read
        st["size"] = size
        t0 = time.monotonic()
        # the same read also gives the squad's routes for the live map (squad only: the game shows your
        # squad where its members are too; never other teams)
        squad = getattr(app, "map", None) is not None and app.cfg.get("map_live_squad", True)
        r = run_reader(self.exe, path, timeout=120, live=True, squad=True) if squad else \
            run_reader(self.exe, path, timeout=120, live=True)
        took = time.monotonic() - t0
        st["every"] = max(self.LIVE_EVERY, took * 6)  # a slow PC reads less often
        if r.get("error") == "no key yet" or "padding" in str(r.get("error") or "").lower():
            # Fortnite only writes the real decryption key when the match ends (mid-match it's zeros)
            st["nokey"] = True
            log.info("mid-match check: %s has no decryption key yet (encrypted=%s), so it waits for the "
                     "match to end", name, r.get("encrypted"))
            return
        if not r.get("recorder_found"):
            return
        st["ok"] += 1
        raw = {k: r.get(k) for k in ("opened", "broken", "recorder_name", "recorder_platform", "teammates")}
        opened, broken, how = my_counts(raw, self.my_names())
        if st["ok"] == 1:
            log.info("mid-match check works: %s read live (%.1f MB in %.1f s): %d opened + %d broken so far",
                     name, size / 1e6, took, opened, broken)
        if how == "recorder":
            res = stats.confirm_partial(name, opened, broken, **_replay_check(r, hdr, live=True))
            if res and res[1] != res[0]:
                log.info("mid-match: replay so far %d opened + %d broken, live counts checked against it "
                         "(was %d, now %d)", opened, broken, *res)
        if hasattr(app, "set_live_entry"):
            app.set_live_entry(name, {"status": "confirmed", "session": r.get("session_id"), "guid": r.get("guid"),
                                      "started": (hdr.get("started") or 0), "teammates": r.get("teammates") or [],
                                      "recorder": r.get("recorder_name")})
        if "treasures" in r and hasattr(app, "on_replay_treasures"):
            app.on_replay_treasures(name, r, hdr.get("started") or time.time(), how)
        if squad and r.get("squad"):
            try:
                app.map.take_live_squad(r["squad"], r.get("teammates") or [], hdr.get("started") or 0)
            except Exception:
                log.exception("live squad positions")

    def _import_tick(self, files):
        stamp = self._reader_stamp()
        left = sum(1 for n in files if not self.db.finished(n, stamp))
        if self.import_progress is None:
            self.import_progress = (0, left, self.app.stats.imported)
        done, of, chests = self.import_progress
        self.import_progress = (done + 1, max(of, done + left), chests)
        if not getattr(self, "background", False):  # (no progress messages over the game)
            self.app.toast(f"Reading past replays {done + 1}/{max(of, done + left)}", 30)


# --------------------------------------------------------------------------------------
# Team: siblings who enter the same code share counts live (internet + home network)
# --------------------------------------------------------------------------------------
normalize_code = teamcrypt.normalize_code


def internet_on(cfg: dict) -> bool:
    """Squad sync over the internet relay: on unless turned off, or offline mode is on."""
    return bool(cfg.get("internet_sync", True)) and not cfg.get("offline")


def team_key(code) -> str:
    """The team's relay channel name, made from the team code (teamcrypt.team_secrets). The code itself is
    never sent anywhere, and every message is locked with a key made from it."""
    return teamcrypt.team_secrets(code)[0]


def old_team_key(code) -> str:
    """The channel name before 1.31 (messages weren't locked then): only to keep remembered teammates."""
    c = normalize_code(code)
    return hashlib.sha256(("chesttracker:" + c).encode()).hexdigest()[:24] if c else ""


def _count(v) -> int:
    try:
        return max(0, min(10_000_000, int(v)))
    except (TypeError, ValueError):
        return 0


class Team:
    """Everyone on my team and their latest counts, merged from all links. Thread-safe."""

    LAN_TIMEOUT = 15    # seconds without a home-network packet before a sibling counts as gone
    NET_TIMEOUT = 100   # seconds without an internet heartbeat (sent every 30 s)

    def __init__(self, app, path: Path = TEAM_PATH):
        self.app = app
        self.path = path
        self.lock = threading.Lock()
        self.version = 0
        self.key = ""
        self.secret = b""
        self.peers: dict[str, dict] = {}
        self._load(app.cfg.get("team_code"))

    def _load(self, code):
        key, self.secret = teamcrypt.team_secrets(code)
        self.key = key
        self.peers = {}
        data = load_json(self.path, {}) if key else {}
        saved = data.get(key)
        if saved is None and key:  # (1.31 changed the channel name: keep the teammates remembered before)
            saved = data.get(old_team_key(code), {})
        saved = saved or {}
        for pid, p in saved.items():
            # Remembered so TEAM TOTAL includes siblings who aren't playing right now.
            self.peers[pid] = {"name": str(p.get("name", ""))[:20], "game": 0, "total": _count(p.get("total")),
                               "ts": 0.0, "online": False, "lan_seen": -1e9}

    def _save(self):
        if not self.key:
            return
        data = load_json(self.path, {})
        data[self.key] = {pid: {"name": p["name"], "total": p["total"]} for pid, p in self.peers.items()}
        save_json(self.path, data)

    def set_code(self, code: str):
        with self.lock:
            self.app.cfg["team_code"] = str(code or "").strip()[:40]
            save_json(CONFIG_PATH, self.app.cfg)
            self._load(code)
            self.version += 1
        log.info("team code %s", "set" if self.key else "cleared")

    def seal(self, message) -> bytes:
        """A message for the relay or the home network, locked with the team code (teamcrypt)."""
        return teamcrypt.lock(self.secret, message)

    def unseal(self, data):
        """A teammate's message, or None if it isn't locked with our team code."""
        return teamcrypt.unlock(self.secret, data)

    def message(self, per_match: bool = False) -> dict:
        s = self.app.stats
        return {"app": "chesttracker", "v": 2, "team": self.key, "id": s.id, "name": self.app.cfg.get("name", "")[:20],
                "game": s.game, "total": s.total, "today": s.today(), "ts": time.time(), "online": True,
                # in-game names this player plays as, so teammates' trackers can send chests seen in their replays
                "fortnite_names": sorted({norm_name(n) for n in self.app.cfg.get("fortnite_names") or [] if n})[:6],
                # console teammates without a tracker, counted from this PC's replays
                "guests": self._my_guests(per_match),
                **({"games": self._my_games()} if per_match else {}),
                # treasure hunt prizes this player found (see treasures.py)
                "treasures": self._my_treasures(),
                # this tracker's version and whether its live map works (to find out why a squad map stays empty)
                "version": APP_VERSION.split(" ")[0], "map": self._map_health()}

    GAMES_SHARED = 40  # my most recent games sent to the team, by match (internet relay only)

    def _my_games(self) -> list:
        """[[match session id, my chests]] for my recent games whose replay was read: teammates' apps show
        this instead of what their replay saw of me."""
        replays = getattr(self.app, "replays", None)
        files = replays.db.files if replays is not None else {}
        s = self.app.stats
        games = [(h.get("replay"), h.get("chests")) for h in list(s.history)[-self.GAMES_SHARED:]]
        if s.replay and s.confirmed:
            games.append((s.replay, s.game))
        out = []
        for replay, n in games:
            session = (files.get(replay) or {}).get("session") if replay else None
            if session and isinstance(n, (int, float)):
                out.append([str(session)[:40], int(n)])
        return out[-self.GAMES_SHARED:]

    def _map_health(self):
        m = getattr(self.app, "map", None)
        if m is None:
            return {"status": "map unavailable (see tracker.log)"}
        try:
            return m.health()
        except Exception as e:
            return {"status": f"error: {type(e).__name__}"[:60]}

    def _my_treasures(self) -> dict:
        log_ = getattr(self.app, "treasures", None)
        return log_.share() if log_ is not None else {"count": 0, "recent": []}

    GUEST_MATCHES = 150  # most recent matches per console teammate sent to the team (internet relay only)

    def _my_guests(self, per_match: bool = False) -> dict:
        fn = getattr(self.app, "guest_stats", None)
        try:
            out = {}
            for n, g in (fn() if fn else {}).items():
                out[n] = {k: g.get(k, 0) for k in ("name", "game", "total", "platform", "today")}
                if per_match:
                    recent = sorted((g.get("by_match") or {}).items(), key=lambda kv: kv[1][1])[-self.GUEST_MATCHES:]
                    out[n]["by_match"] = dict(recent)
            return out
        except Exception:
            return {}

    def status_message(self, online: bool) -> dict:
        return {"app": "chesttracker", "v": 2, "team": self.key, "id": self.app.stats.id, "ts": time.time(),
                "online": online}

    def _is_online(self, p: dict, now_mono: float, now: float) -> bool:
        return now_mono - p["lan_seen"] < self.LAN_TIMEOUT or (p["online"] and abs(now - p["ts"]) < self.NET_TIMEOUT)

    def update(self, m, via: str, now_mono: float | None = None, now: float | None = None) -> str | None:
        """Takes a message from a sibling. Returns their name if they just came online."""
        if not isinstance(m, dict) or m.get("app") != "chesttracker":
            return None
        pid = str(m.get("id", ""))
        if not re.fullmatch(r"[0-9a-f]{4,32}", pid) or pid == self.app.stats.id:
            return None
        now_mono = time.monotonic() if now_mono is None else now_mono
        now = time.time() if now is None else now
        with self.lock:
            if str(m.get("team") or "") != self.key:
                return None
            p = self.peers.get(pid) or {"name": "", "game": 0, "total": 0, "ts": 0.0, "online": False, "lan_seen": -1e9}
            was_online = self._is_online(p, now_mono, now) if pid in self.peers else False
            ts = float(m.get("ts") or 0) if isinstance(m.get("ts"), (int, float)) else 0.0
            old_total = p["total"]
            if "total" in m:  # a full state message (status messages only carry online/offline)
                p["name"] = str(m.get("name", ""))[:20]
                p["game"] = _count(m.get("game"))
                p["total"] = _count(m.get("total"))
                p["today"] = _count(m.get("today")) if "today" in m else None  # (older trackers don't send it)
                fns = m.get("fortnite_names")
                if isinstance(fns, list):
                    p["fortnite_names"] = [norm_name(n)[:32] for n in fns[:6] if isinstance(n, str)]
                gs = m.get("guests")
                if isinstance(gs, dict):
                    old = p.get("guests") or {}
                    p["guests"] = {norm_name(n)[:32]: {"name": str(g.get("name", n))[:32], "game": _count(g.get("game")),
                                                       "total": _count(g.get("total")), "platform": str(g.get("platform", ""))[:8],
                                                       "today": _count(g.get("today")),
                                                       # per match: only in internet messages; a LAN one keeps the last
                                                       "by_match": _by_match(g.get("by_match"))
                                                       if "by_match" in g else (old.get(norm_name(n)[:32]) or {}).get("by_match")}
                                   for n, g in list(gs.items())[:10] if isinstance(g, dict) and norm_name(n)}
                tr = treasures.parse_shared(m.get("treasures"))
                if tr is not None:
                    seen = {(x["prize"], x["when"]) for x in (p.get("treasures") or {}).get("recent", [])}
                    first = "treasures" not in p
                    p["treasures"] = tr
                    fresh = [x for x in tr["recent"] if (x["prize"], x["when"]) not in seen and now - x["when"] < 3 * 3600]
                    if fresh and not first and hasattr(self.app, "announce_treasure"):
                        for x in fresh:
                            self.app.announce_treasure(str(m.get("name") or "A teammate")[:20], x)
                    rec = getattr(self.app, "recorder", None)
                    if fresh and rec is not None:  # their find keeps my video of that match (mine may not see it)
                        for x in fresh:
                            rec.shared_find(x["when"], x["prize"], str(m.get("name") or "")[:20], treasures.describe)
            games = m.get("games")
            if isinstance(games, list) and "total" in m:
                got = {}
                for g in games[:60]:
                    if (isinstance(g, list) and len(g) == 2 and isinstance(g[0], str)
                            and isinstance(g[1], (int, float)) and 0 <= g[1] < 10000):
                        got[g[0][:40]] = int(g[1])
                p["games"] = got
            if ts >= p["ts"]:
                p["ts"] = ts
                p["online"] = bool(m.get("online", True))
            if via == "lan":
                p["lan_seen"] = now_mono
            self.peers[pid] = p
            self.version += 1
            if p["total"] != old_total or pid not in load_json(self.path, {}).get(self.key, {}):
                self._save()
            if not was_online:
                p["announced"] = False
            # announce once they're online and their name has arrived (status can come before state)
            if self._is_online(p, now_mono, now) and p["name"] and not p.get("announced"):
                p["announced"] = True
                return p["name"]
        return None

    def online(self) -> list[dict]:
        now_mono, now = time.monotonic(), time.time()
        with self.lock:
            return [dict(p) for p in self.peers.values() if self._is_online(p, now_mono, now)]

    def guests(self) -> dict[str, dict]:
        """Console teammates without a tracker (from this PC's settings and from teammates' trackers),
        merged by name. Names a teammate claims as their own are left out: their chests go to that
        teammate's total instead, so nothing is counted twice."""
        mine = {}
        fn = getattr(self.app, "guest_stats", None)
        if fn:
            try:
                mine = fn()
            except Exception:
                log.exception("console teammates")
        claimed = self.claimed_names()
        now_mono, now = time.monotonic(), time.time()
        sources: dict[str, list] = {}  # name -> [(guest dict, online)] from me and each teammate's tracker
        for n, g in mine.items():
            if n not in claimed:
                sources.setdefault(n, []).append((g, True))
        with self.lock:
            for p in self.peers.values():
                online = self._is_online(p, now_mono, now)
                for n, g in (p.get("guests") or {}).items():
                    if n not in claimed:
                        sources.setdefault(n, []).append((g, online))
        return {n: merge_guest(src) for n, src in sources.items()}

    def team_game(self) -> int:
        guests = sum(g["game"] for g in self.guests().values() if g.get("online"))
        return self.app.stats.game + sum(p["game"] for p in self.online()) + guests

    def team_total(self) -> int:
        guests = sum(g["total"] for g in self.guests().values())
        with self.lock:
            return self.app.stats.total + sum(p["total"] for p in self.peers.values()) + guests

    def claimed_names(self) -> set[str]:
        """In-game names my teammates said they play as (from their settings)."""
        with self.lock:
            return {n for p in self.peers.values() for n in p.get("fortnite_names") or []}

    def has_team(self) -> bool:
        return bool(self.key) or bool(self.online())

    LAST_GAME_S = 3 * 3600  # a game that started longer ago than this isn't "the last game" any more

    def last_squad(self, now: float | None = None) -> tuple[set[str], set[str]]:
        """My latest game's squad: (normalized names of the other players, its match ids). The game being
        played if its replay has been read mid-match, else the newest finished replay, if it started within
        LAST_GAME_S. Empty when there's none."""
        now = time.time() if now is None else now
        replays = getattr(self.app, "replays", None)
        entries = dict(getattr(getattr(replays, "db", None), "files", None) or {})
        live = getattr(self.app, "_live_entry", None)
        if live:
            entries[live[0]] = dict(live[1], status=live[1].get("status") or "confirmed", started=live[1].get("started") or now)
        best = None
        for e in entries.values():
            if e.get("status") not in ("imported", "confirmed") or e.get("how") == "absent":
                continue
            try:
                started = float(e.get("started") or 0)
            except (TypeError, ValueError):
                continue
            if now - started <= self.LAST_GAME_S and (best is None or started > best[0]):
                best = (started, e)
        if best is None:
            return set(), set()
        e = best[1]
        names = {norm_name(t.get("name")) for t in e.get("teammates") or [] if t.get("name") and not t.get("bot")}
        if e.get("how") == "teammate" and e.get("recorder"):
            names.add(norm_name(e["recorder"]))
        return names - {""}, {str(e["session"])[:40]} if e.get("session") else set()

    def players(self) -> list[dict]:
        """The squad for the expanded overlay: me first, then teammates who are online, were in my last game
        (see last_squad) or are console players in this game. Everyone else stays in TEAM TOTAL only."""
        s = self.app.stats
        me = {"name": self.app.cfg.get("name") or "You", "game": s.game, "total": s.total, "online": True, "me": True,
              "today": s.today()}
        now_mono, now = time.monotonic(), time.time()
        midnight = local_midnight(now)
        recent, matches = self.last_squad(now)
        with self.lock:
            # today: what they last sent, if that was today (None: their tracker doesn't send it yet)
            others = [{"name": p["name"] or "?", "game": p["game"], "total": p["total"],
                       "today": (p.get("today") if p.get("ts", 0) >= midnight else 0) if p.get("today") is not None else None,
                       "online": self._is_online(p, now_mono, now), "me": False}
                      for p in self.peers.values()
                      if self._is_online(p, now_mono, now) or recent & set(p.get("fortnite_names") or [])
                      or matches & set(p.get("games") or {})]
        for n, g in self.guests().items():
            if not (g.get("game") or n in recent):
                continue  # a console teammate who isn't playing with me now
            plat = {"PSN": "PS", "PS5": "PS", "PS4": "PS", "XBL": "Xbox", "XSX": "Xbox", "SWT": "Switch"}.get(
                (g.get("platform") or "").upper(), "console")
            others.append({"name": f"{g['name']} ({plat})", "game": g["game"], "total": g["total"],
                           "today": g.get("today", 0), "online": bool(g.get("online")), "me": False})
        others.sort(key=lambda p: (not p["online"], -p["total"]))
        return [me] + others


class LanLink(threading.Thread):
    """Shares counts with siblings on the same home network (UDP broadcast, no internet needed)."""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app

    def run(self):
        port = int(self.app.cfg["lan_port"])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind(("", port))
        except OSError as e:
            log.warning("home network sync off, port %s busy: %s", port, e)
            return
        sock.settimeout(0.25)
        last_sent, last_ver = 0.0, -1
        while True:
            if self.app.cfg.get("offline"):  # (offline mode: nothing sent, nothing read)
                time.sleep(1)
                continue
            now = time.monotonic()
            ver = (self.app.stats.version, self.app.team.version)
            if now - last_sent > 2 or ver != last_ver:
                try:
                    if self.app.team.secret:  # (only with a team code: everything sent is locked with it)
                        sock.sendto(self.app.team.seal(self.app.team.message()), ("255.255.255.255", port))
                except OSError:
                    pass
                last_sent, last_ver = now, ver
            try:
                data, _ = sock.recvfrom(65535)
                m = self.app.team.unseal(data)
                joined = self.app.team.update(m, "lan") if m is not None else None
                if joined:
                    self.app.toast(f"{joined} joined the team")
            except socket.timeout:
                pass
            except OSError:
                time.sleep(0.5)


class NetLink(threading.Thread):
    """Shares counts over the internet through a public MQTT relay.

    Every message is locked with the team code (teamcrypt): the relay only carries unreadable bytes.
    Each player keeps two retained messages under chesttracker/v3/<team channel>/:
      state/<id>   name, this game's count, total (re-sent on every change and every 30 s)
      status/<id>  online or offline (the relay posts "offline" itself if the app dies)
    """

    HEARTBEAT = 30

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.status = "off"
        self.wake = threading.Event()
        self._stop = threading.Event()
        self._clients: dict = {}       # relay host -> its connected client
        self.relays: dict = {}         # relay host -> "connected", "connecting", "unreachable"...
        self._make_lock = threading.Lock()
        self._rx_lock = threading.Lock()  # (messages from several relays: handled one at a time)
        self._latest: dict = {}        # kind -> (sequence number, message) made once for every relay
        self._seq = 0
        self._credits_checked: dict = {}

    def brokers(self):
        out = []
        for b in self.app.cfg.get("mqtt_brokers") or []:
            host, _, port = str(b).partition(":")
            out.append((host, int(port or 1883)))
        return out or [("broker.hivemq.com", 1883)]

    def shutdown(self):
        """Tell the team we're leaving (called on quit)."""
        self._stop.set()
        self.wake.set()
        for c in list(self._clients.values()):
            try:
                base = f"chesttracker/v3/{self.app.team.key}"
                c.publish(f"{base}/status/{self.app.stats.id}", self.app.team.seal(self.app.team.status_message(False)),
                          qos=1, retain=True)
                for _ in range(10):
                    c.loop(timeout=0.1)
                c.disconnect()
            except Exception:
                pass

    def _on_message(self, msg):
        with self._rx_lock:
            self._handle_message(msg)

    def _handle_message(self, msg):
        m = self.app.team.unseal(msg.payload)
        if not isinstance(m, dict):
            return  # not locked with our team code: from someone else, an old tracker, or made up
        if "/map/" in msg.topic:  # a squad member's position for the map window
            if self.app.map is not None:
                self.app.map.on_team_message(m)
            return
        if "/credits/" in msg.topic:
            self.app.apply_credits(m)
            return
        joined = self.app.team.update(m, "net")
        try:
            fn = getattr(self.app, "on_teammate_counts", None)
            if fn is not None:
                fn(m)
        except Exception:
            log.exception("teammate's own counts")
        if joined:
            self.app.toast(f"{joined} joined the team")

    def credits_message(self) -> bytes | None:
        """Chests this PC's replays saw teammates get (console or PC), for their trackers."""
        replays = getattr(self.app, "replays", None)
        if replays is None:
            return None
        # only for names a teammate claimed: random squad fills' names never leave this PC
        claimed = self.app.team.claimed_names()
        entries, mine = list(replays.db.files.values()), replays.my_names()
        credits = {n: v for n, v in build_credits(entries, mine).items() if n in claimed}
        if not credits:
            return None
        # "credits": console only, for trackers from before 1.16 (they'd count a PC credit twice)
        old = {n: v for n, v in build_credits(entries, mine, console_only=True).items() if n in claimed}
        return json.dumps({"app": "chesttracker", "type": "credits", "team": self.app.team.key,
                           "from": self.app.cfg.get("name") or "a teammate", "credits": old,
                           "credits_v2": credits}, sort_keys=True).encode()

    def run(self):
        """One connection per relay, all at once: messages go to every relay and come from every relay (a
        relay one PC can't reach for a moment never splits the squad between relays)."""
        try:
            import paho.mqtt.client as mqtt  # noqa: F401
        except ImportError:
            self.status = "internet sync needs paho-mqtt"
            log.warning("paho-mqtt is not installed: team sync works on the home network only")
            return
        threads: dict = {}
        while not self._stop.is_set():
            key = self.app.team.key
            if not key or not internet_on(self.app.cfg):
                self.status = "offline mode" if self.app.cfg.get("offline") else "off"
            else:
                for host, port in self.brokers():
                    t = threads.get((host, port))
                    if t is None or not t.is_alive():
                        t = threading.Thread(target=self._relay, args=(host, port), daemon=True,
                                             name=f"relay-{host}")
                        threads[(host, port)] = t
                        t.start()
                self._update_status()
            self.wake.wait(2)
            self.wake.clear()

    def _update_status(self):
        up = [h for h, st in list(self.relays.items()) if st == "connected"]
        if up:
            self.status = "connected"  # (on at least one relay: enough to reach the whole team)
        elif any(str(st).startswith("connecting") for st in self.relays.values()):
            self.status = "connecting to the internet relay"
        elif self.relays:
            self.status = "can't reach the internet relay, retrying"

    def _shared(self, kind: str, make):
        """A message made once and sent to every relay: (sequence number, bytes) or None. The map's position
        message (rate limited) and the last-game message (sent once) can only be made once for all relays."""
        with self._make_lock:
            try:
                msg = make()
            except Exception:
                log.exception("building the %s message failed", kind)
                msg = None
            if msg:
                self._seq += 1
                self._latest[kind] = (self._seq, msg)
            return self._latest.get(kind)

    def _relay(self, host: str, port: int):
        """Keeps one relay connection while the team code stays the same; retries with a growing wait."""
        import paho.mqtt.client as mqtt

        attempt = 0
        sent: dict = {}  # kind -> sequence number of the last shared message sent to this relay
        while not self._stop.is_set():
            key = self.app.team.key
            if not key or not internet_on(self.app.cfg) or (host, port) not in self.brokers():
                self.relays.pop(host, None)
                return
            base = f"chesttracker/v3/{key}"
            me = self.app.stats.id
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"ct-{me}-{secrets.token_hex(3)}")
            client.will_set(f"{base}/status/{me}", self.app.team.seal(self.app.team.status_message(False)), qos=1, retain=True)
            client.on_message = lambda c, u, msg: self._on_message(msg)
            ready = threading.Event()

            def on_connect(c, userdata, flags, reason, props=None):
                if not reason.is_failure:
                    c.subscribe(f"{base}/#", qos=1)  # also re-subscribes after a reconnect
                    ready.set()

            def on_disconnect(c, userdata, flags, reason, props=None):
                ready.clear()

            client.on_connect, client.on_disconnect = on_connect, on_disconnect
            try:
                self.relays[host] = "connecting"
                client.connect(host, port, keepalive=30)
                end = time.monotonic() + 10
                while not ready.is_set() and time.monotonic() < end:
                    client.loop(timeout=0.1)
                if not ready.is_set():
                    raise TimeoutError("no answer from the relay")
            except Exception as e:
                log.info("team relay %s unreachable: %s", host, e)
                self.relays[host] = "unreachable"
                self._update_status()
                attempt += 1
                if self._stop.wait(min(120, 5 * attempt)):
                    return
                continue
            log.info("team relay connected: %s", host)
            attempt = 0
            self._clients[host] = client
            self.relays[host] = "connected"
            self._update_status()
            last_pub, last_ver, failures = 0.0, None, 0
            last_credits = None
            while not self._stop.is_set() and self.app.team.key == key and internet_on(self.app.cfg):
                rc = client.loop(timeout=0.2)
                if rc != mqtt.MQTT_ERR_SUCCESS:
                    failures += 1
                    if failures > 3:
                        break
                    time.sleep(1)
                    try:
                        client.reconnect()
                    except Exception:
                        pass
                    continue
                failures = 0
                if not ready.is_set():
                    continue
                now = time.monotonic()
                ver = (self.app.stats.version, self.app.cfg.get("name"))
                if ver != last_ver or now - last_pub > self.HEARTBEAT:
                    client.publish(f"{base}/state/{me}", self.app.team.seal(self.app.team.message(per_match=True)), qos=1, retain=True)
                    client.publish(f"{base}/status/{me}", self.app.team.seal(self.app.team.status_message(True)),
                                   qos=1, retain=True)
                    last_pub, last_ver = now, ver
                mgr = getattr(self.app, "map", None)
                if mgr is not None:
                    # only during matches, at most every 3 s (made once, sent to every relay)
                    got = self._shared("map", mgr.team_message)
                    if got and got[0] != sent.get("map") and time.time() - self._made_at(got) < 10:
                        client.publish(f"{base}/map/{me}", self.app.team.seal(got[1]), qos=0)
                    if got:
                        sent["map"] = got[0]
                    # the game that just ended, for the phone page (retained; again when the replay recounts)
                    got = self._shared("last", mgr.last_message)
                    if got and got[0] != sent.get("last"):
                        client.publish(f"{base}/last/{me}", self.app.team.seal(got[1]), qos=1, retain=True)
                        sent["last"] = got[0]
                if now - self._credits_checked.get(host, -1e9) > 20:
                    self._credits_checked[host] = now
                    try:
                        credits = self.credits_message()
                    except Exception:
                        log.exception("building credits failed")
                        credits = None
                    if credits and credits != last_credits:
                        client.publish(f"{base}/credits/{me}", self.app.team.seal(credits), qos=1, retain=True)
                        last_credits = credits
                        log.info("shared teammates' console chests on %s (%d bytes)", host, len(credits))
            self._clients.pop(host, None)
            self.relays[host] = "reconnecting"
            self._update_status()
            if self._stop.is_set():
                return
            # team code changed, sync turned off, or the connection dropped: say goodbye and start over
            try:
                client.publish(f"{base}/status/{me}", self.app.team.seal(self.app.team.status_message(False)), qos=1, retain=True)
                client.loop(timeout=0.5)
                client.disconnect()
            except Exception:
                pass
            if failures > 3:
                attempt += 1
                time.sleep(3)

    def _made_at(self, got) -> float:
        """When a shared map message was made (its "t"): a relay that was busy doesn't send an old position."""
        try:
            return float(json.loads(got[1]).get("t") or 0)
        except (ValueError, TypeError, AttributeError):
            return 0.0


# --------------------------------------------------------------------------------------
# Overlay
# --------------------------------------------------------------------------------------
KEY_COLOR = (255, 0, 255)  # see-through color, only used where per-pixel transparency isn't available
PANEL = (14, 15, 30)
BORDER = (52, 56, 98)
WHITE = (244, 245, 255)
MUTED = (182, 188, 226)
GOLD = (255, 197, 61)
GREEN = (96, 214, 120)


def custom_font() -> Path | None:
    """A font file dropped into the app's "fonts" folder is used instead of the built-in choice
    (for example Fortnite's own Burbank Big Condensed, if you have it)."""
    try:
        return next(iter(sorted(p for p in (APP_DIR / "fonts").iterdir() if p.suffix.lower() in (".ttf", ".otf"))), None)
    except OSError:
        return None


# Fortnite's HUD font (Burbank Big Condensed) isn't free to share; Impact, on every Windows PC, is
# the closest heavy condensed look.
FORTNITE_LIKE = ["impact.ttf", "seguibl.ttf", "ariblk.ttf", "DejaVuSans-Bold.ttf"]


def load_font(names, size):
    from PIL import ImageFont

    custom = custom_font()
    if custom:
        try:
            return ImageFont.truetype(str(custom), size)
        except Exception:
            log.warning("could not use the font %s", custom)
    dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts/truetype/dejavu")]
    for n in names:
        for d in dirs:
            p = d / n
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except Exception:
                    pass
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


def fallback_chest(size: int):
    """A simple drawn chest, used if chest.png is missing."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 100
    d.rounded_rectangle([18 * s, 30 * s, 82 * s, 52 * s], radius=int(10 * s), fill=(150, 104, 40, 255))
    d.rectangle([18 * s, 50 * s, 82 * s, 78 * s], fill=(124, 84, 30, 255))
    d.rectangle([18 * s, 48 * s, 82 * s, 54 * s], fill=(190, 190, 200, 255))
    d.rectangle([45 * s, 44 * s, 55 * s, 60 * s], fill=(190, 190, 200, 255))
    return img


class Painter:
    """Draws the overlay as a transparent (RGBA) image so it looks the same everywhere."""

    def __init__(self, scale: float = 1.0):
        from PIL import Image

        self.s = scale = max(0.5, min(3.0, float(scale)))
        self.W, self.H = int(318 * scale), int(100 * scale)
        cs = int(108 * scale)
        try:
            chest = Image.open(APP_DIR / "chest.png").convert("RGBA").resize((cs, cs), Image.LANCZOS)
        except Exception:
            chest = fallback_chest(cs)
        self.chest = chest
        self.big = load_font(FORTNITE_LIKE, int(60 * scale))
        self.num = load_font(FORTNITE_LIKE, int(21 * scale))
        self.lab = load_font(FORTNITE_LIKE, int(12 * scale))
        self.msg = load_font(FORTNITE_LIKE, int(14 * scale))
        self._panels: dict = {}

    PARTY_HEAD, PARTY_ROW = 30, 22  # expanded table: header and row height (unscaled)

    def party_height(self, n: int) -> int:
        return int((self.PARTY_HEAD + self.PARTY_ROW * n + 10) * self.s) if n else 0

    def _panel(self, opacity: float, highlight: bool, H: int):
        """The rounded background, anti-aliased. Never fully transparent so it can still be clicked."""
        from PIL import Image, ImageDraw

        key = (round(opacity, 2), highlight, H)
        if key not in self._panels:
            W, ss = self.W, 4
            self._panels = {}
            r = int(16 * self.s) * ss
            alpha = max(1, int(round(255 * opacity)))
            fill = Image.new("L", (W * ss, H * ss), 0)
            ImageDraw.Draw(fill).rounded_rectangle([0, 0, W * ss - 1, H * ss - 1], radius=r, fill=alpha)
            edge = Image.new("L", (W * ss, H * ss), 0)
            bw = int(max(1, 2 * self.s if highlight else 1.2 * self.s) * ss)
            ImageDraw.Draw(edge).rounded_rectangle([0, 0, W * ss - 1, H * ss - 1], radius=r, outline=255, width=bw)
            fill, edge = fill.resize((W, H), Image.LANCZOS), edge.resize((W, H), Image.LANCZOS)
            panel = Image.new("RGBA", (W, H), PANEL + (0,))
            panel.putalpha(fill)
            border = Image.new("RGBA", (W, H), (GOLD if highlight else BORDER) + (0,))
            border_alpha = 255 if highlight else max(1, int(round(255 * min(1.0, opacity * 1.2))))
            border.putalpha(edge.point(lambda v: v * border_alpha // 255))
            self._panels = {key: Image.alpha_composite(panel, border)}
        return self._panels[key]

    def draw(self, mine: int, rows, flash: float = 0.0, message: str = "", confirmed: bool = False,
             opacity: float = 0.85, highlight: bool = False, party=None, final: bool = False,
             timer: str = "", late: bool = False):
        """rows: [(label, value)] shown small on the right. party: players for the expanded table
        (see Team.players), or None when collapsed. final: the match is over (shows FINAL).
        timer: the match clock, tiny at the top (red once late). Returns an RGBA image."""
        from PIL import Image, ImageDraw, ImageFilter

        s, W, H = self.s, self.W, self.H
        full_h = H + self.party_height(len(party or []))
        img = self._panel(opacity, highlight, full_h).copy()
        layer = Image.new("RGBA", (W, full_h), (0, 0, 0, 0))
        layer.paste(self.chest, (int(-8 * s), (H - self.chest.height) // 2), self.chest)
        img = Image.alpha_composite(img, layer)

        text = Image.new("RGBA", (W, full_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(text)
        stroke = {}  # (text gets a drop shadow instead of an outline, added at the end)

        x0 = int(96 * s)
        d.text((x0, H // 2 + int(2 * s)), str(mine), font=self.big, fill=GOLD if flash > 0 else WHITE, anchor="lm", **stroke)
        if confirmed:
            # green tick: this game's count was confirmed from its replay
            nx = x0 + d.textlength(str(mine), font=self.big) + int(5 * s)
            ny = H // 2 + int(14 * s)
            pts = [(nx, ny), (nx + 5 * s, ny + 5 * s), (nx + 14 * s, ny - 7 * s)]
            d.line(pts, fill=GREEN, width=max(2, int(3 * s)))
        if final:
            d.text((x0 + int(2 * s), int(16 * s)), "FINAL", font=self.lab, fill=GOLD, anchor="lm")
        elif timer:
            d.text((x0 + int(2 * s), int(14 * s)), timer, font=self.lab, fill=(255, 64, 56) if late else MUTED, anchor="lm")

        rx = W - int(14 * s)
        if message:
            # status text takes the place of the small stats for a few seconds
            words, lines, cur = message.split(), [], ""
            for w in words:
                trial = (cur + " " + w).strip()
                if d.textlength(trial, font=self.msg) > W - int(170 * s) and cur:
                    lines.append(cur)
                    cur = w
                else:
                    cur = trial
            lines.append(cur)
            lines = lines[:4]
            lh = int(16 * s)
            y = H // 2 - lh * len(lines) // 2
            for ln in lines:
                d.text((rx, y), ln, font=self.msg, fill=GOLD, anchor="ra", **stroke)
                y += lh
        else:
            step = int((26 if len(rows) >= 3 else 30) * s)
            top = (H - step * (len(rows) - 1)) // 2
            for i, (label, val) in enumerate(rows):
                y = top + i * step
                d.text((rx, y), str(val), font=self.num, fill=WHITE, anchor="rm", **stroke)
                vw = d.textlength(str(val), font=self.num)
                d.text((rx - vw - int(6 * s), y + int(1 * s)), label, font=self.lab, fill=MUTED, anchor="rm", **stroke)

        if party:
            # expanded: one row per player with this game's chests and their total
            lx, gx = int(16 * s), W - int(96 * s)
            y = H + int(4 * s)
            d.line([(lx, H), (rx, H)], fill=BORDER + (255,), width=max(1, int(s)))
            d.text((lx, y + int(10 * s)), "PLAYER", font=self.lab, fill=MUTED, anchor="lm", **stroke)
            d.text((gx, y + int(10 * s)), "THIS GAME", font=self.lab, fill=MUTED, anchor="rm", **stroke)
            d.text((rx, y + int(10 * s)), "TOTAL", font=self.lab, fill=MUTED, anchor="rm", **stroke)
            y += int(self.PARTY_HEAD * s)
            for p in party:
                cy = y + int(self.PARTY_ROW * s / 2) - int(4 * s)
                r = max(2, int(3.5 * s))
                d.ellipse([lx, cy - r, lx + 2 * r, cy + r], fill=(GREEN if p["online"] else MUTED) + (255,))
                col = GOLD if p.get("me") else WHITE
                name = p["name"] if d.textlength(p["name"], font=self.msg) < gx - lx - 70 * s else p["name"][:12] + "…"
                d.text((lx + 2 * r + int(8 * s), cy), name, font=self.msg, fill=col, anchor="lm", **stroke)
                d.text((gx, cy), str(p["game"]) if p["online"] else "–", font=self.msg, fill=col, anchor="rm", **stroke)
                d.text((rx, cy), str(p["total"]), font=self.msg, fill=col, anchor="rm", **stroke)
                y += int(self.PARTY_ROW * s)
        # Fortnite-style drop shadow: the text's shape, blurred, darker and a little down-right.
        # Stronger over a see-through background, where the game shows behind the text.
        a = text.getchannel("A").filter(ImageFilter.GaussianBlur(max(1.0, 1.4 * s)))
        strength = 0.95 if opacity < 0.6 else 0.75
        shadow = Image.new("RGBA", text.size, (0, 0, 0, 0))
        shadow.paste((0, 0, 0, 255), (max(1, round(1.5 * s)), max(1, round(2.5 * s))),
                     a.point(lambda v: min(255, int(v * strength * 1.3))))
        return Image.alpha_composite(Image.alpha_composite(img, shadow), text)


def make_painter(cfg: dict, scale: float):
    """The overlay's look: the app window's glass style (overlay_glass.py, in the chosen colour theme) unless
    config "overlay_style" is "classic". Falls back to the classic Painter if the glass one can't load."""
    if cfg.get("overlay_style", "glass") != "classic":
        try:
            import overlay_glass

            return overlay_glass.GlassPainter(scale, cfg.get("app_theme"))
        except Exception:
            log.exception("glass overlay unavailable, using the classic look")
    return Painter(scale)


class LayeredWindow:
    """Shows an RGBA image as a window with real per-pixel transparency (Windows UpdateLayeredWindow).

    Tk can only make one exact color see-through, which can't do a half-transparent background
    behind solid text. This hands Windows the finished image instead.
    """

    def __init__(self, hwnd: int):
        from ctypes import wintypes as wt

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        self.BITMAPINFO, self.BLENDFUNCTION, self.wt = BITMAPINFO, BLENDFUNCTION, wt
        u = self.u = ctypes.WinDLL("user32", use_last_error=True)
        g = self.g = ctypes.WinDLL("gdi32", use_last_error=True)
        u.GetDC.argtypes, u.GetDC.restype = [wt.HWND], wt.HDC
        u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        g.CreateCompatibleDC.argtypes, g.CreateCompatibleDC.restype = [wt.HDC], wt.HDC
        g.DeleteDC.argtypes = [wt.HDC]
        g.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       wt.HANDLE, wt.DWORD]
        g.CreateDIBSection.restype = wt.HBITMAP
        g.SelectObject.argtypes, g.SelectObject.restype = [wt.HDC, wt.HGDIOBJ], wt.HGDIOBJ
        g.DeleteObject.argtypes = [wt.HGDIOBJ]
        u.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                          ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
        u.UpdateLayeredWindow.restype = wt.BOOL
        self.hwnd = hwnd

    def show(self, img) -> bool:
        import numpy as np

        wt = self.wt
        w, h = img.size
        a = np.asarray(img, dtype=np.uint16)
        alpha = a[:, :, 3:4]
        rgb = (a[:, :, :3] * alpha + 127) // 255  # Windows wants premultiplied alpha
        bgra = np.ascontiguousarray(np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0], alpha[:, :, 0]]).astype(np.uint8))

        bmi = self.BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(bmi.bmiHeader)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h  # negative = top-down rows
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        screen = self.u.GetDC(None)
        mem = self.g.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        bmp = self.g.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        ok = False
        if bmp and bits.value:
            ctypes.memmove(bits, bgra.ctypes.data, w * h * 4)
            old = self.g.SelectObject(mem, bmp)
            blend = self.BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
            ok = bool(self.u.UpdateLayeredWindow(self.hwnd, screen, None, ctypes.byref(wt.SIZE(w, h)), mem,
                                                 ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), 2))  # ULW_ALPHA
            self.g.SelectObject(mem, old)
            self.g.DeleteObject(bmp)
        self.g.DeleteDC(mem)
        self.u.ReleaseDC(None, screen)
        return ok


def win_ex_style(hwnd: int, add: int = 0, remove: int = 0):
    u = ctypes.WinDLL("user32", use_last_error=True)
    get = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
    put = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
    get.argtypes, get.restype = [ctypes.c_void_p, ctypes.c_int], ctypes.c_ssize_t
    put.argtypes, put.restype = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
    style = get(hwnd, -20)  # GWL_EXSTYLE
    put(hwnd, -20, (style | add) & ~remove)


WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x8, 0x08000000


class App:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        version = int(self.cfg.get("capture_version") or 1)
        upgrade = version < 2
        if upgrade:
            # Older versions read the wrong screen on some two-screen PCs (and the lobby):
            # forget the pop-up spot they learned and the chests they counted from the screen.
            self.cfg["scan_region"] = None
        if version < 3:
            # The "let clicks go through" checkbox is gone: it made the overlay impossible to click
            # (and so to turn back off), while Fortnite keeps the mouse in game anyway.
            self.cfg["click_through"] = False
        self.cfg["capture_version"] = 4
        save_json(CONFIG_PATH, self.cfg)
        had_stats = STATS_PATH.exists()  # a brand-new install has nothing to clean up
        self.stats = Stats()
        if upgrade and had_stats:
            self.stats.forget_screen_counts()
        if version < 4 and had_stats:
            # versions before 1.11 missed new matches and counted their chests twice
            self.stats.drop_double_counted()
        self.team = Team(self)
        self.treasures = treasures.TreasureLog(DATA_DIR / "treasures.json")
        try:
            import match_recorder

            self.recorder = match_recorder.MatchRecorder(self, DATA_DIR / "recordings.json")
        except Exception:
            log.exception("match recorder")
            self.recorder = None
        self._treasure_said: set = set()
        self.detector = Detector(self)
        self.lan = LanLink(self)
        self.net = NetLink(self)
        self.replays = ReplayWatcher(self)
        self.gamelog = GameLog(self)
        self.map = None  # map window data (map_tracker.MapManager), started in run()
        self._pending: list = []
        self._lock = threading.Lock()
        self._flash_until = 0.0
        self._toast = ("", 0.0)
        self._dragging = False
        self.expanded = False  # click the overlay to show every player's counts
        self._settings = None
        self.layered: LayeredWindow | None = None
        self._unlocked = False      # taskbar button / F11 pressed during a match: the overlay takes clicks
        self._unlock_away = False   # ...and Fortnite has been in the background since
        self._through = None        # whether clicks currently go through the overlay
        self._collapse_at = None    # the team table opened by itself at the end of a match closes at this time

    # thread-safe helpers ------------------------------------------------------------
    def call_soon(self, fn):
        with self._lock:
            self._pending.append(fn)

    def toast(self, text: str, seconds: float = 4.0):
        self._toast = (text, time.monotonic() + seconds)

    def flash(self):
        self._flash_until = time.monotonic() + 1.2

    def manual_add(self):
        self.stats.add(1, manual=True)
        self.flash()

    def undo(self):
        self.stats.add(-1, manual=True)
        self.toast("Took back one chest", 2)

    def request_calibrate(self):
        if not self.detector.ready:
            self.toast("Still starting up, try again in a moment")
            return
        self.detector.calibrate_requested.set()

    # match events (from Fortnite's log) -----------------------------------------------
    def match_in_progress(self) -> bool:
        """True from loading into a match until the placement screen. Fortnite's log says so
        exactly; without it, a replay being recorded is the sign."""
        state = self.gamelog.match_state()
        if state is None:
            return self.replays.match_in_progress()
        return state == "match"

    LEAVE_AT_S = 14 * 60  # the overlay's match timer turns red: leave together and requeue (most chests per hour)

    def match_timer(self, now: float | None = None) -> tuple[str, bool]:
        """The overlay's tiny match clock ("12:34", red from 14:00) from joining the match; "" between matches."""
        t0 = getattr(self, "_match_t0", None)
        if not t0 or not self.cfg.get("match_timer", True):
            return "", False
        el = (time.time() if now is None else now) - t0
        if not 0 <= el < 45 * 60:
            return "", False
        el = int(el)
        return f"{el // 60}:{el % 60:02d}", el >= self.LEAVE_AT_S

    def on_match_start(self):
        self._unlocked = False
        self._match_t0 = time.time()
        if getattr(self, "recorder", None) is not None:
            self.recorder.match_started()
        if self.cfg.get("auto_new_game", True) and self.stats.new_game("match joined", debounce=20):
            self.toast("New game. Good luck!")

    def on_match_over(self, squad_out: bool):
        """The placement screen showed (squad_out) or I left the match: this game's live count is final."""
        self._match_t0 = None
        if getattr(self, "recorder", None) is not None:
            self.recorder.match_over()
        if not self.stats.finish():
            return
        n = self.stats.game
        msg = (f"Squad out! You: {n} chest{'s' * (n != 1)}" if squad_out else f"Game over: {n} chest{'s' * (n != 1)}")
        if self.team.has_team():
            msg += f". Team: {self.team.team_game()}"
        if self.cfg.get("replays", True) and self.replays.exe.exists():
            msg += ". Broken chests get added from the replay once you leave the match" if squad_out else                 ". Checking the replay for broken chests"
        self.toast(msg, 15 if squad_out else 8)
        self.flash()
        if self.team.has_team() and self.cfg.get("show_team_at_end", True):
            self.call_soon(lambda: self.show_team(30))

    def show_team(self, seconds: float):
        """Opens the team table for a while (it closes again by itself, or when you click elsewhere)."""
        if not self.expanded:
            self.expanded = True
            self._collapse_at = time.monotonic() + seconds
            self.redraw(force=True)

    def on_teammate_counts(self, m):
        """A teammate's state message: their own count for each recent match goes on this PC's replays (the
        app shows it instead of what my replay saw of them), and their recounted game (the replay adds the
        chests they broke) reaches the live map, which otherwise keeps the last number sent mid-match."""
        if not isinstance(m, dict) or m.get("app") != "chesttracker" or m.get("team") != self.team.key:
            return
        pid = str(m.get("id") or "")
        if not pid or pid == self.stats.id or "total" not in m:
            return
        with self.team.lock:
            p = dict(self.team.peers.get(pid) or {})
        mgr = getattr(self, "map", None)
        game = m.get("game")
        if mgr is not None and isinstance(game, int) and 0 <= game < 10000:
            with mgr.lock:
                q = mgr.team.get(pid)
                if q is not None and game > int(q.get("game") or 0):
                    q["game"] = game  # (only up: a new match's small count doesn't replace the last one)
            mgr.bump()
        games, names = p.get("games") or {}, {norm_name(n) for n in p.get("fortnite_names") or [] if n}
        replays = getattr(self, "replays", None)
        if not games or not names or replays is None:
            return
        key = (tuple(sorted(games.items())), tuple(sorted(names)), len(replays.db.files))
        cache = self.__dict__.setdefault("_own_applied", {})
        if cache.get(pid) == key:
            return
        cache[pid] = key
        n = replays.db.set_own_counts(names, games)
        if n:
            log.info("%s's own counts now on %d of this PC's replays", p.get("name") or "a teammate", n)
            self.stats.version += 1  # (the app's lists redraw)

    def on_replay_treasures(self, name: str, r: dict, started: float, how: str):
        """Treasure hunt prizes in a replay just read: mine get saved; fresh ones get a toast."""
        names = {norm_name(n) for n in self.cfg.get("fortnite_names") or [] if n}
        if getattr(self, "recorder", None) is not None:  # a real find (anyone's): keep that match's whole video
            self.recorder.replay_read(name, started, r.get("treasures"), treasures.describe)
        mine = treasures.mine(r.get("treasures"), names, how == "recorder")
        new = self.treasures.add(r.get("session_id") or name, mine, started or time.time())
        fresh = time.time() - (started or 0) < 3 * 3600  # this session's matches, not old replays
        for item in new:
            log.info("treasure found in %s: %s (%s)", name, item["name"], item["prize"])
            if fresh:
                self.announce_treasure("You", item)
        if fresh:  # teammates' finds this replay saw (their own tracker may share them too: said once)
            for t in r.get("treasures") or []:
                if t.get("who") == "teammate" and t.get("won", True) and norm_name(t.get("name")) not in names:
                    self.announce_treasure(str(t.get("name") or "A teammate")[:20],
                                           {"prize": t.get("prize"), "when": round((started or 0) + float(t.get("t") or 0))})

    def announce_treasure(self, who: str, item: dict):
        key = (item.get("prize"), round(float(item.get("when") or 0) / 120))
        if key in self._treasure_said:
            return
        self._treasure_said.add(key)
        rare = treasures.describe(item.get("prize"))
        self.toast(treasures.found_message(who, item), 25 if rare["pirate"] else 15)
        self.flash()

    def show_treasures(self):
        try:
            treasures.show_window(self)
        except Exception:
            log.exception("treasure window")

    def guest_names(self) -> dict[str, str]:
        """Console teammates set in settings ({normalized: as typed}), minus my own names."""
        mine = self.replays.my_names()
        names = {norm_name(n): n.strip() for n in self.cfg.get("console_teammates") or [] if norm_name(n)}
        return {n: shown for n, shown in names.items() if n not in mine}

    def guest_stats(self) -> dict[str, dict]:
        names = self.guest_names()
        live = getattr(self, "_live_entry", None)
        if live and (live[0] != self.stats.replay or live[0] in self.replays.db.files):
            live = self._live_entry = None  # a new game, or its replay has been read for real
        key = (getattr(self.replays.db, "version", 0), tuple(sorted(names)), self.stats.replay, live and live[2])
        cache = getattr(self, "_guest_cache", None)
        if cache is None or cache[0] != key:
            entries = self.replays.db.files
            if live:
                entries = {**entries, live[0]: live[1]}
            self._guest_cache = cache = (key, guest_stats(entries, names, self.stats.replay))
        return cache[1]

    def set_live_entry(self, name: str, entry: dict):
        """The current match's replay as read mid-match, so console teammates' counts move during it."""
        n = (getattr(self, "_live_entry", None) or (None, None, 0))[2] + 1
        self._live_entry = (name, entry, n)

    def apply_credits(self, m=None):
        """Adds chests a teammate's replays saw this player get on a console (once per match).
        Called with each credits message; called with nothing after the Fortnite names change."""
        if not hasattr(self, "_credit_msgs"):
            self._credit_msgs = {}
        if m is not None:
            if not isinstance(m, dict) or m.get("type") != "credits" or m.get("team") != self.team.key:
                return
            self._credit_msgs[str(m.get("from"))[:20]] = m
        names = self.replays.my_names()
        local = self.replays.db.sessions()
        added = matches = 0
        givers = set()
        for giver, msg in list(self._credit_msgs.items()):
            credits = msg.get("credits_v2") if isinstance(msg.get("credits_v2"), dict) else msg.get("credits")
            if not isinstance(credits, dict):
                continue
            for n in names:
                for match, vals in (credits.get(n) or {}).items():
                    try:
                        o, b, started = int(vals[0]), int(vals[1]), float(vals[2] or 0)
                        # (1.24+: broken chests their replay couldn't see, estimated: I was far from them)
                        b += max(0, min(200, int(vals[3]))) if len(vals) > 3 else 0
                    except (TypeError, ValueError, IndexError):
                        continue
                    if match in local or o < 0 or b < 0 or o + b > 500:
                        continue  # a match this PC recorded itself, or nonsense
                    delta = self.stats.credit_game(str(match)[:64], giver, started, o, b)
                    if delta is not None:
                        added += delta
                        matches += 1
                        givers.add(giver)
        if matches:
            log.info("credited %+d chests from %d matches (from %s)", added, matches, sorted(givers))
            if added > 0:
                self.toast(f"Added {added} chests from {matches} matches your PC has no replay of "
                           f"(seen in {', '.join(sorted(givers))}'s replays)", 10)

    def rows(self):
        if self.team.has_team():
            return [("TEAM GAME", self.team.team_game()), ("MY TOTAL", self.stats.total),
                    ("TEAM TOTAL", self.team.team_total())]
        return [("THIS GAME", self.stats.game), ("TOTAL", self.stats.total)]

    # window -------------------------------------------------------------------------
    def run(self):
        import tkinter as tk
        from PIL import ImageTk

        if IS_WIN:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
            try:
                # its own taskbar button and icon, instead of being grouped under Python
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChestTracker.Overlay")
            except Exception:
                pass

        root = tk.Tk()
        root.withdraw()
        self.root = root
        try:  # the app window's dark look for every window (map, match browser, settings)
            import app_window

            app_window.apply_theme(root, self.cfg.get("app_theme"))
        except Exception:
            log.exception("theme")
        try:
            from PIL import Image

            path = APP_DIR / "assets" / "icon.png"  # (the logo on a dark tile: readable on any taskbar)
            icon = Image.open(path if path.exists() else APP_DIR / "chest.png").convert("RGBA")
            self._icons = [ImageTk.PhotoImage(icon.resize((n, n), Image.LANCZOS)) for n in (64, 32, 16)]
            root.iconphoto(True, *self._icons)  # the logo for every window, including the taskbar
        except Exception:
            log.warning("could not set the window icon", exc_info=True)
        if not self.cfg.get("name"):
            first_run(self)
        self.painter = make_painter(self.cfg, float(self.cfg.get("overlay_scale") or 1.0))
        root.deiconify()
        root.title("Chest Tracker")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        key_hex = "#%02x%02x%02x" % KEY_COLOR
        root.configure(bg=key_hex)
        self.label = tk.Label(root, bd=0, bg=key_hex, highlightthickness=0)
        self.label.pack(fill="both", expand=True)
        x, y = self.cfg.get("overlay_x"), self.cfg.get("overlay_y")
        if x is None or y is None or not self._on_screen(int(x), int(y)):
            x, y = root.winfo_screenwidth() - self.painter.W - 24, 24
        root.geometry(f"{self.painter.W}x{self.painter.H}+{int(x)}+{int(y)}")
        root.update_idletasks()
        if IS_WIN:
            try:
                win_ex_style(self._hwnd(), add=WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE)
                self.layered = LayeredWindow(self._hwnd())
            except Exception:
                log.exception("per-pixel transparency unavailable, using a see-through color")
                self.layered = None
            if self.layered is None:
                root.attributes("-transparentcolor", key_hex)
            self._apply_click_through()

        # Bound on the window (root), not the label: with per-pixel transparency the label holds no
        # image and shrinks, so most clicks land on the window itself. Tk also passes the label's
        # events up to the root's bindings, so each click still fires exactly once.
        root.bind("<ButtonPress-1>", self._drag_start)
        root.bind("<B1-Motion>", self._drag_move)
        root.bind("<ButtonRelease-1>", self._drag_end)
        root.bind("<ButtonRelease-3>", lambda e: self.open_settings())

        self.detector.start()
        Hotkeys(self).start()
        if self.cfg.get("replays", True):
            self.replays.start()
        self.gamelog.start()
        if self.cfg.get("lan_sync", True):
            self.lan.start()
        self.net.start()
        self._start_map()
        self.toast("Starting chest detection... Right-click me for settings", 12)
        self._make_taskbar_button()

        self._last_frame = None
        self._ImageTk = ImageTk
        edition.start_personal_features(self)  # (only a copy with a personal/ folder has any)
        self._tick()
        root.mainloop()

    def _on_screen(self, x: int, y: int) -> bool:
        try:
            vx, vy = self.root.winfo_vrootx(), self.root.winfo_vrooty()
            vw, vh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
            if IS_WIN:
                u = ctypes.windll.user32
                vx, vy, vw, vh = u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)
            return vx - 50 <= x <= vx + vw - 60 and vy - 10 <= y <= vy + vh - 40
        except Exception:
            return True

    def _frame(self):
        now = time.monotonic()
        msg = self._toast[0] if now < self._toast[1] else ""
        if not msg and self.detector.error:
            msg = "Detection off. See tracker.log"
        flash = 1.0 if now < self._flash_until else 0.0
        full = self.expanded or (self.cfg.get("overlay_always_full") and self.team.has_team())  # (setting: keep it open)
        party = tuple(tuple(sorted(p.items())) for p in self.team.players()) if full else None
        return (self.stats.game, tuple(self.rows()), flash, msg, self.stats.confirmed is not None,
                round(float(self.cfg.get("bg_opacity", 0.85)), 2), self._dragging, party, self.painter.W,
                bool(self.stats.finished), self.match_timer())

    def redraw(self, force: bool = False):
        frame = self._frame()
        if frame == self._last_frame and not force:
            return
        self._last_frame = frame
        mine, rows, flash, msg, confirmed, opacity, dragging, party, _, final, (timer, late) = frame
        party = [dict(p) for p in party] if party is not None else None
        img = self.painter.draw(mine, list(rows), flash, msg, confirmed, opacity, highlight=dragging, party=party,
                                final=final, timer=timer, late=late)
        if img.size != (self.root.winfo_width(), self.root.winfo_height()):
            self.root.geometry(f"{img.size[0]}x{img.size[1]}")
        if self.layered is not None and self.layered.show(img):
            return
        from PIL import Image

        flat = Image.new("RGB", img.size, KEY_COLOR)
        flat.paste(img, (0, 0), img)
        self._photo = self._ImageTk.PhotoImage(flat)
        self.label.configure(image=self._photo)

    def _tick(self):
        with self._lock:
            pending, self._pending = self._pending, []
        for fn in pending:
            try:
                fn()
            except Exception:
                log.exception("action failed")
        if self.expanded and (self._clicked_elsewhere()
                              or (self._collapse_at is not None and time.monotonic() > self._collapse_at)):
            self.expanded = False
        if not self.expanded:
            self._collapse_at = None
        self._update_lock()
        self.redraw()
        if IS_WIN and int(time.monotonic()) % 2 == 0:
            self._keep_on_top()
        self.root.after(100, self._tick)

    def _clicked_elsewhere(self) -> bool:
        """True when a mouse button is down outside the overlay (the overlay never takes focus,
        so Tk's focus-out can't be used for this)."""
        if not IS_WIN:
            return False
        u = ctypes.windll.user32
        if not any(u.GetAsyncKeyState(vk) & 0x8000 for vk in (0x01, 0x02, 0x04)):
            return False
        from ctypes import wintypes

        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        x, y = self.root.winfo_x(), self.root.winfo_y()
        inside = x <= pt.x < x + self.root.winfo_width() and y <= pt.y < y + self.root.winfo_height()
        if not inside and self._settings is not None:
            try:  # clicks in the settings window don't count as "clicked off"
                sw = self._settings.win
                inside = (sw.winfo_rootx() <= pt.x < sw.winfo_rootx() + sw.winfo_width()
                          and sw.winfo_rooty() <= pt.y < sw.winfo_rooty() + sw.winfo_height())
            except Exception:
                pass
        return not inside

    # Windows window tricks ---------------------------------------------------------------
    def _hwnd(self):
        return ctypes.windll.user32.GetParent(self.root.winfo_id())

    def _apply_click_through(self):
        if IS_WIN:
            through = bool(self.cfg.get("click_through")) or self.locked()
            if through != self._through:
                self._through = through
                if through:
                    win_ex_style(self._hwnd(), add=WS_EX_TRANSPARENT)
                else:
                    win_ex_style(self._hwnd(), remove=WS_EX_TRANSPARENT)
                log.info("overlay %s", "locked: clicks go to the game" if through else "unlocked")

    def locked(self) -> bool:
        """During a match the overlay is locked in place and clicks go through it to the game, so a
        click that lands on it never interrupts a fight. The taskbar button (or F11) unlocks it until
        you click back into Fortnite."""
        if not self.cfg.get("lock_in_match", True) or self._unlocked:
            return False
        state = self.gamelog.match_state()
        if state is None:  # no log to go by: only lock while a replay is really being recorded
            return self.replays.match_in_progress(strict=True)
        return state == "match"

    def _update_lock(self):
        if self._unlocked:
            if not fortnite_in_front():
                self._unlock_away = True
            elif self._unlock_away:
                self._unlocked = False  # back in the game: lock again
        self._apply_click_through()

    def _keep_on_top(self):
        HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = -1, 0x1, 0x2, 0x10
        try:
            ctypes.windll.user32.SetWindowPos(self._hwnd(), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        except Exception:
            pass

    # mouse: click to expand, drag to move, right-click for settings ----------------------
    DRAG_THRESHOLD = 5  # pixels the mouse must move before a click becomes a drag

    def _drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        self._press = (e.x_root, e.y_root)

    def _drag_move(self, e):
        px, py = getattr(self, "_press", (e.x_root, e.y_root))
        if not self._dragging and abs(e.x_root - px) + abs(e.y_root - py) < self.DRAG_THRESHOLD:
            return
        dx, dy = getattr(self, "_drag", (0, 0))
        self._dragging = True
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _drag_end(self, e):
        if self._dragging:
            self._dragging = False
            self.cfg["overlay_x"], self.cfg["overlay_y"] = self.root.winfo_x(), self.root.winfo_y()
            save_json(CONFIG_PATH, self.cfg)
        else:
            self.toggle_expanded()

    def toggle_expanded(self):
        self.expanded = not self.expanded
        self.redraw(force=True)

    # settings ---------------------------------------------------------------------------
    def set_opacity(self, value: float):
        self.cfg["bg_opacity"] = round(max(0.0, min(1.0, value)), 2)

    def set_scale(self, value: float):
        value = round(max(0.6, min(2.5, value)), 2)
        if value != self.cfg.get("overlay_scale"):
            self.cfg["overlay_scale"] = value
            self.painter = make_painter(self.cfg, value)
            self.root.geometry(f"{self.painter.W}x{self.painter.H}")
            self.redraw(force=True)

    def set_click_through(self, on: bool):
        self.cfg["click_through"] = bool(on)
        self._apply_click_through()
        save_json(CONFIG_PATH, self.cfg)

    def _make_taskbar_button(self):
        """A "Chest Tracker" button on the taskbar. The overlay itself stays off the taskbar (so it
        can't be minimized or take focus by accident); instead a tiny helper window sits minimized
        there. Clicking the button opens settings; right-click > Close window quits."""
        import tkinter as tk

        tb = self.taskbar = tk.Toplevel(self.root)
        tb.title("Chest Tracker")
        tb.geometry("1x1+-32000+-32000")  # never actually seen
        tb.protocol("WM_DELETE_WINDOW", self.quit)
        tb.iconify()

        def restored(_event=None):
            if tb.state() == "normal":  # the button was clicked
                tb.iconify()
                self.call_soon(self.open_settings)

        tb.bind("<Map>", lambda e: tb.after(10, restored))

    def quit(self):
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        log.info("quit")
        save_json(CONFIG_PATH, self.cfg)
        self.net.shutdown()
        self.root.destroy()

    def _start_map(self):
        """The map window's data: live position from the minimap, routes from replays (map_tracker.py)."""
        try:
            import map_tracker

            self.map = map_tracker.MapManager(self, DATA_DIR, DEMOS_DIR, READER_EXE, map_tracker.fortnite_client_rect,
                                              fortnite_in_front)
            self.map.save_cfg = lambda: save_json(CONFIG_PATH, self.cfg)  # (where it found the minimap)
            self.map.start()
        except Exception:
            log.exception("map unavailable")
            self.map = None
            return
        if self.cfg.get("map_window"):
            try:
                self.open_map()
            except Exception:
                log.exception("map window failed to open")

    def open_map(self):
        if self.map is None:
            self.toast("The map isn't available. See tracker.log")
            return
        if getattr(self, "_map_window", None) is None:
            from map_window import MapWindow

            self._map_window = MapWindow(self, self.map, lambda: save_json(CONFIG_PATH, self.cfg))
        self._map_window.show()

    def open_settings(self):
        if self.locked():
            self.toast("Unlocked: drag me now. Click back into Fortnite to lock me again", 8)
        self._unlocked, self._unlock_away = True, False
        try:  # the app window (app_window.py); the small settings window below is the fallback
            import app_window

            app_window.show(self)
            return
        except Exception:
            log.exception("app window failed to open; showing the small settings window")
        if self._settings is not None:
            try:
                self._settings.deiconify()
                self._settings.lift()
                self._settings.focus_force()
                return
            except Exception:
                self._settings = None
        self._settings = SettingsWindow(self)


class SettingsWindow:
    """Right-click menu: opacity and size sliders, name, team code."""

    def __init__(self, app: App):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self._size_pending = None  # set before the sliders: setting their start values calls the handlers
        cfg = app.cfg
        w = self.win = tk.Toplevel(app.root)
        w.title(f"Chest Tracker settings - version {APP_VERSION}")
        w.attributes("-topmost", True)
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self.close)
        f = ttk.Frame(w, padding=16)
        f.grid(sticky="nsew")
        f.columnconfigure(1, weight=1)
        head = ("Segoe UI Semibold", 11)
        row = 0

        ttk.Label(f, text="Overlay", font=head).grid(row=row, column=0, columnspan=3, sticky="w"); row += 1
        ttk.Label(f, text="Background").grid(row=row, column=0, sticky="w", pady=4)
        self.op_text = ttk.Label(f, width=5, anchor="e")
        self.op = ttk.Scale(f, from_=0, to=100, length=220, command=self._opacity)
        self.op.set(float(cfg.get("bg_opacity", 0.85)) * 100)
        self.op.grid(row=row, column=1, sticky="ew", padx=8)
        self.op_text.grid(row=row, column=2, sticky="e"); row += 1
        ttk.Label(f, text="Size").grid(row=row, column=0, sticky="w", pady=4)
        self.size_text = ttk.Label(f, width=5, anchor="e")
        self.size = ttk.Scale(f, from_=60, to=250, length=220, command=self._size_moved)
        self.size.set(float(cfg.get("overlay_scale") or 1.0) * 100)
        self.size.grid(row=row, column=1, sticky="ew", padx=8)
        self.size_text.grid(row=row, column=2, sticky="e"); row += 1
        self.lock = tk.BooleanVar(value=bool(cfg.get("lock_in_match", True)))
        ttk.Checkbutton(f, text="Lock it during matches (clicks go through to the game)", variable=self.lock,
                        command=self._lock_changed).grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        row += 1
        self.team_end = tk.BooleanVar(value=bool(cfg.get("show_team_at_end", True)))
        ttk.Checkbutton(f, text="Show everyone's chests when the squad is out", variable=self.team_end,
                        command=self._team_end_changed).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        ttk.Label(f, text="Drag the overlay to move it. Click it to show every player's chests.\n"
                          "In a match it's locked: click Chest Tracker on the taskbar to unlock it\n"
                          "until you click back into Fortnite. F9 shows everyone's chests, F11 this window.",
                  foreground="#666", justify="left").grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 12))
        row += 1

        ttk.Label(f, text="Team", font=head).grid(row=row, column=0, columnspan=3, sticky="w"); row += 1
        ttk.Label(f, text="Your name").grid(row=row, column=0, sticky="w", pady=4)
        self.name = tk.StringVar(value=cfg.get("name", ""))
        ttk.Entry(f, textvariable=self.name, width=24).grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0)); row += 1
        ttk.Label(f, text="Fortnite name(s)").grid(row=row, column=0, sticky="w", pady=4)
        self.fn = tk.StringVar(value=", ".join(cfg.get("fortnite_names") or []))
        fn_entry = ttk.Entry(f, textvariable=self.fn, width=24)
        fn_entry.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0)); row += 1
        # saved as soon as you press Enter or click elsewhere (not only when the window closes)
        fn_entry.bind("<Return>", lambda e: self._save_fortnite_names())
        fn_entry.bind("<FocusOut>", lambda e: self._save_fortnite_names())
        self.fn_note = ttk.Label(f, text="Only these players' chests count for you. Add your console name too\n"
                                         "(e.g. your PlayStation name), separated by commas.", foreground="#666",
                                 justify="left")
        self.fn_note.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 8)); row += 1
        ttk.Label(f, text="Console teammates").grid(row=row, column=0, sticky="w", pady=4)
        self.guests = tk.StringVar(value=", ".join(cfg.get("console_teammates") or []))
        g_entry = ttk.Entry(f, textvariable=self.guests, width=24)
        g_entry.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0)); row += 1
        g_entry.bind("<Return>", lambda e: self._save_guests())
        g_entry.bind("<FocusOut>", lambda e: self._save_guests())
        self.g_note = ttk.Label(f, text="Fortnite names of teammates who play on console without a tracker,\n"
                                        "separated by commas. Their chests in your games count for the team.",
                                foreground="#666", justify="left")
        self.g_note.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 8)); row += 1
        ttk.Label(f, text="Team code").grid(row=row, column=0, sticky="w", pady=4)
        self.code = tk.StringVar(value=cfg.get("team_code", ""))
        ttk.Entry(f, textvariable=self.code, width=24).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(f, text="Join", command=self._join).grid(row=row, column=2, sticky="e"); row += 1
        ttk.Label(f, text="Everyone who enters the same code shares their chest counts live.", foreground="#666").grid(
            row=row, column=0, columnspan=3, sticky="w"); row += 1
        self.status = ttk.Label(f, foreground="#2a7a3a", wraplength=330, justify="left")
        self.status.grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 12)); row += 1

        b = ttk.Frame(f)
        b.grid(row=row, column=0, columnspan=3, sticky="ew")
        ttk.Button(b, text="Reset this game", command=lambda: app.stats.new_game("reset in settings")).pack(side="left")
        ttk.Button(b, text="Map window", command=app.open_map).pack(side="left", padx=(8, 0))
        ttk.Button(b, text="Treasures", command=app.show_treasures).pack(side="left", padx=(8, 0))
        ttk.Button(b, text="Quit Chest Tracker", command=app.quit).pack(side="left", padx=8)
        ttk.Button(b, text="Close", command=self.close).pack(side="right")

        w.update_idletasks()
        ox, oy = app.root.winfo_x(), app.root.winfo_y() + app.painter.H + 8
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"+{max(0, min(ox, sw - w.winfo_width() - 8))}+{max(0, min(oy, sh - w.winfo_height() - 48))}")
        w.focus_force()
        self._refresh()

    def _lock_changed(self):
        self.app.cfg["lock_in_match"] = bool(self.lock.get())
        save_json(CONFIG_PATH, self.app.cfg)

    def _team_end_changed(self):
        self.app.cfg["show_team_at_end"] = bool(self.team_end.get())
        save_json(CONFIG_PATH, self.app.cfg)

    def _opacity(self, v):
        self.app.set_opacity(float(v) / 100)
        self.op_text.configure(text=f"{float(v):.0f}%")

    def _size_moved(self, v):
        self.size_text.configure(text=f"{float(v):.0f}%")
        if self._size_pending:
            self.win.after_cancel(self._size_pending)
        self._size_pending = self.win.after(150, lambda: self.app.set_scale(float(self.size.get()) / 100))

    def _save_fortnite_names(self):
        names = [n.strip()[:32] for n in self.fn.get().split(",") if n.strip()][:6]
        if [norm_name(n) for n in names] != [norm_name(n) for n in self.app.cfg.get("fortnite_names") or []]:
            self.app.cfg["fortnite_names"] = names
            save_json(CONFIG_PATH, self.app.cfg)
            log.info("Fortnite names set to %s", names)
            try:
                self.fn_note.configure(text=f"Saved: counting chests for {', '.join(names) or 'you'}.",
                                       foreground="#2a7a3a")
            except Exception:
                pass

            def recount():
                try:
                    self.app.replays.reattribute()
                    self.app.apply_credits()
                    self.app.toast(f"Counting chests for {', '.join(names) or 'you'}. Total {self.app.stats.total}", 8)
                except Exception:
                    log.exception("recount failed")

            threading.Thread(target=recount, daemon=True).start()

    def _save_guests(self):
        names = [n.strip()[:32] for n in self.guests.get().split(",") if n.strip()][:8]
        if [norm_name(n) for n in names] != [norm_name(n) for n in self.app.cfg.get("console_teammates") or []]:
            self.app.cfg["console_teammates"] = names
            save_json(CONFIG_PATH, self.app.cfg)
            log.info("console teammates set to %s", names)
            try:
                stats = self.app.guest_stats()
                found = ", ".join(f"{g['name']} {g['total']} chests in {g['matches']} matches" for g in stats.values())
                self.g_note.configure(text=f"Saved. {found or 'No console teammates.'}", foreground="#2a7a3a")
            except Exception:
                log.exception("console teammates")

    def _join(self):
        self._save_fortnite_names()
        self._save_guests()
        name = self.name.get().strip()[:20]
        if name and name != self.app.cfg.get("name"):
            self.app.cfg["name"] = name
        code = self.code.get().strip()
        if normalize_code(code) != normalize_code(self.app.cfg.get("team_code")):
            self.app.team.set_code(code)
            self.app.net.wake.set()
            self.app.toast(f"Joined team {code}" if code else "Left the team", 4)
        save_json(CONFIG_PATH, self.app.cfg)
        self._refresh(once=True)

    def _refresh(self, once: bool = False):
        try:
            if not self.win.winfo_exists():
                return
        except Exception:
            return
        app = self.app
        if not app.team.key:
            text = "No team code yet. Make one up (like SIBLINGS42) and give your siblings the same code."
        else:
            names = [f"{p['name'] or '?'} ({p['game']} this game)" for p in app.team.online()]
            net = app.net.status if internet_on(app.cfg) else ("offline mode" if app.cfg.get("offline") else "internet sync off")
            text = (f"Internet: {net}.\n" + (f"Online now: {', '.join(names)}" if names else
                                              "Waiting for teammates. They need the same code."))
        self.status.configure(text=text)
        if not once:
            self.win.after(1000, self._refresh)

    def close(self):
        self._save_fortnite_names()
        self._save_guests()
        name = self.name.get().strip()[:20]
        if name:
            self.app.cfg["name"] = name
        save_json(CONFIG_PATH, self.app.cfg)
        self.app._settings = None
        self.win.destroy()


SETUP_TITLE = "Chest Tracker - first-time setup"  # (setup_wizard.TITLE is the same)


def first_run(app: App):
    """The first-time setup window (setup_wizard.py); the plain one below if that fails for any reason."""
    try:
        import setup_wizard

        setup_wizard.run(app, lambda cfg: save_json(CONFIG_PATH, cfg), DEMOS_DIR, READER_EXE,
                         APP_DIR / "assets" / "logo.png")
        return
    except Exception:
        log.exception("setup window failed; using the plain one")
    plain_first_run(app)


def plain_first_run(app: App):
    """Asks for the name siblings will see and the team code, in one window.

    The window is always on top: on a first start Windows often doesn't let a just-started program
    come to the front (for example right after an installer), and a normal dialog would then sit
    hidden behind other windows while the app waits for it."""
    import getpass
    import tkinter as tk
    from tkinter import ttk

    try:
        default = getpass.getuser()
    except Exception:
        default = "Player"
    w = tk.Toplevel(app.root)
    w.title(SETUP_TITLE)
    w.attributes("-topmost", True)
    w.resizable(False, False)
    f = ttk.Frame(w, padding=18)
    f.grid()
    ttk.Label(f, text="Welcome to Chest Tracker!", font=("Segoe UI Semibold", 13)).grid(row=0, column=0, columnspan=2,
                                                                                    sticky="w", pady=(0, 10))
    ttk.Label(f, text="Your name (what your team sees):").grid(row=1, column=0, sticky="w")
    name = tk.StringVar(value=default[:20])
    e1 = ttk.Entry(f, textvariable=name, width=28)
    e1.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(2, 10))
    ttk.Label(f, text="Team code (everyone on your team types the same one, like SIBLINGS42):").grid(
        row=3, column=0, columnspan=2, sticky="w")
    code = tk.StringVar()
    ttk.Entry(f, textvariable=code, width=28).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(2, 4))
    ttk.Label(f, text="You can leave it empty and add it later in settings.", foreground="#666").grid(
        row=5, column=0, columnspan=2, sticky="w", pady=(0, 12))
    done = tk.BooleanVar(value=False)
    ttk.Button(f, text="Start", command=lambda: done.set(True)).grid(row=6, column=1, sticky="e")
    w.bind("<Return>", lambda e: done.set(True))
    w.protocol("WM_DELETE_WINDOW", lambda: done.set(True))

    w.update_idletasks()
    sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
    w.geometry(f"+{(sw - w.winfo_width()) // 2}+{(sh - w.winfo_height()) // 3}")
    w.lift()
    w.focus_force()
    e1.focus_set()
    e1.select_range(0, "end")
    log.info("first-time setup window shown")
    w.wait_variable(done)

    app.cfg["name"] = (name.get().strip() or default)[:20]
    save_json(CONFIG_PATH, app.cfg)
    if code.get().strip():
        app.team.set_code(code.get().strip())
    w.destroy()
    log.info("first-time setup done (name set, team code %s)", "set" if code.get().strip() else "skipped")


def show_running_copy(quiet: bool = False) -> None:
    """Called when Chest Tracker is started while it's already running: bring the running copy's
    setup window to the front if it's waiting for an answer, otherwise say where the app is
    (unless quiet: the launcher says it in its own window)."""
    if not IS_WIN:
        return
    from ctypes import wintypes

    u = ctypes.windll.user32
    me = os.getpid()
    setup = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        title = ctypes.create_unicode_buffer(128)
        u.GetWindowTextW(h, title, 128)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value != me and title.value == SETUP_TITLE:
            setup.append(h)
        return True

    u.EnumWindows(visit, 0)
    if setup:
        u.ShowWindow(setup[0], 9)  # SW_RESTORE
        u.SetForegroundWindow(setup[0])  # allowed: this copy was just started by the user
        return
    if quiet:
        return
    u.MessageBoxW(None, "Chest Tracker is already running.\n\n"
                        "The overlay is in the top-right corner of your main screen, and the chest button "
                        "on the taskbar opens its settings (right-click it > Close window to quit).",
                  "Chest Tracker", 0x40 | 0x40000 | 0x10000)  # MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND


if IS_WIN:
    import ctypes  # noqa: E402
else:
    ctypes = None  # type: ignore


_instance_lock = None


def already_running() -> bool:
    """Only one Chest Tracker at a time: two would both count every chest and fight over stats.json."""
    global _instance_lock
    if not IS_WIN:
        return False
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes, k.CreateMutexW.restype = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE
    name = os.environ.get("CHEST_TRACKER_INSTANCE", "ChestTrackerSingleInstance")  # tests use their own
    _instance_lock = k.CreateMutexW(None, False, "Local\\" + name)
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if load_json(CONFIG_PATH, DEFAULT_CONFIG).get("debug") else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )
    if already_running():
        log.info("Chest Tracker is already running; not starting a second copy")
        show_running_copy(quiet=bool(os.environ.get("CHEST_LAUNCHER")))
        sys.exit(3)  # the launcher recognises this and says so in its window
    log.info("Chest Tracker %s starting from %s", APP_VERSION, APP_DIR)
    try:
        App().run()
    except Exception:
        log.exception("Chest Tracker crashed")
        try:
            from tkinter import messagebox

            messagebox.showerror("Chest Tracker", f"Chest Tracker stopped because of an error.\nDetails are in:\n{LOG_PATH}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
