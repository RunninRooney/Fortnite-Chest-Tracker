"""Treasure hunt prizes (Treasure Hunters mini-season): what each prize id is, which ones you've found,
and the list window.

Where they come from: when a chest rolls a treasure prize, the server spawns a gold chest
(BGA_DelilahTreasureHuntAnimatedChest) for about 1.5 s. It carries the prize id (e.g.
"DelilahPrize:dpid_item21") and who opened it. The replay reader reports those as "treasures"
(see reader_src/ChestReader.cs ResolveTreasures). Like chests, a gold chest is only in a replay when it
was near the player who recorded it, so each tracker records its own player's prizes from its own
replays and shares them with the team.

Prize names: from the LED-N/Fortnite-ChestCounter prize list (content/content.json, Nov 15, 2026),
checked against our own prizes (item 20 = Miku's Takarabako, 21 = PPPP, 22 = May Fortune Find You).
Weeks 4-5 had no names yet. The Pirate Shop access prize has a bare GUID as its id.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

PIRATE_SHOP = "9f73f94d-2e61-4523-97d7-d61235d3365e"

# id -> (name, kind)
CATALOG: dict[str, tuple[str, str]] = {
    # Week 1 "A$AP Arrives"
    "DelilahPrize:dpid_item1": ("Solid Gold A$AP Rocky", "outfit"),
    "DelilahPrize:dpid_item2": ("Solid Gold A$AP Rocky's Gauntlets", "pickaxe"),
    "DelilahPrize:dpid_item3": ("No Trespassing", "jam track"),
    "DelilahPrize:dpid_item4": ("Plunder in Paradise", "loading screen"),
    "DelilahPrize:dpid_item5": ("Gold Mercedes-AMG G 63 Decal", "car"),
    # Week 2 "Lil Uzi Arrives"
    "DelilahPrize:dpid_item14": ("Solid Gold Lil Uzi Vert", "outfit"),
    "DelilahPrize:dpid_item15": ("Diamond and Gold", "pickaxe or back bling"),
    "DelilahPrize:dpid_item16": ("London", "jam track"),
    "DelilahPrize:dpid_item17": ("So Kawaii", "loading screen"),
    "DelilahPrize:dpid_item18": ("Gold Cadillac CT5-V Decal", "car"),
    # Week 3 "Miku & Teto Arrive"
    "DelilahPrize:dpid_item19": ("Solid Gold Miku", "outfit"),
    "DelilahPrize:dpid_item20": ("Miku's Takarabako", "back bling"),
    "DelilahPrize:dpid_item21": ("PPPP", "jam track"),
    "DelilahPrize:dpid_item22": ("May Fortune Find You", "loading screen"),
    "DelilahPrize:dpid_item23": ("Gold Chevrolet Corvette ZR1 Decal", "car"),
    # Pirate Shop access (won on the pre-roll; its id is a bare GUID)
    PIRATE_SHOP: ("Pirate Shop Access", "pirate shop"),
}
# Weeks 4 and 5: the slot order matches weeks 1-3 (outfit, pickaxe/back bling, jam, loading screen, car)
_WEEK_SLOTS = ["Gold Outfit", "Pickaxe or Back Bling", "Jam Track", "Loading Screen", "Vehicle & Decal"]
for _week, _first in ((4, 24), (5, 29)):
    for _i, _slot in enumerate(_WEEK_SLOTS):
        CATALOG.setdefault(f"DelilahPrize:dpid_item{_first + _i}", (f"Week {_week} {_slot}", _slot.lower()))

# The Pirate Shop GUID as raw bytes (four little- or big-endian 32-bit parts), in case the reader only
# found bytes, not text
_G = PIRATE_SHOP.replace("-", "")
_PARTS = [_G[i:i + 8] for i in range(0, 32, 8)]
_PIRATE_HEX = {_G.upper(), "".join(bytes.fromhex(p)[::-1].hex() for p in _PARTS).upper()}

# Rough odds per chest (Epic's posted odds, via the same prize list): rarer = bigger celebration
RARITY = {"outfit": 5, "gold outfit": 5, "pirate shop": 4, "car": 3, "vehicle & decal": 3,
          "pickaxe": 2, "back bling": 2, "pickaxe or back bling": 2, "jam track": 1, "loading screen": 1,
          "sprite gizmo": 0}  # (the commonest prize: never the "rarest" find over a real one)

# Odds per chest, "1 in N" (Epic's posted odds: "per chest in a normal match"). Update these numbers here if
# Epic posts new ones. Treasure Hours raise some of them (1.5x-10x); that isn't counted here.
ODDS = {"pirate shop": 137_500, "outfit": 1_562_500, "car": 7_813, "pickaxe": 568, "jam track": 391,
        "loading screen": 272, "sprite gizmo": 127}
ODDS_LABELS = {"pirate shop": "Pirate Shop access", "outfit": "Gold outfit", "car": "Car decal",
               "pickaxe": "Pickaxe or back bling", "jam track": "Jam track", "loading screen": "Loading screen",
               "sprite gizmo": "Sprite gizmo"}
# Epic's posted odds are per chest, with no chest type named, so every chest counts toward every prize. (Until
# Nov 21, 2026 only tiered chests counted for the gold-chest prizes: an assumption, not Epic's. Of the replays' 5
# non-gizmo finds with a known chest, all 5 were tiered: suggestive, but 1 in 7 even if every chest counts.)
# Set TIERED_ONLY = True to go back to counting only tiered chests for everything but gizmos.
TIERED_ONLY = False
ANY_CHEST = {"sprite gizmo"} if TIERED_ONLY else set(ODDS)


def is_tiered(chest_class) -> bool:
    """Tiered chests (Tiered_Chest_Athena, Tiered_Chest_*, B_TieredChest_Relics*...), as opposed to AlwaysSpawn ones."""
    return "tiered" in str(chest_class or "").lower()


def describe(prize: str | None) -> dict:
    """{"name", "kind", "rarity", "pirate"} for a prize id; unknown ids get a readable fallback."""
    p = (prize or "").strip()
    pl = p.lower()
    if pl == PIRATE_SHOP or (pl.startswith("hex:") and any(h in p.upper() for h in _PIRATE_HEX)):
        name, kind = CATALOG[PIRATE_SHOP]
    elif p in CATALOG:
        name, kind = CATALOG[p]
    elif pl == "gizmo":  # a treasure prize with no gold chest: a sprite gizmo (which one isn't in the replay)
        name, kind = "Sprite gizmo", "sprite gizmo"
    elif re.fullmatch(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", pl):
        name, kind = "Special prize (shop or token?)", "special"
    else:
        m = re.search(r"dpid_item(\d+)", p)
        name, kind = (f"Treasure prize #{m.group(1)}" if m else "Treasure prize"), "unknown"
    return {"name": name, "kind": kind, "rarity": RARITY.get(kind, 1), "pirate": kind == "pirate shop"}


def _norm(name) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def mine(treasures: list, my_names: set[str], recorder_is_me: bool) -> list:
    """The treasures in one replay that this player opened (by in-game name; the recorder if no name is set)."""
    out = []
    for t in treasures or []:
        if not isinstance(t, dict) or t.get("won") is False:
            continue
        if (my_names and _norm(t.get("name")) in my_names) or (not my_names and t.get("who") == "me" and recorder_is_me):
            out.append(t)
    return out


class TreasureLog:
    """This player's prizes, kept in treasures.json: [{"match", "t", "prize", "name", "kind", "when"}]."""

    SHARE = 12  # how many recent finds go to teammates

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.items = [x for x in data.get("found", []) if isinstance(x, dict)] if isinstance(data, dict) else []
        except (OSError, ValueError):
            self.items = []

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"found": self.items}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, match: str, finds: list, when: float) -> list[dict]:
        """Adds this match's finds; returns the ones not already known (each match/prize/time once).
        A match read again replaces its sprite gizmo entries: 1.18 credited the squad's shared gizmo prizes
        to whoever recorded the replay, and re-reading with the fixed reader takes those back out."""
        new = []
        with self.lock:
            keep = {round(float(f.get("t") or 0)) for f in finds if f.get("prize") == "gizmo"}
            before = len(self.items)
            self.items = [x for x in self.items if not (x.get("match") == match and x.get("prize") == "gizmo"
                                                        and round(float(x.get("t") or 0)) not in keep)]
            dropped = before - len(self.items)
            have = {(x.get("match"), x.get("prize"), round(float(x.get("t") or 0))) for x in self.items}
            for f in finds:
                key = (match, f.get("prize"), round(float(f.get("t") or 0)))
                if key in have:
                    continue
                d = describe(f.get("prize"))
                item = {"match": match, "t": round(float(f.get("t") or 0), 1), "prize": f.get("prize"),
                        "name": d["name"], "kind": d["kind"], "when": round(when + float(f.get("t") or 0))}
                self.items.append(item)
                have.add(key)
                new.append(item)
            if new or dropped:
                self.items.sort(key=lambda x: x.get("when") or 0)
                self._save()
        return new

    def share(self) -> dict:
        with self.lock:
            return {"count": len(self.items),
                    "recent": [{"prize": x.get("prize"), "when": x.get("when")} for x in self.items[-self.SHARE:]]}


