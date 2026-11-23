"""What the app window shows, worked out from the files the tracker already keeps (no Tk, no I/O):

  * stats.json history  -> one row per game: chests, opened, broken, how it was counted;
  * replays.json        -> who was in the squad, a console teammate's counts, the replay's session id;
  * maps/*.map.json     -> chests broken by placing a build, eliminations, how long the match was;
  * treasures.json      -> treasure hunt prizes found in that match.

Everything here is plain functions over dicts, so the window (app_window.py) stays thin and these
can be tested without a screen.
"""
from __future__ import annotations

import re
import time

import treasures

DAY = 86400
PLATFORMS = {"WIN": "PC", "MAC": "Mac", "PSN": "PlayStation", "PS4": "PlayStation", "PS5": "PlayStation",
             "XBL": "Xbox", "XSX": "Xbox", "XB1": "Xbox", "SWT": "Switch", "SW2": "Switch", "AND": "Android",
             "IOS": "iOS"}


def norm(name) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def platform_name(code) -> str:
    c = str(code or "").upper()
    return PLATFORMS.get(c, c.title() if c else "")


def is_me(name, recorder: bool, names: set[str]) -> bool:
    """A player in a replay is me: by in-game name, or (no names set yet) the one who recorded it."""
    return norm(name) in names if names else bool(recorder)


# --------------------------------------------------------------------------------------------
# Per-game rows
# --------------------------------------------------------------------------------------------
def my_map_stats(rec: dict | None, names: set[str], pois) -> dict | None:
    """map_tracker.player_stats for me in a replay's map record (where I landed, time alive, chests a
    minute, first chest, distances), or None. Cached per record."""
    if not rec or not isinstance(rec.get("map"), dict):
        return None
    me = next((p for p in rec["map"].get("players", []) if is_me(p.get("name"), p.get("recorder"), names)), None)
    if me is None:
        return None
    key = (rec.get("file"), id(rec), me.get("name"), len(pois or ()))
    hit = _STATS_CACHE.get(key)
    if hit is None:
        import map_tracker

        try:
            hit = map_tracker.player_stats(rec, me, pois or ())
        except Exception:
            hit = {}
        if len(_STATS_CACHE) > 2000:
            _STATS_CACHE.clear()
        _STATS_CACHE[key] = hit
    return hit or None


_STATS_CACHE: dict = {}


def replay_counts(e: dict | None) -> dict[str, tuple[int, int]]:
    """{player name: (opened, broken)} for one match, from its replays.json entry: the recorder (raw) and
    every teammate the reader saw. The whole match's real numbers, far-away teammates included."""
    out: dict[str, tuple[int, int]] = {}
    if not e:
        return out
    raw = e.get("raw") or {}
    rec = e.get("recorder") or raw.get("recorder_name")
    if rec:
        out[rec] = (int(raw.get("opened") or 0), int(raw.get("broken") or 0))
    for t in e.get("teammates") or []:
        if t.get("name"):
            out[t["name"]] = (int(t.get("opened") or 0), int(t.get("broken") or 0))
    return out


def drop_spots(recs: list, names: set[str], pois) -> list[dict]:
    """map_tracker.drop_report for me: per landing place, games, average chests / elims / time alive,
    best game. Best average first."""
    import map_tracker

    return map_tracker.drop_report(recs, lambda n, rec: is_me(n, rec, names), pois or ())


def _map_numbers(rec: dict | None, names: set[str]) -> tuple[int | None, int | None, float | None]:
    """(broken by a build, eliminations, match length s) for me in a replay's map record."""
    if not rec or not isinstance(rec.get("map"), dict):
        return None, None, None
    m = rec["map"]
    mine = {p.get("name") for p in m.get("players", []) if is_me(p.get("name"), p.get("recorder"), names)}
    if not mine:
        return None, None, rec.get("length")
    by_build = sum(1 for c in m.get("chests", []) if c.get("who") in mine and c.get("by_build"))
    kills = sum(len(p.get("kills") or []) for p in m.get("players", []) if p.get("name") in mine)
    return by_build, kills, rec.get("length")


