"""First-run setup: three short steps in one window, the first time Chest Tracker starts.

  1. You          the name your squad sees, and your Fortnite name(s)
  2. How to play  with a squad (team code, or make a strong one), just me, or private (offline mode)
  3. Fortnite     checks the settings Chest Tracker needs: replays on, Windowed Fullscreen, the replay reader

The window stays on top: on a first start Windows often doesn't let a just-started program come to the front,
and the app waits for this window.
"""
from __future__ import annotations

import configparser
import logging
import os
import re
import time
from pathlib import Path

import teamcrypt

log = logging.getLogger("chest")

TITLE = "Chest Tracker - first-time setup"
BG, CARD, CARD_ON, LINE = "#070b14", "#0e1626", "#10283f", "#1d2b44"
TEXT, DIM, ACC, ACC_TEXT = "#eef6ff", "#8fa4c2", "#3fc8ff", "#03121f"
OK, WARN, INFO = "#5fdc8a", "#ffc163", "#8fa4c2"
FONT = "Segoe UI"

GAME_INI = Path(os.environ.get("LOCALAPPDATA") or "") / "FortniteGame" / "Saved" / "Config" / "WindowsClient" / "GameUserSettings.ini"
RECENT_REPLAY_DAYS = 14


def fortnite_checks(demos: Path, ini: Path, reader: Path, now: float | None = None) -> list[tuple[str, str, str]]:
    """[(state, title, detail)] with state "ok", "warn" or "info", for the settings Chest Tracker needs."""
    now = time.time() if now is None else now
    out = []
    try:
        newest = max((p.stat().st_mtime for p in demos.glob("*.replay")), default=None)
    except OSError:
        newest = None
    if newest is not None and now - newest < RECENT_REPLAY_DAYS * 86400:
        out.append(("ok", "Replays are on", "Fortnite saves a replay of each match, so broken chests and your squad "
                                            "get counted too."))
    elif demos.exists():
        out.append(("warn", "Turn on replays", "In Fortnite: Settings > Game > Replays > Record Replays: On. That's how "
                                               "broken chests and your squad's chests get counted."))
    else:
        out.append(("info", "No replays yet", "Fortnite hasn't saved a replay on this PC yet. In Fortnite: Settings > "
                                              "Game > Replays > Record Replays: On."))
    mode = None
    try:
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        cp.read(ini, encoding="utf-8-sig")
        for sec in cp.sections():
            v = cp[sec].get("LastConfirmedFullscreenMode") or cp[sec].get("FullscreenMode")
            if v is not None and re.fullmatch(r"\d", v.strip()):
                mode = int(v.strip())
                break
    except Exception:
        mode = None
    if mode == 0:
        out.append(("warn", "Switch to Windowed Fullscreen", "Fullscreen hides every overlay, including this one. In "
                                                             "Fortnite: Settings > Display > Window Mode > Windowed Fullscreen."))
    elif mode in (1, 2):
        out.append(("ok", "Window mode is fine", "Windowed Fullscreen lets the overlay show over the game."))
    else:
        out.append(("info", "Use Windowed Fullscreen", "In Fortnite: Settings > Display > Window Mode > Windowed "
                                                       "Fullscreen (regular Fullscreen hides the overlay)."))
    if reader.exists():
        out.append(("ok", "Replay reader is ready", "Broken chests are counted after each match."))
    else:
        out.append(("info", "Replay reader", "Start Chest Tracker.bat sets it up (it counts broken chests). Until "
                                             "then, opened chests still count."))
    return out


def parse_names(text: str) -> list[str]:
    return [n.strip()[:32] for n in re.split(r"[,;]", text or "") if n.strip()][:6]


def apply(app, name: str, fortnite: str, mode: str, code: str, save) -> None:
    """Saves the answers. mode: "squad", "solo" or "offline"."""
    cfg = app.cfg
    cfg["name"] = (name.strip() or "Player")[:20]
    names = parse_names(fortnite)
    if names:
        cfg["fortnite_names"] = names
    cfg["offline"] = mode == "offline"
    cfg["setup_done"] = True
    save(cfg)
    if mode == "squad" and teamcrypt.normalize_code(code):
        app.team.set_code(code.strip())
    elif app.cfg.get("team_code"):
        app.team.set_code("")


