"""
Chest Tracker launcher. "Start Chest Tracker.bat" runs this (in a console window) every time.

1. Update: asks for the newest version: GitHub Releases for the public build, the squad's relay for a
   personal build (see edition.py). Updates are signed; anything without a valid signature from the
   publisher's key (PUBLISHER_KEY below, whose private half never leaves the publisher's PC) is ignored.
   If nothing can be reached, the current version just runs. Offline mode: no update check at all.
2. Checks everything needed is installed (and repairs it if not), builds the replay reader if needed.
3. Starts Chest Tracker and watches it for a few seconds. If it stops right away, the error is shown
   here (this window stays open) instead of disappearing.
4. Only if problem reports are on (Settings > Privacy; off in the public build unless turned on): sends a
   short debug report (what happened, errors, the app's log, with the Windows user name and folder paths
   blanked out), sealed so only the publisher can read it. Never in offline mode.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import time
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edition  # noqa: E402
import teamcrypt  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / "ChestTracker"
LAUNCHER_LOG = DATA_DIR / "launcher.log"
BROKERS = [("broker.hivemq.com", 1883), ("broker.emqx.io", 1883), ("test.mosquitto.org", 1883)]

# Public half of the publisher's signing key. Only updates signed with the matching private key
# (kept on the publisher's PC) are installed.
PUBLISHER_KEY = "9a0462610ca924631c404d274e3c1b5621e02a4cb5fc07568e4ca250f065eab4"

ALREADY_RUNNING_EXIT = 3  # chest_tracker.py exits with this when another copy is already running
lines: list[str] = []      # everything printed, for the debug report


def say(text: str = ""):
    print(text, flush=True)
    lines.append(text)


def channel(pubkey_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(pubkey_hex)).hexdigest()[:16]


# --------------------------------------------------------------------------------------
# Ed25519 signatures (RFC 8032 reference algorithm, pure Python, no extra packages)
# --------------------------------------------------------------------------------------
_p = 2 ** 255 - 19
_d = -121665 * pow(121666, _p - 2, _p) % _p
_q = 2 ** 252 + 27742317777372353535851937790883648493
_sqrt_m1 = pow(2, (_p - 1) // 4, _p)


def _add(P, Q):
    A, B = (P[1] - P[0]) * (Q[1] - Q[0]) % _p, (P[1] + P[0]) * (Q[1] + Q[0]) % _p
    C, D = 2 * P[3] * Q[3] * _d % _p, 2 * P[2] * Q[2] % _p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F, G * H, F * G, E * H)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q):
    return (P[0] * Q[2] - Q[0] * P[2]) % _p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % _p == 0


def _recover_x(y, sign):
    if y >= _p:
        return None
    x2 = (y * y - 1) * pow(_d * y * y + 1, _p - 2, _p)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_p + 3) // 8, _p)
    if (x * x - x2) % _p != 0:
        x = x * _sqrt_m1 % _p
    if (x * x - x2) % _p != 0:
        return None
    if (x & 1) != sign:
        x = _p - x
    return x


_gy = 4 * pow(5, _p - 2, _p) % _p
_gx = _recover_x(_gy, 0)
_G = (_gx, _gy, 1, _gx * _gy % _p)


def _compress(P):
    zinv = pow(P[2], _p - 2, _p)
    x, y = P[0] * zinv % _p, P[1] * zinv % _p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _p)


def _expand(secret):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _hq(b):
    return int.from_bytes(hashlib.sha512(b).digest(), "little") % _q


def public_key(secret: bytes) -> bytes:
    return _compress(_mul(_expand(secret)[0], _G))


def sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(secret)
    A = _compress(_mul(a, _G))
    r = _hq(prefix + msg)
    Rs = _compress(_mul(r, _G))
    s = (r + _hq(Rs + A + msg) * a) % _q
    return Rs + int.to_bytes(s, 32, "little")


def verify(public: bytes, msg: bytes, sig: bytes) -> bool:
    if len(public) != 32 or len(sig) != 64:
        return False
    A, R = _decompress(public), _decompress(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _q:
        return False
    return _equal(_mul(s, _G), _add(R, _mul(_hq(sig[:32] + public + msg), A)))


# --------------------------------------------------------------------------------------
# Update bundles
# --------------------------------------------------------------------------------------
SAFE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 _.\-]*(/[A-Za-z0-9_][A-Za-z0-9 _.\-]*)*$")


def make_bundle(secret: bytes, build: int, version: str, files: dict[str, bytes]) -> bytes:
    payload = json.dumps({"build": build, "version": version,
                          "files": {k: base64.b64encode(v).decode() for k, v in files.items()}}).encode()
    packed = zlib.compress(payload, 9)
    return json.dumps({"payload": base64.b64encode(packed).decode(), "sig": sign(secret, packed).hex()}).encode()


def open_bundle(message: bytes, pubkey_hex: str) -> dict | None:
    """Returns {"build", "version", "files": {path: bytes}} if the signature is valid, else None."""
    try:
        m = json.loads(message)
        packed = base64.b64decode(m["payload"])
        if not verify(bytes.fromhex(pubkey_hex), packed, bytes.fromhex(m["sig"])):
            return None
        data = json.loads(zlib.decompress(packed))
        files = {}
        for path, b64 in data["files"].items():
            if not SAFE_PATH.match(path) or ".." in path.split("/"):
                return None
            files[path] = base64.b64decode(b64)
        return {"build": int(data["build"]), "version": str(data["version"]), "files": files}
    except Exception:
        return None


def local_build(app_dir: Path = APP_DIR) -> int:
    try:
        return int(json.loads((app_dir / "version.json").read_text())["build"])
    except Exception:
        return 0


def install_bundle(bundle: dict, app_dir: Path = APP_DIR) -> list[str]:
    """Writes the bundle's files. The running .bat can't be overwritten while it runs, so a new
    one is written next to it and swapped in by the .bat when this launcher exits."""
    changed = []
    for path, content in bundle["files"].items():
        target = app_dir / path
        if path == "Start Chest Tracker.bat":
            target = app_dir / "Start Chest Tracker.bat.new"
        try:
            if (app_dir / path).read_bytes() == content:
                continue
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, target)
        changed.append(path)
    return changed


def fetch_from_relay(topic: str, wait: float = 6.0) -> bytes | None:
    """Reads the retained message on a topic from the first relay that answers."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return None
    for host, port in BROKERS:
        got: list[bytes] = []
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ct-upd-" + secrets.token_hex(4))
            c.on_connect = lambda cl, u, f, rc, p=None: cl.subscribe(topic, qos=1)
            c.on_message = lambda cl, u, msg: got.append(msg.payload)
            c.connect(host, port, keepalive=20)
            end = time.monotonic() + wait
            while not got and time.monotonic() < end:
                c.loop(timeout=0.2)
            c.disconnect()
        except Exception:
            continue
        if got:
            return got[0]
    return None