def _squad(e: dict, names: set[str]) -> list[dict]:
    """The other players in my squad in one replay: [{"name", "platform", "chests"}]."""
    out = []
    rec = e.get("recorder")
    if rec and e.get("how") in ("teammate", "absent") and not is_me(rec, True, names):
        raw = e.get("raw") or {}
        out.append({"name": rec, "platform": platform_name(raw.get("recorder_platform")),
                    "chests": int(raw.get("opened") or 0) + int(raw.get("broken") or 0), "recorder": True})
    for t in e.get("teammates") or []:
        if t.get("bot") or is_me(t.get("name"), False, names) or not t.get("name"):
            continue
        out.append({"name": t.get("name"), "platform": platform_name(t.get("platform")),
                    "chests": teammate_chests(t), "own": "own" in t, "recorder": False})
    return out


# Broken chests per opened chest that a replay misses while a teammate is far from the recorder (a chest
# broken out of the recorder's view never reaches the replay; their opens do, by the search flag). Learned
# from teammates who run their own tracker (see chest_tracker.ReplayDB.break_rate); 0.3 until then.
# Nov 20, 2026, 21 matches of Falcon: replays saw 79% of his own count; with this, about right in total.
BREAK_RATE = 0.3
# Off (Nov 20, 2026): checked by hand on one game, the estimate made Comet 18 and Brave 41 where they really got
# 12 and 32 (the replay already counted crates and barrels as chests, and they broke few). "far" is still recorded.
ESTIMATE_FAR_BROKEN = False


def far_broken(t: dict, rate: float | None = None) -> int:
    """Broken chests a replay can't have seen: the teammate's opens while far from the recorder ("far": their
    share of searches made over FAR_M away) times the squad's break rate."""
    far = t.get("far")
    if not isinstance(far, (int, float)) or far <= 0:
        return 0
    return int(round((BREAK_RATE if rate is None else rate) * int(t.get("opened") or 0) * min(1.0, float(far))))


def teammate_chests(t: dict) -> int:
    """A squadmate's chests in a match: their own tracker's count when it shared one ("own"), else what this
    PC's replay saw (plus, if ESTIMATE_FAR_BROKEN, the broken chests it couldn't see: far_broken)."""
    own = t.get("own")
    if isinstance(own, (int, float)) and own >= 0:
        return int(own)
    return int(t.get("opened") or 0) + int(t.get("broken") or 0) + (far_broken(t) if ESTIMATE_FAR_BROKEN else 0)


def _my_teammate_entry(e: dict, names: set[str]) -> dict | None:
    for t in e.get("teammates") or []:
        if names and norm(t.get("name")) in names:
            return t
    return None


MAX_MATCH_S = 30 * 60  # longer than any battle royale match: a time span past this isn't one match


def play_seconds(h: dict, length, now: float | None = None) -> tuple[float | None, bool]:
    """How long I was in a game's match: (seconds, estimated). The replay's length when known; otherwise
    from when the match ended (placement screen / leaving), or roughly from when the next game began
    (that includes the lobby in between: estimated). (None, False) when it can't be told."""
    if length:
        return float(length), False
    started = float(h.get("started") or 0)
    if not started:
        return None, False
    if h.get("finished"):
        s = float(h["finished"]) - started
        return (s, False) if 0 < s <= MAX_MATCH_S else (None, False)
    if h.get("current"):
        s = (time.time() if now is None else now) - started
        return (s, False) if 0 < s <= MAX_MATCH_S else (None, False)
    if h.get("ended"):
        s = float(h["ended"]) - started
        return (s, True) if 120 <= s <= MAX_MATCH_S else (None, False)
    return None, False


def per_minute(chests, seconds) -> float | None:
    return chests / (seconds / 60) if seconds and seconds >= 60 else None


