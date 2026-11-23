"""The files an install of Chest Tracker needs: what updates and the download zip carry.

public_files()     the public build (make_release.py: GitHub)
personal_files()   just the personal/ folder (a personal build adds it on top; see edition.py)
"""
from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[1]

TOP = ["version.json", "requirements.txt", "README.txt", "LICENSE", "setup_reader.bat", "Start Chest Tracker.bat",
       "chest.png"]
ASSETS = ["assets/icon.png", "assets/logo.png"]  # (icon.ico is for shortcuts and installers: not needed to run)
NOT_SHIPPED_PY: set[str] = set()


def public_files(app: Path = APP) -> dict[str, bytes]:
    names = sorted(p.name for p in app.glob("*.py") if p.name not in NOT_SHIPPED_PY)
    names += [n for n in TOP + ASSETS if (app / n).exists()]
    files = {n: (app / n).read_bytes() for n in names}
    for f in sorted((app / "reader_src").glob("*")):
        if f.is_file() and f.suffix in (".cs", ".csproj", ".txt"):
            files["reader_src/" + f.name] = f.read_bytes()
    return files


PERSONAL_NOT_SHIPPED = {"tools", "replay_sharing"}  # (the publisher's own tools and notes stay on the publisher's PC)


def personal_files(app: Path = APP) -> dict[str, bytes]:
    """What the squad's PCs need from personal/: its settings and features (.py and .json), not the publisher's
    tools, notes or pictures. Updates go through the relay, which wants them small."""
    out = {}
    root = app / "personal"
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if (f.is_file() and f.suffix in (".py", ".json") and "__pycache__" not in rel.parts
                and rel.parts[0] not in PERSONAL_NOT_SHIPPED):
            out["personal/" + rel.as_posix()] = f.read_bytes()
    return out