def publish_to_relay(topic: str, payload: bytes, retain: bool = True) -> list[str]:
    """Publishes to every relay that answers. Returns the hosts that confirmed."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return []
    ok = []
    for host, port in BROKERS:
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ct-pub-" + secrets.token_hex(4))
            c.connect(host, port, keepalive=20)
            info = c.publish(topic, payload, qos=1, retain=retain)
            end = time.monotonic() + 8
            while not info.is_published() and time.monotonic() < end:
                c.loop(timeout=0.2)
            if info.is_published():
                ok.append(host)
            c.disconnect()
        except Exception:
            continue
    return ok


GITHUB_ASSET = "update.json"   # the signed bundle attached to each GitHub release (tools/make_release.py)
MAX_BUNDLE = 64 * 1024 * 1024


def fetch_from_github(repo: str, timeout: float = 15) -> bytes | None:
    """The signed bundle attached to the repository's latest release, or None."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
        return None
    head = {"User-Agent": "ChestTracker-updater", "Accept": "application/vnd.github+json"}
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=head)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            release = json.load(r)
        asset = next((a for a in release.get("assets") or [] if a.get("name") == GITHUB_ASSET), None)
        url = (asset or {}).get("browser_download_url") or ""
        if not url.startswith("https://github.com/"):
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "ChestTracker-updater"})
        with urllib.request.urlopen(req, timeout=timeout * 4) as r:
            body = r.read(MAX_BUNDLE + 1)
        return body if len(body) <= MAX_BUNDLE else None
    except Exception:
        return None


