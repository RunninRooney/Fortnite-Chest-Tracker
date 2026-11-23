"""Which build of Chest Tracker this is, and the personal add-ons.

The public build (this repository) updates from GitHub Releases and never sends anything unless the user turns
it on. A copy with a personal/ folder (never uploaded: see .gitignore) is a personal build: personal/edition.json
overrides the settings below (for example updates through the squad's relay, problem reports sealed to the
publisher), and personal/__init__.py, if there is one, can add features of its own:

    def setup(app):            # called once the app is running (app: chest_tracker.App)
        ...

Anything personal stays in personal/: the public code never needs to change for it.
"""
from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PERSONAL_DIR = APP_DIR / "personal"

# The GitHub repository public updates come from ("owner/name"). Empty: no update checks.
GITHUB_REPO = ""

DEFAULTS = {
    "name": "public",
    "updates": "github",      # "github" (GitHub Releases of GITHUB_REPO), "relay" (the squad relay) or "none"
    "github_repo": GITHUB_REPO,
    "reports": False,         # problem reports when the user hasn't chosen (they can turn them on in Settings)
    "report_key": "",         # X25519 public key (hex) reports are sealed to; without one none are sent
}

log = logging.getLogger("chest")


def load(app_dir: Path | None = None) -> dict:
    out = dict(DEFAULTS)
    path = (app_dir or APP_DIR) / "personal" / "edition.json"
    try:
        extra = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(extra, dict):
            out.update({k: v for k, v in extra.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception:
        log.warning("personal/edition.json can't be read; using the public settings", exc_info=True)
    return out


def is_personal(app_dir: Path | None = None) -> bool:
    return load(app_dir)["name"] != "public"


def start_personal_features(app) -> bool:
    """Runs personal/__init__.py's setup(app), if this copy has one. Never stops the app if it fails."""
    if not (PERSONAL_DIR / "__init__.py").exists():
        return False
    try:
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
        mod = importlib.import_module("personal")
        fn = getattr(mod, "setup", None)
        if callable(fn):
            fn(app)
            log.info("personal features started")
            return True
    except Exception:
        log.exception("personal features failed to start (the app runs without them)")
    return False