def match_rows(history: list, current: dict | None, files: dict, map_recs: dict, treasure_items: list,
               names: set[str], pois=None, now: float | None = None) -> list[dict]:
    """One row per game, newest first.

    history: stats.json "history"; current: the game being counted now (same keys, plus "finished");
    files: replays.json "files"; map_recs: {replay file name: map record}; treasure_items: TreasureLog items;
    pois: named places [(name, x, y)] for "landed at" (None: no map stats).
    """
    by_match: dict[str, list] = {}
    for x in treasure_items or []:
        by_match.setdefault(str(x.get("match")), []).append(x)
    games = [dict(h, current=False) for h in history or []]
    if current and (current.get("chests") or current.get("replay")):  # (an empty game nobody played yet: left out)
        games.append(dict(current, current=True))
    rows = []
    for h in games:
        replay = h.get("replay")
        e = files.get(replay) or {} if replay else {}
        conf = h.get("confirmed") if isinstance(h.get("confirmed"), dict) else None
        opened = int(conf.get("opened", 0)) if conf else int(h.get("seen") or 0)
        broken = int(conf.get("broken", 0)) if conf else None
        by_build, kills, length = _map_numbers(map_recs.get(replay) if replay else None, names)
        if by_build is None and e.get("how") == "teammate":
            t = _my_teammate_entry(e, names)
            if t is not None and "build_breaks" in t:
                by_build = int(t.get("build_breaks") or 0)
        started = float(h.get("started") or 0)
        if length is None and h.get("imported") and h.get("ended"):
            length = max(0.0, float(h["ended"]) - started)
        if e.get("how") == "absent":
            source = f"Played by {e.get('recorder') or 'someone else'}"  # someone else's match on this PC
        elif h.get("credited_by"):
            source = (f"Live + {h['credited_by']}'s replay" if int(h.get("seen") or 0) else
                      f"From {h['credited_by']}'s replay")
        elif h.get("imported"):
            source = "Replay"
        elif conf:
            source = "Live + replay"
        elif h.get("current") and not h.get("finished"):
            source = "Live (playing)"
        else:
            source = "Live"
        found = []
        for key in {e.get("session"), replay, h.get("match")} - {None, ""}:
            found += by_match.get(str(key), [])
        found.sort(key=lambda x: x.get("t") or 0)
        squad = _squad(e, names)
        chests = int(h.get("chests") or 0)
        if conf and broken is not None:
            # the count takes each opened chest from the live pop-ups or the replay, whichever saw it (see
            # chest_tracker.game_count): the replay's own "opened" can be lower, so "opened + broken" in the
            # app wouldn't add up to the game's number (76 shown, "57 opened + 16 broken" under it)
            opened = max(0, chests - broken)
        # the squad's total: me plus everyone the replay saw with me (only when this PC has that replay)
        squad_total = (chests + sum(q["chests"] for q in squad)) if (squad and e.get("how") != "absent") else None
        play, play_est = play_seconds(h, length, now)
        rows.append({
            "play": play, "play_est": play_est, "per_min": per_minute(chests, play),
            "started": started, "length": length, "chests": chests, "opened": opened, "squad_total": squad_total,
            "broken": broken, "by_build": by_build, "kills": kills, "treasures": found,
            "squad": squad, "source": source, "replay": replay, "current": bool(h.get("current")),
            "confirmed": conf is not None, "how": e.get("how"), "not_me": e.get("how") == "absent",
            "credited_by": h.get("credited_by"),
            "map": my_map_stats(map_recs.get(replay), names, pois) if (replay and pois is not None) else None,
            "on_map": bool(replay and replay in map_recs),
        })
    rows.sort(key=lambda r: (r["current"], r["started"]), reverse=True)
    return rows


def filter_rows(rows: list[dict], days: float | None, now: float | None = None) -> list[dict]:
    if days is None:
        return list(rows)
    now = time.time() if now is None else now
    if days == 1:  # "today": since local midnight
        lt = time.localtime(now)
        since = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    else:
        since = now - days * DAY
    return [r for r in rows if r["started"] >= since or r["current"]]