def read_config() -> dict:
    try:
        cfg = json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def update(pubkey_hex: str = PUBLISHER_KEY, app_dir: Path = APP_DIR) -> bool:
    """Installs a newer signed version if there is one. Returns True if anything was installed."""
    if pubkey_hex == "PUBLISHER_KEY_PLACEHOLDER":
        return False
    if read_config().get("offline"):
        say("  Offline mode: not checking for updates.")
        return False
    ed = edition.load(app_dir)
    if ed["updates"] == "relay":
        say("  Checking for updates...")
        msg = fetch_from_relay(f"chesttracker/u1/{channel(pubkey_hex)}/latest")
    elif ed["updates"] == "github" and ed["github_repo"]:
        say("  Checking for updates...")
        msg = fetch_from_github(ed["github_repo"])
    else:
        return False  # (no update source set)
    if msg is None:
        say("  Couldn't reach the update server; using this version.")
        return False
    bundle = open_bundle(msg, pubkey_hex)
    if bundle is None:
        say("  Ignored an update that wasn't properly signed.")
        return False
    if bundle["build"] <= local_build(app_dir):
        say(f"  Up to date (version {bundle['version']}).")
        return False
    changed = install_bundle(bundle, app_dir)
    say(f"  Updated to version {bundle['version']} ({len(changed)} files).")
    return True


# --------------------------------------------------------------------------------------
# Health checks, replay reader, starting the app
# --------------------------------------------------------------------------------------
NEEDED = "import tkinter, paho.mqtt, rapidocr_onnxruntime, mss, PIL, numpy"


def run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def check_packages() -> bool:
    r = run([sys.executable, "-c", NEEDED])
    if r.returncode == 0:
        return True
    say("  Installing missing parts...")
    p = run([sys.executable, "-m", "pip", "install", "--ignore-requires-python", "-r", str(APP_DIR / "requirements.txt")])
    if p.returncode != 0:
        say(p.stdout[-1500:] + p.stderr[-1500:])
    r = run([sys.executable, "-c", NEEDED])
    if r.returncode != 0:
        say("  Something needed is still missing:")
        say(r.stderr.strip()[-1500:])
        return False
    return True


def reader_source_hash() -> str:
    h = hashlib.sha256()
    for f in sorted((APP_DIR / "reader_src").glob("*")):
        if f.is_file():
            h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()[:16]


