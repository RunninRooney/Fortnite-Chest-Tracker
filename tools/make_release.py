"""Builds a public release: dist/ChestTracker.zip (the download) and dist/update.json (the signed update that
installed copies fetch from the repository's latest GitHub release).

    python tools/make_release.py            bumps the build number, then builds
    python tools/make_release.py --dry-run  builds without bumping

Then attach both files to a new GitHub release, for example:
    gh release create v1.31 dist/ChestTracker.zip dist/update.json --title "Chest Tracker 1.31"

Never includes personal/ (see edition.py). The signing key is %APPDATA%\\ChestTracker\\publisher_key.hex; its
public half is PUBLISHER_KEY in launcher.py. If you forked this project, make your own key (this tool creates
one), put its public half in launcher.py and your repository in edition.GITHUB_REPO.
"""
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tools"))
import bundle_files  # noqa: E402
import launcher  # noqa: E402

KEY_FILE = Path(os.environ["APPDATA"]) / "ChestTracker" / "publisher_key.hex"


def load_key() -> bytes:
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(os.urandom(32).hex())
        print("created a signing key:", KEY_FILE, "(keep it private; put its public half in launcher.py)")
    return bytes.fromhex(KEY_FILE.read_text().strip())


def check_private(files: dict[str, bytes]):
    """Stops if anything private would be published: personal files, or this PC's team code."""
    bad = [n for n in files if n.startswith("personal/")]
    if bad:
        sys.exit(f"personal files in a public release: {bad[:5]}")
    try:
        cfg = json.loads((Path(os.environ["APPDATA"]) / "ChestTracker" / "config.json").read_text(encoding="utf-8"))
        code = str(cfg.get("team_code") or "").strip()
    except Exception:
        code = ""
    if len(code) >= 4:
        for n, b in files.items():
            if code.lower().encode() in b.lower():
                sys.exit(f"{n} contains this PC's team code: not publishing")


def main():
    dry = "--dry-run" in sys.argv
    secret = load_key()
    pub = launcher.public_key(secret).hex()
    if launcher.PUBLISHER_KEY != pub:
        sys.exit(f"launcher.py has a different publisher key; this key's public half is {pub}")
    version = re.search(r'APP_VERSION = "([^"]+)"', (APP / "chest_tracker.py").read_text(encoding="utf-8")).group(1)
    vfile = APP / "version.json"
    build = json.loads(vfile.read_text())["build"] + (0 if dry else 1)
    if not dry:
        vfile.write_text(json.dumps({"build": build, "version": version}) + "\n")
    files = bundle_files.public_files()
    check_private(files)
    bundle = launcher.make_bundle(secret, build, version, files)
    check = launcher.open_bundle(bundle, pub)
    assert check and check["build"] == build and check["files"] == files, "bundle doesn't verify"
    dist = APP / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "update.json").write_bytes(bundle)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr("ChestTracker/" + name, data)
    (dist / "ChestTracker.zip").write_bytes(buf.getvalue())
    print(f"build {build}, version {version}: {len(files)} files")
    print(f"  dist/ChestTracker.zip  {len(buf.getvalue()) // 1024} KB")
    print(f"  dist/update.json       {len(bundle) // 1024} KB")
    tag = "v" + version.split(" ")[0]
    print(f'next: gh release create {tag} dist/ChestTracker.zip dist/update.json --title "Chest Tracker {version}"')


if __name__ == "__main__":
    main()
