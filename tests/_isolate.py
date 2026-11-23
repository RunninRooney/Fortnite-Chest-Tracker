"""Imported first by every test file: points %APPDATA% at a temporary folder, so tests can never
read or write the real Chest Tracker settings, totals or signing key.

(An earlier version used os.environ.setdefault, which keeps the real folder when one is set, and a
test overwrote a real config.json. check() now stops the run if that could happen again.)
"""
import os
import tempfile

REAL_APPDATA = os.environ.get("CHEST_REAL_APPDATA") or os.environ.get("APPDATA") or ""
os.environ["CHEST_REAL_APPDATA"] = REAL_APPDATA
if not os.environ.get("CHEST_TEST_APPDATA"):
    os.environ["CHEST_TEST_APPDATA"] = tempfile.mkdtemp(prefix="chest_tests_")
os.environ["APPDATA"] = os.environ["CHEST_TEST_APPDATA"]


def check(module):
    """Refuse to run if the module under test points at the real settings folder."""
    data_dir = str(getattr(module, "DATA_DIR", ""))
    if REAL_APPDATA and data_dir.lower().startswith(REAL_APPDATA.lower()):
        raise SystemExit(f"tests stopped: {module.__name__} would use the real settings folder {data_dir}")