def run_setup_reader():
    """Runs setup_reader.bat (installs .NET if needed, builds the reader). Its output is shown in this
    window as it happens and kept for the debug report."""
    p = subprocess.Popen(["cmd", "/c", str(APP_DIR / "setup_reader.bat")], cwd=str(APP_DIR), stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    for line in p.stdout:
        if line.strip() and "Press any key" not in line:
            say("    " + line.strip()[:300])
    return p.wait()


def ensure_reader():
    exe, stamp = APP_DIR / "reader" / "ChestReplayReader.exe", APP_DIR / "reader" / "source.hash"
    want = reader_source_hash()
    if exe.exists() and stamp.exists() and stamp.read_text().strip() == want:
        return
    has_sdk = False
    # .NET in Program Files, or in this user's folder (where setup_reader.bat installs it without admin rights)
    for dotnet in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "dotnet" / "dotnet.exe",
                   Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "dotnet" / "dotnet.exe"):
        if has_sdk or not dotnet.is_file():  # on a PC without .NET there's nothing to ask: go straight to installing it
            continue
        try:
            sdk = run([str(dotnet), "--list-sdks"])
            has_sdk = sdk.returncode == 0 and bool(re.search(r"^10\.", sdk.stdout, re.M))
        except OSError:
            pass
    tried = DATA_DIR / "reader_setup_tried"
    if not has_sdk and exe.exists():
        return  # an older reader works for the basics; it's rebuilt once .NET is there
    # installing .NET is tried once per version of Chest Tracker (not every start)
    attempt = f"build {local_build()}"
    if not has_sdk and tried.exists() and tried.read_text().strip() == attempt:
        say("  Broken chests are off: the replay reader isn't set up (double-click setup_reader.bat).")
        return
    tried.write_text(attempt)
    if exe.exists():
        exe.unlink()  # the reader's source changed: rebuild it
    say("  Setting up the replay reader...")
    run_setup_reader()
    if exe.exists():
        stamp.write_text(want)