def summary(rows: list[dict], now: float | None = None) -> dict:
    """Totals over some games (rows from match_rows)."""
    now = time.time() if now is None else now
    played = [r for r in rows if r["chests"] or r["replay"]]
    best = max(played, key=lambda r: r["chests"], default=None)
    today = filter_rows(rows, 1, now)
    return {
        "games": len(played),
        "chests": sum(r["chests"] for r in rows),
        "opened": sum(r["opened"] for r in rows),
        "broken": sum(r["broken"] or 0 for r in rows),
        "squad_chests": sum(r["squad_total"] or r["chests"] for r in rows),
        "by_build": sum(r["by_build"] or 0 for r in rows),
        "kills": sum(r["kills"] or 0 for r in rows),
        "treasures": sum(len(r["treasures"]) for r in rows),
        "avg": (sum(r["chests"] for r in played) / len(played)) if played else 0.0,
        "best": best["chests"] if best else 0,
        "best_when": best["started"] if best else None,
        "today": sum(r["chests"] for r in today),
        "today_games": len([r for r in today if r["chests"] or r["replay"]]),
        **playtime(rows),
        "today_per_min": playtime(today)["per_min"],
    }


def playtime(rows: list[dict]) -> dict:
    """Time in matches and chests per minute over some games. Games that can't be timed are left out of
    both (so the rate stays honest) and counted in "untimed"."""
    played = [r for r in rows if (r["chests"] or r["replay"]) and not r.get("not_me")]
    timed = [r for r in played if r.get("play")]
    secs = sum(r["play"] for r in timed)
    return {"play_s": secs, "per_min": per_minute(sum(r["chests"] for r in timed), secs),
            "untimed": len(played) - len(timed), "play_est": any(r.get("play_est") for r in timed)}


def daily(rows: list[dict], days: int = 14, now: float | None = None) -> list[dict]:
    """Chests per day for the last `days` days, oldest first: [{"day", "label", "opened", "broken", "chests"}]."""
    now = time.time() if now is None else now
    out = []
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    starts = []
    for i in range(days - 1, -1, -1):
        d = time.localtime(midnight - i * DAY + 3600 * 3)  # +3 h: safe across a daylight-saving change
        s = time.mktime((d.tm_year, d.tm_mon, d.tm_mday, 0, 0, 0, 0, 0, -1))
        starts.append(s)
        out.append({"day": s, "label": time.strftime("%a", d) if days <= 7 else time.strftime("%d", d).lstrip("0"),
                    "opened": 0, "broken": 0, "chests": 0, "games": 0})
    for r in rows:
        t = r["started"]
        if t < starts[0]:
            continue
        i = max(k for k, s in enumerate(starts) if t >= s)
        b = out[i]
        b["chests"] += r["chests"]
        b["broken"] += min(r["broken"] or 0, r["chests"])
        b["opened"] += max(0, r["chests"] - min(r["broken"] or 0, r["chests"]))
        b["games"] += 1 if (r["chests"] or r["replay"]) else 0
    return out