def parse_shared(v) -> dict | None:
    """A teammate's shared treasures, checked: {"count": n, "recent": [{"prize", "when"}]}."""
    if not isinstance(v, dict):
        return None
    try:
        count = max(0, min(int(v.get("count") or 0), 100000))
    except (TypeError, ValueError):
        return None
    recent = []
    for x in (v.get("recent") or [])[:TreasureLog.SHARE] if isinstance(v.get("recent"), list) else []:
        if isinstance(x, dict) and isinstance(x.get("prize"), str):
            try:
                recent.append({"prize": x["prize"][:120], "when": int(x.get("when") or 0)})
            except (TypeError, ValueError):
                pass
    return {"count": count, "recent": recent}


def found_message(who: str, item: dict) -> str:
    d = describe(item.get("prize"))
    if d["pirate"]:
        return f"PIRATE SHOP ACCESS! {who} won it!" if who != "You" else "PIRATE SHOP ACCESS! You won it!"
    if d["kind"] == "sprite gizmo":
        return f"TREASURE! {who} got a sprite gizmo" if who != "You" else "TREASURE! You got a sprite gizmo"
    return f"TREASURE! {who} found {d['name']} ({d['kind']})"


def show_window(app):
    """A small window listing everyone's treasures (yours from treasures.json, teammates' as shared)."""
    import tkinter as tk
    from tkinter import ttk

    w = tk.Toplevel(app.root)
    w.title("Treasures found")
    w.attributes("-topmost", True)
    f = ttk.Frame(w, padding=12)
    f.grid(sticky="nsew")
    rows = []
    with app.treasures.lock:
        for x in app.treasures.items:
            rows.append((x.get("when") or 0, "You", x.get("prize")))
    with app.team.lock:
        peers = [dict(p) for p in app.team.peers.values()]
    for p in peers:
        for x in (p.get("treasures") or {}).get("recent", []):
            rows.append((x.get("when") or 0, p.get("name") or "?", x.get("prize")))
    rows = sorted(set(rows), key=lambda r: -r[0])
    ttk.Label(f, text="Treasure hunt prizes", font=("Segoe UI Semibold", 12)).grid(row=0, column=0, columnspan=3, sticky="w")
    if not rows:
        ttk.Label(f, text="None yet. They show up here after the match's replay is read.", foreground="#666").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
    for i, (when, who, prize) in enumerate(rows[:40], start=1):
        d = describe(prize)
        day = time.strftime("%b %d %I:%M %p", time.localtime(when)) if when else ""
        color = {"foreground": "#b8860b"} if d["pirate"] or d["rarity"] >= 3 else {}
        ttk.Label(f, text=day, foreground="#666").grid(row=i, column=0, sticky="w", padx=(0, 10))
        ttk.Label(f, text=who).grid(row=i, column=1, sticky="w", padx=(0, 10))
        ttk.Label(f, text=f"{d['name']} ({d['kind']})", **color).grid(row=i, column=2, sticky="w")
    total = len([r for r in rows if r[1] == "You"])
    ttk.Label(f, text=f"You: {total} found. Gold chests only count when the match replay saw them (near you).",
              foreground="#666").grid(row=len(rows[:40]) + 2, column=0, columnspan=3, sticky="w", pady=(10, 0))
    ttk.Button(f, text="Close", command=w.destroy).grid(row=len(rows[:40]) + 3, column=2, sticky="e", pady=(8, 0))