def start_app() -> dict:
    """Starts the tracker (no console) and watches it for a while. Returns what happened."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    err_path = DATA_DIR / "app_errors.log"
    err = open(err_path, "w", encoding="utf-8")
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen([str(pythonw), str(APP_DIR / "chest_tracker.py")], cwd=str(APP_DIR),
                            stdout=subprocess.DEVNULL, stderr=err, creationflags=flags,
                            env=dict(os.environ, CHEST_LAUNCHER="1"))
    end = time.monotonic() + 15
    while time.monotonic() < end and proc.poll() is None:
        time.sleep(0.25)
    err.close()
    code = proc.poll()
    # what the app wrote while starting (its log lines, and the error if it crashed)
    output = err_path.read_text(encoding="utf-8", errors="replace").strip() if err_path.exists() else ""
    return {"running": code is None, "exit_code": code, "output": output[-4000:]}


# --------------------------------------------------------------------------------------
# Debug report
# --------------------------------------------------------------------------------------
def redact(text: str) -> str:
    """Blank out the Windows user name and personal folder paths."""
    for var in ("USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        v = os.environ.get(var)
        if v:
            text = text.replace(v, "%" + var + "%").replace(v.replace("\\", "\\\\"), "%" + var + "%")
    user = os.environ.get("USERNAME")
    if user and len(user) > 2:
        text = re.sub(re.escape(user), "<user>", text, flags=re.I)
    return text


def machine_id() -> str:
    f = DATA_DIR / "machine_id"
    try:
        return f.read_text().strip()
    except OSError:
        mid = secrets.token_hex(6)
        f.write_text(mid)
        return mid


def tail(path: Path, n: int) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def replay_summary() -> dict:
    """How many replays Fortnite has saved on this PC, and what the tracker did with them."""
    demos = Path(os.environ.get("LOCALAPPDATA", "")) / "FortniteGame" / "Saved" / "Demos"
    out: dict = {"demos_folder": demos.exists()}
    try:
        files = sorted(demos.glob("*.replay"), key=lambda p: p.stat().st_mtime)
        out["files"] = len(files)
        if files:
            out["oldest"] = time.strftime("%Y-%m-%d", time.localtime(files[0].stat().st_mtime))
            out["newest"] = time.strftime("%Y-%m-%d", time.localtime(files[-1].stat().st_mtime))
    except OSError:
        pass
    try:
        db = json.loads((DATA_DIR / "replays.json").read_text(encoding="utf-8"))
        statuses: dict = {}
        for e in db.get("files", {}).values():
            statuses[e.get("status", "?")] = statuses.get(e.get("status", "?"), 0) + 1
        out["tracker"] = statuses
    except Exception:
        out["tracker"] = "none read yet"
    try:
        st = json.loads((DATA_DIR / "stats.json").read_text(encoding="utf-8"))
        out["total"], out["imported_chests"] = st.get("total"), st.get("imported")
    except Exception:
        pass
    return out


def reports_on(cfg: dict, ed: dict | None = None) -> bool:
    """Problem reports: never offline; else the user's choice (Settings > Privacy), else the build's default.
    Only sent when the build has a key to seal them to."""
    ed = ed or edition.load()
    if cfg.get("offline") or not ed.get("report_key"):
        return False
    choice = cfg.get("send_reports")
    return choice if isinstance(choice, bool) else bool(ed.get("reports"))


def send_report(result: dict, pubkey_hex: str = PUBLISHER_KEY):
    cfg = read_config()
    ed = edition.load()
    if not reports_on(cfg, ed) or pubkey_hex == "PUBLISHER_KEY_PLACEHOLDER":
        return
    try:
        version = re.search(r'APP_VERSION = "([^"]+)"', (APP_DIR / "chest_tracker.py").read_text(encoding="utf-8")).group(1)
    except Exception:
        version = "?"
    report = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "name": cfg.get("name", ""), "app_version": version,
        "build": local_build(), "windows": platform.platform(), "python": sys.version.split()[0],
        "app_folder": str(APP_DIR), "has_reader": (APP_DIR / "reader" / "ChestReplayReader.exe").exists(),
        "team_code_set": bool(cfg.get("team_code")), "replays": replay_summary(),
        "result": result, "launcher_output": "\n".join(lines[-60:]),
        "tracker_log": tail(DATA_DIR / "tracker.log", 80),
    }
    try:
        payload = teamcrypt.seal_to(bytes.fromhex(ed["report_key"]), redact(json.dumps(report, indent=1)).encode())
    except ValueError:
        return
    publish_to_relay(f"chesttracker/u1/{channel(pubkey_hex)}/diag/{machine_id()}", payload)


# --------------------------------------------------------------------------------------
def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    say("")
    say("  Chest Tracker")
    say("  -------------")
    if "--updated" not in sys.argv:
        try:
            if update() and (APP_DIR / "launcher.py").exists():
                # the launcher itself may have changed: continue with the new one
                return subprocess.call([sys.executable, str(APP_DIR / "launcher.py"), "--updated"])
        except Exception as e:
            say(f"  Update check failed ({e}); using this version.")
    if not check_packages():
        send_report({"running": False, "stage": "packages"})
        say("")
        say("  Chest Tracker can't start because something didn't install. Check your internet connection,")
        say("  or delete %LOCALAPPDATA%\\ChestTracker\\venv and run this again.")
        return 1
    try:
        ensure_reader()
    except Exception as e:
        say(f"  Replay reader setup failed ({e}); live counting still works.")
    say("  Starting Chest Tracker...")
    result = start_app()
    if result["exit_code"] == ALREADY_RUNNING_EXIT:
        say("  Chest Tracker was already running (look for the chest button on the taskbar).")
        result["stage"] = "already running"
        send_report(result)
        return 0
    if not result["running"]:
        say("")
        say("  Chest Tracker stopped right after starting. Error details:")
        say("  " + (result["output"] or tail(DATA_DIR / "tracker.log", 15) or "(no details)").replace("\n", "\n  "))
        say("")
        if reports_on(read_config()):
            say("  A report with these details was sent so it can be fixed.")
        result["stage"] = "crashed at start"
        send_report(result)
        return 1
    say("  Chest Tracker is running.")
    say("  - The first time, a setup window asks for your name and team code.")
    say("  - The overlay is in the top-right corner of your main screen.")
    say("  - The chest button on the taskbar opens settings (right-click it > Close window to quit).")
    result["stage"] = "started"
    send_report(result)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:  # never vanish without saying why
        import traceback

        say("  The launcher hit an error:")
        say(traceback.format_exc())
        try:
            send_report({"running": False, "stage": "launcher error", "errors": traceback.format_exc()})
        except Exception:
            pass
        code = 1
    try:
        LAUNCHER_LOG.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass
    sys.exit(code)  # on an error the .bat keeps the window open