def play_times(rows: list[dict], days: int = 60, now: float | None = None) -> list[list[int]]:
    """Chests by weekday (Monday first) and 4-hour block of the day (from midnight), over the last `days`
    days: [[block0..block5] for each weekday]. For the "when you play" grid."""
    now = time.time() if now is None else now
    grid = [[0] * 6 for _ in range(7)]
    for r in rows:
        if not r["chests"] or r["started"] < now - days * DAY:
            continue
        lt = time.localtime(r["started"])
        grid[lt.tm_wday][lt.tm_hour // 4] += r["chests"]
    return grid


# --------------------------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------------------------
def played_with(files: dict, names: set[str]) -> list[dict]:
    """Everyone who was in my squad in this PC's replays, most games first:
    [{"name", "platform", "games", "chests", "best", "last"}]. Each match counts once."""
    people: dict[str, dict] = {}
    seen = set()
    for rname, e in list(files.items()):
        if e.get("status") not in ("imported", "confirmed"):
            continue
        if names and e.get("how") == "absent":
            continue  # a match someone else played on this PC
        match = e.get("session") or e.get("guid") or rname
        for p in _squad(e, names):
            n = norm(p["name"])
            if (n, match) in seen:
                continue
            seen.add((n, match))
            d = people.setdefault(n, {"name": p["name"], "platform": p["platform"], "games": 0, "chests": 0,
                                      "best": 0, "last": 0.0})
            d["games"] += 1
            d["chests"] += p["chests"]
            d["best"] = max(d["best"], p["chests"])
            when = float(e.get("started") or 0)
            if when >= d["last"]:
                d["last"], d["name"] = when, p["name"]
                d["platform"] = p["platform"] or d["platform"]
    return sorted(people.values(), key=lambda d: (-d["games"], -d["chests"]))


def player_games(files: dict, map_recs: dict, player_names, my_names: set[str], pois=None) -> list[dict]:
    """Every game a squadmate was in, from this PC's replays, newest first. player_names: the names they play
    as (any of them). Their chests are what the replay saw (near the recorder, or their own when they
    recorded it). Rows: {"started", "replay", "chests", "opened", "broken", "landed_at", "alive", "kills",
    "with", "on_map", "name"}."""
    want = {norm(n) for n in player_names if norm(n)}
    out, seen = [], set()
    for rname, e in list(files.items()):
        if e.get("status") not in ("imported", "confirmed"):
            continue
        match = e.get("session") or e.get("guid") or rname
        if match in seen:
            continue
        raw = e.get("raw") or {}
        hit = None
        if norm(e.get("recorder")) in want:  # they recorded it (their own match, played on this PC)
            hit = {"name": e.get("recorder"), "opened": int(raw.get("opened") or 0), "broken": int(raw.get("broken") or 0)}
        for t in e.get("teammates") or []:
            if hit is None and norm(t.get("name")) in want:
                hit = {"name": t.get("name"), "opened": int(t.get("opened") or 0), "broken": int(t.get("broken") or 0),
                       "chests": teammate_chests(t)}
        if hit is None:
            continue
        seen.add(match)
        others = [x for x in ([e.get("recorder")] + [t.get("name") for t in e.get("teammates") or [] if not t.get("bot")])
                  if x and norm(x) not in want]
        row = {"started": float(e.get("started") or 0), "replay": rname, "name": hit["name"],
               "chests": hit.get("chests", hit["opened"] + hit["broken"]), "opened": hit["opened"],
               "broken": hit["broken"],
               "landed_at": None, "alive": None, "kills": None, "with": others, "on_map": rname in map_recs}
        rec = map_recs.get(rname)
        pl = next((x for x in (rec or {}).get("map", {}).get("players", []) if norm(x.get("name")) in want), None)
        if pl is not None and pois is not None:
            try:
                import map_tracker

                st = map_tracker.player_stats(rec, pl, pois)
                row.update(landed_at=st.get("landed_at"), alive=st.get("alive"), kills=st.get("kills"))
            except Exception:
                pass
        out.append(row)
    out.sort(key=lambda r: -r["started"])
    return out


def player_summary(rows: list[dict], now: float | None = None) -> dict:
    """Totals for player_games rows."""
    now = time.time() if now is None else now
    n = len(rows)
    best = max(rows, key=lambda r: r["chests"], default=None)
    places: dict = {}
    for r in rows:
        if r.get("landed_at"):
            places[r["landed_at"]] = places.get(r["landed_at"], 0) + 1
    fav = max(places, key=places.get) if places else None
    today = filter_rows([dict(r, current=False) for r in rows], 1, now)
    return {"games": n, "chests": sum(r["chests"] for r in rows), "avg": (sum(r["chests"] for r in rows) / n) if n else 0.0,
            "best": best["chests"] if best else 0, "best_when": best["started"] if best else None,
            "today": sum(r["chests"] for r in today), "kills": sum(r["kills"] or 0 for r in rows),
            "favourite": fav, "favourite_n": places.get(fav, 0) if fav else 0}


# --------------------------------------------------------------------------------------------
# Odds (treasure hunt prizes)
# --------------------------------------------------------------------------------------------
def tiered_share(map_recs: dict, names: set[str]) -> tuple[float, int]:
    """(share of my chests that were tiered, chests with a known class) from the map records of my games.
    Only tiered chests can roll a treasure. 2/3 when nothing is known yet."""
    tiered = known = 0
    for rec in (map_recs or {}).values():
        m = rec.get("map") or {}
        mine = {p.get("name") for p in m.get("players", []) if is_me(p.get("name"), p.get("recorder"), names)}
        for c in m.get("chests", []):
            if c.get("who") in mine and c.get("class"):
                known += 1
                tiered += 1 if treasures.is_tiered(c.get("class")) else 0
    return (tiered / known if known >= 20 else 2 / 3), known


def chance_by_now(chests: float, one_in: float) -> float:
    """The chance at least one prize would have come up in this many chests: 1 - (1 - p)^n."""
    import math

    if chests <= 0 or one_in <= 0:
        return 0.0
    return -math.expm1(chests * math.log1p(-1.0 / one_in))


def chests_for(target: float, one_in: float) -> int:
    """How many chests give `target` chance (0-1) of at least one: ln(1 - target) / ln(1 - p)."""
    import math

    target = min(max(target, 1e-9), 0.999999)
    return int(math.ceil(math.log1p(-target) / math.log1p(-1.0 / one_in)))


def pace(rows: list[dict], days: int = 14, now: float | None = None) -> dict:
    """My recent pace: chests per day played, games per day played, chests per game (last `days` days)."""
    now = time.time() if now is None else now
    recent = [r for r in rows if r["started"] >= now - days * DAY and (r["chests"] or r.get("replay"))]
    played = {time.strftime("%Y%m%d", time.localtime(r["started"])) for r in recent}
    chests = sum(r["chests"] for r in recent)
    n = len(recent)
    return {"chests_per_day": chests / len(played) if played else 0.0, "games_per_day": n / len(played) if played else 0.0,
            "chests_per_game": chests / n if n else 0.0, "days_played": len(played)}


def odds_table(chests: int, share: float, target: float) -> list[dict]:
    """Every prize kind: odds per chest, chance by now (from the tiered chests), chests (all kinds) needed for
    the target chance, and how many more that is."""
    tiered = chests * share
    out = []
    for kind, one_in in treasures.ODDS.items():
        any_chest = kind in treasures.ANY_CHEST  # (sprite gizmos: every chest counts, not just tiered ones)
        need_tiered = chests_for(target, one_in)
        need = need_tiered if any_chest or not share else int(round(need_tiered / share))
        out.append({"kind": kind, "label": treasures.ODDS_LABELS.get(kind, kind), "one_in": one_in,
                    "chance": chance_by_now(chests if any_chest else tiered, one_in), "need_tiered": need_tiered,
                    "need": need, "more": max(0, need - chests), "any_chest": any_chest})
    return out


# --------------------------------------------------------------------------------------------
# Treasures
# --------------------------------------------------------------------------------------------
def treasure_rows(mine: list, peers: list[dict]) -> list[dict]:
    """Everyone's prizes, newest first: [{"when", "who", "prize", "name", "kind", "pirate", "rarity", "me"}].
    mine: TreasureLog items; peers: Team peers (each may carry {"treasures": {"recent": [...]}})."""
    rows, have = [], set()

    def add(when, who, prize, me):
        key = (who, prize, int(when or 0))
        if key in have:
            return
        have.add(key)
        d = treasures.describe(prize)
        rows.append({"when": float(when or 0), "who": who, "prize": prize, "name": d["name"], "kind": d["kind"],
                     "pirate": d["pirate"], "rarity": d["rarity"], "me": me})

    for x in mine or []:
        add(x.get("when"), "You", x.get("prize"), True)
    for p in peers or []:
        for x in ((p.get("treasures") or {}).get("recent") or []):
            add(x.get("when"), p.get("name") or "Teammate", x.get("prize"), False)
    rows.sort(key=lambda r: -r["when"])
    return rows


WEEKS = [("Week 1", "A$AP Arrives", 1), ("Week 2", "Lil Uzi Arrives", 14), ("Week 3", "Miku & Teto Arrive", 19),
         ("Week 4", "", 24), ("Week 5", "", 29)]


def collection(found: list[dict]) -> list[dict]:
    """The prize list by week with who found each: [{"week", "title", "items": [{"prize", "name", "kind",
    "found_by": [names]}]}], then the Pirate Shop and sprite gizmos (found again and again: "You ×3")."""
    counts: dict[str, dict] = {}
    for r in found:
        if r["pirate"]:
            key = treasures.PIRATE_SHOP
        elif r["kind"] == "sprite gizmo":
            key = "gizmo"
        else:
            key = str(r["prize"])
        who = counts.setdefault(key, {})
        who[r["who"]] = who.get(r["who"], 0) + 1
    by_prize = {k: [w if n == 1 else f"{w} ×{n}" for w, n in v.items()] for k, v in counts.items()}
    weeks = []
    for week, title, first in WEEKS:
        items = []
        for i in range(5):
            pid = f"DelilahPrize:dpid_item{first + i}"
            d = treasures.describe(pid)
            items.append({"prize": pid, "name": d["name"], "kind": d["kind"], "found_by": by_prize.get(pid, [])})
        weeks.append({"week": week, "title": title, "items": items})
    d = treasures.describe(treasures.PIRATE_SHOP)
    g = treasures.describe("gizmo")
    weeks.append({"week": "Pirate Shop", "title": "& sprite gizmos",
                  "items": [{"prize": treasures.PIRATE_SHOP, "name": d["name"], "kind": d["kind"],
                             "found_by": by_prize.get(treasures.PIRATE_SHOP, [])},
                            {"prize": "gizmo", "name": g["name"], "kind": g["kind"], "found_by": by_prize.get("gizmo", [])}]})
    return weeks


# --------------------------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------------------------
def fmt_length(s) -> str:
    if s is None:
        return "-"
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


def fmt_playtime(s) -> str:
    """'3h 05m', '47m', '-'."""
    if not s:
        return "-"
    m = int(s) // 60
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def fmt_rate(x) -> str:
    return "-" if x is None else f"{x:.1f}"


def fmt_when(t: float, now: float | None = None) -> str:
    """'Today 3:42 PM', 'Yesterday 9:10 PM', 'Mon 4:05 PM' (this week), 'Nov 12 4:05 PM'."""
    if not t:
        return "-"
    now = time.time() if now is None else now
    lt, ln = time.localtime(t), time.localtime(now)
    clock = time.strftime("%I:%M %p", lt).lstrip("0")
    days = (time.mktime((ln.tm_year, ln.tm_mon, ln.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))) / DAY
    if round(days) == 0:
        return f"Today {clock}"
    if round(days) == 1:
        return f"Yesterday {clock}"
    if days < 7:
        return f"{time.strftime('%a', lt)} {clock}"
    return f"{time.strftime('%b', lt)} {lt.tm_mday} {clock}"


def fmt_ago(t: float, now: float | None = None) -> str:
    if not t:
        return "-"
    now = time.time() if now is None else now
    s = max(0, now - t)
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < DAY:
        return f"{int(s // 3600)} h ago"
    d = int(s // DAY)
    return "yesterday" if d == 1 else f"{d} days ago"