def run(app, save, demos: Path, reader: Path, logo_path: Path) -> None:  # noqa: C901 (one window, three steps)
    """Shows the window and waits for it. save(cfg) writes the config."""
    import tkinter as tk

    default = "Player"  # (only if the window is closed without a name: never the Windows user name)
    w = tk.Toplevel(app.root)
    w.title(TITLE)
    w.configure(bg=BG)
    w.attributes("-topmost", True)
    w.resizable(False, False)
    W, H = 600, 700
    sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
    w.geometry(f"{W}x{H}+{(sw - W) // 2}+{max(0, (sh - H) // 3)}")

    st = {"step": 0, "mode": "squad" if app.cfg.get("team_code") else None, "done": tk.BooleanVar(value=False)}
    name = tk.StringVar(value=app.cfg.get("name") or "")
    fortnite = tk.StringVar(value=", ".join(app.cfg.get("fortnite_names") or []))
    code = tk.StringVar(value=app.cfg.get("team_code", ""))

    # header: the logo
    head = tk.Frame(w, bg=BG)
    head.pack(fill="x", pady=(14, 0))
    try:
        from PIL import Image, ImageTk

        img = Image.open(logo_path).convert("RGBA").resize((120, 120), Image.LANCZOS)
        st["logo"] = ImageTk.PhotoImage(img)
        tk.Label(head, image=st["logo"], bg=BG, bd=0).pack()
    except Exception:
        tk.Label(head, text="Chest Tracker", font=(FONT, 20, "bold"), fg=TEXT, bg=BG).pack(pady=20)
    dots = tk.Frame(head, bg=BG)
    dots.pack(pady=(4, 0))
    dot_labels = [tk.Label(dots, text="●", font=(FONT, 9), bg=BG) for _ in range(3)]
    for d in dot_labels:
        d.pack(side="left", padx=3)

    foot = tk.Frame(w, bg=BG)
    foot.pack(side="bottom", fill="x", padx=34, pady=(0, 22))  # (first, so a long step never pushes it off)
    body = tk.Frame(w, bg=BG)
    body.pack(fill="both", expand=True, padx=34, pady=(8, 0))

    def button(parent, text, cmd, primary=False):
        b = tk.Label(parent, text=text, font=(FONT, 11, "bold" if primary else "normal"), cursor="hand2",
                     bg=ACC if primary else CARD, fg=ACC_TEXT if primary else TEXT, padx=18, pady=8)
        b.bind("<Button-1>", lambda e: cmd())
        return b

    def entry(parent, var, width=34):
        box = tk.Frame(parent, bg=LINE, padx=1, pady=1)
        e = tk.Entry(box, textvariable=var, width=width, font=(FONT, 12), bg=CARD, fg=TEXT, insertbackground=TEXT,
                     relief="flat", bd=6)
        e.pack(fill="x")
        return box, e

    def heading(text, sub):
        tk.Label(body, text=text, font=(FONT, 17, "bold"), fg=TEXT, bg=BG, anchor="w").pack(fill="x")
        tk.Label(body, text=sub, font=(FONT, 10), fg=DIM, bg=BG, anchor="w", justify="left", wraplength=530).pack(
            fill="x", pady=(2, 14))

    def label(parent, text, size=10, color=DIM, bold=False, pady=(0, 0)):
        lab = tk.Label(parent, text=text, font=(FONT, size, "bold" if bold else "normal"), fg=color,
                       bg=parent["bg"], anchor="w", justify="left", wraplength=520)
        lab.pack(fill="x", pady=pady)
        return lab

    note = tk.Label(foot, text="", font=(FONT, 10), fg=WARN, bg=BG, anchor="w", wraplength=330, justify="left")

    def step_you():
        heading("Welcome to Chest Tracker", "It counts every chest you open and break in Fortnite, and adds up "
                                            "your squad's. Two quick questions first.")
        label(body, "Your name", 11, TEXT, True)
        label(body, "What your squad sees next to your chests.", pady=(0, 4))
        box, e = entry(body, name)
        box.pack(fill="x", pady=(0, 16))
        label(body, "Your Fortnite name (optional)", 11, TEXT, True)
        label(body, "Found from your replays if you leave it empty. Add a console name too, with a comma "
                    "between names.", pady=(0, 4))
        entry(body, fortnite)[0].pack(fill="x")
        e.focus_set()
        e.select_range(0, "end")

    def step_mode():
        heading("How will you play?", "You can change this any time in Settings.")
        options = [("squad", "With my squad", "Share chest counts live with friends who type the same team code."),
                   ("solo", "Just me", "No squad. Updates still install by themselves."),
                   ("offline", "Private (offline)", "Nothing leaves this PC and nothing is downloaded: no squad, no "
                                                    "update checks, no problem reports, no map download.")]
        cards = {}

        def pick(key):
            st["mode"] = key
            note.configure(text="")
            for k, (frame, widgets) in cards.items():
                col = CARD_ON if k == key else CARD
                frame.configure(bg=col, highlightbackground=ACC if k == key else LINE)
                for wd in widgets:
                    wd.configure(bg=col)
            code_row.pack_forget()
            if key == "squad":
                code_row.pack(fill="x", pady=(0, 4), after=cards["squad"][0])

        for key, title, sub in options:
            fr = tk.Frame(body, bg=CARD, highlightthickness=2, highlightbackground=LINE, cursor="hand2", padx=14, pady=10)
            fr.pack(fill="x", pady=(0, 8))
            t = tk.Label(fr, text=title, font=(FONT, 12, "bold"), fg=TEXT, bg=CARD, anchor="w")
            t.pack(fill="x")
            s_ = tk.Label(fr, text=sub, font=(FONT, 10), fg=DIM, bg=CARD, anchor="w", justify="left", wraplength=490)
            s_.pack(fill="x")
            for wd in (fr, t, s_):
                wd.bind("<Button-1>", lambda e, k=key: pick(k))
            cards[key] = (fr, (t, s_))

        code_row = tk.Frame(body, bg=BG)
        row = tk.Frame(code_row, bg=BG)
        row.pack(fill="x")
        box, ce = entry(row, code, 22)
        box.pack(side="left", fill="x", expand=True)

        def make():
            code.set(teamcrypt.make_code())
            strength_note()

        def copy():
            w.clipboard_clear()
            w.clipboard_append(code.get())
            hint.configure(text="Copied. Send it to your squad: everyone types the same code.", fg=OK)

        button(row, "Copy", copy).pack(side="right", padx=(6, 0))
        button(row, "Make a code", make).pack(side="right", padx=(6, 0))
        hint = tk.Label(code_row, font=(FONT, 9), bg=BG, anchor="w", justify="left", wraplength=520)
        hint.pack(fill="x", pady=(4, 0))

        def strength_note(*_):
            s_ = teamcrypt.code_strength(code.get()) if code.get().strip() else ""
            if s_ == "weak":
                hint.configure(text="Short codes are easy to guess: try Make a code.", fg=WARN)
            else:
                hint.configure(text="Everyone in the squad types the same code. Your numbers are locked with it "
                                    "before they leave your PC.", fg=DIM)

        ce.bind("<KeyRelease>", strength_note)
        strength_note()
        if st["mode"]:
            pick(st["mode"])

    def step_fortnite():
        heading("Check Fortnite's settings", "Chest Tracker only reads Fortnite's files and the screen. These "
                                             "settings make it work best.")
        rows = tk.Frame(body, bg=BG)
        rows.pack(fill="x")

        def fill():
            for c in rows.winfo_children():
                c.destroy()
            for state, title, detail in fortnite_checks(demos, GAME_INI, reader):
                fr = tk.Frame(rows, bg=CARD, padx=12, pady=9)
                fr.pack(fill="x", pady=(0, 8))
                mark = {"ok": ("✓", OK), "warn": ("!", WARN), "info": ("i", INFO)}[state]
                tk.Label(fr, text=mark[0], font=(FONT, 13, "bold"), fg=mark[1], bg=CARD, width=2).pack(side="left", anchor="n")
                col = tk.Frame(fr, bg=CARD)
                col.pack(side="left", fill="x", expand=True)
                tk.Label(col, text=title, font=(FONT, 11, "bold"), fg=TEXT, bg=CARD, anchor="w").pack(fill="x")
                tk.Label(col, text=detail, font=(FONT, 9), fg=DIM, bg=CARD, anchor="w", justify="left",
                         wraplength=470).pack(fill="x")

        fill()
        button(body, "Check again", fill).pack(anchor="w", pady=(4, 0))

    steps = [step_you, step_mode, step_fortnite]

    def show():
        for c in body.winfo_children():
            c.destroy()
        for c in foot.winfo_children():
            c.pack_forget()
        for i, d in enumerate(dot_labels):
            d.configure(fg=ACC if i == st["step"] else LINE)
        steps[st["step"]]()
        last = st["step"] == len(steps) - 1
        button(foot, "Start" if last else "Next", nxt, primary=True).pack(side="right")
        if st["step"]:
            button(foot, "Back", back).pack(side="right", padx=(0, 8))
        note.pack(side="left", fill="x", expand=True)

    def nxt():
        if st["step"] == 0 and not name.get().strip():
            note.configure(text="Type a name for your squad to see.")
            return
        if st["step"] == 1:
            if not st["mode"]:
                note.configure(text="Pick one of the three.")
                return
            if st["mode"] == "squad" and not teamcrypt.normalize_code(code.get()):
                note.configure(text="Type your squad's code, or click Make a code.")
                return
        note.configure(text="")
        if st["step"] == len(steps) - 1:
            st["done"].set(True)
            return
        st["step"] += 1
        show()

    def back():
        st["step"] = max(0, st["step"] - 1)
        note.configure(text="")
        show()

    w.bind("<Return>", lambda e: nxt())
    w.protocol("WM_DELETE_WINDOW", lambda: st["done"].set(True))
    show()
    w.lift()
    w.focus_force()
    log.info("first-time setup window shown")
    w.wait_variable(st["done"])
    apply(app, name.get() or default, fortnite.get(), st["mode"] or "solo", code.get(), save)
    try:
        w.destroy()
    except Exception:
        pass
    log.info("first-time setup done (mode %s, team code %s)", st["mode"] or "solo",
             "set" if app.cfg.get("team_code") else "none")
